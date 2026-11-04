"""Acquire Cargo/SCIP inputs and assemble a scan before any database write."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlparse
from uuid import uuid4

from .cargo import import_cargo
from .model import RustGraph, repository_id
from .scip import ScipImporter, load_index


@dataclass(frozen=True)
class ScanOptions:
    cargo: str = "cargo"
    analyzer: str = "rust-analyzer"
    target: str | None = None
    features: tuple[str, ...] = ()
    all_features: bool = False
    no_default_features: bool = False
    offline: bool = False
    cargo_only: bool = False
    metadata_path: Path | None = None
    scip_path: Path | None = None
    artifacts_dir: Path | None = None
    repo_id: str | None = None


def run(command: list[str], root: Path, log_path: Path | None = None) -> str:
    print(f"Running {command[0]} {' '.join(command[1:])}", file=sys.stderr)
    try:
        completed = subprocess.run(command, cwd=root, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE if log_path else None, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError(f"Executable not found: {command[0]}") from exc
    if log_path:
        log_path.write_text(completed.stderr or "", encoding="utf-8")
    if completed.returncode:
        if log_path:
            print("\n".join((completed.stderr or "").splitlines()[-30:]), file=sys.stderr)
        raise RuntimeError(f"{command[0]} failed (exit {completed.returncode}); see diagnostics above. "
                           "You can supply --cargo-metadata and --scip from an existing export.")
    return completed.stdout


def git_info(root: Path) -> dict:
    try:
        revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                                  text=True, check=True).stdout.strip()
        status = subprocess.run(["git", "status", "--porcelain"], cwd=root, capture_output=True,
                                text=True, check=True).stdout
        return dict(revision=revision, dirty=bool(status))
    except (FileNotFoundError, subprocess.CalledProcessError):
        return dict(revision="unknown", dirty=True)


def build_rust_graph(root: Path, options: ScanOptions = ScanOptions()) -> RustGraph:
    root = root.expanduser().resolve()
    if not root.is_dir() or not (root / "Cargo.toml").is_file():
        raise ValueError(f"Expected a Cargo workspace directory with Cargo.toml: {root}")
    if options.cargo_only and options.scip_path:
        raise ValueError("--cargo-only cannot be combined with --scip")
    started_at = datetime.now(timezone.utc).isoformat()
    revision = git_info(root)
    with tempfile.TemporaryDirectory(prefix="repograph-") as temporary:
        artifacts = options.artifacts_dir.expanduser().resolve() if options.artifacts_dir else Path(temporary)
        artifacts.mkdir(parents=True, exist_ok=True)
        command = [options.cargo, "metadata", "--format-version", "1", "--locked"]
        if options.target:
            command += ["--filter-platform", options.target]
        if options.features:
            command += ["--features", ",".join(options.features)]
        if options.all_features:
            command += ["--all-features"]
        if options.no_default_features:
            command += ["--no-default-features"]
        if options.offline:
            command += ["--offline"]
        raw_metadata = options.metadata_path.read_text(encoding="utf-8") if options.metadata_path else run(command, root)
        if not options.metadata_path:
            (artifacts / "cargo-metadata.json").write_text(raw_metadata, encoding="utf-8")
        metadata = json.loads(raw_metadata)
        workspace = Path(metadata["workspace_root"]).resolve()
        if workspace != root:
            raise ValueError(f"Cargo metadata belongs to workspace {workspace}; scan that workspace root instead of {root}")
        graph = RustGraph(repository_id(root, options.repo_id), root)
        cargo = import_cargo(graph, metadata)
        config = {"cargo": {
            "features": "all" if options.all_features else list(options.features),
            "noDefaultFeatures": options.no_default_features,
            "extraArgs": ["--locked"] + (["--offline"] if options.offline else []),
        }}
        if options.target:
            config["cargo"]["target"] = options.target
        graph.scan = dict(
            id=str(uuid4()), started_at=started_at, **revision,
            target=options.target or "workspace default (host if unset; Cargo resolve includes all platforms)",
            features=list(options.features), all_features=options.all_features,
            no_default_features=options.no_default_features, locked=True,
            cargo_metadata_sha256=hashlib.sha256(raw_metadata.encode()).hexdigest(),
            configuration_json=json.dumps(config, sort_keys=True),
            cargo_metadata_source="provided" if options.metadata_path else "generated",
            analyzer_version="not run", index_source="none", semantic_enrichment=False,
        )
        if not options.cargo_only:
            index_path = options.scip_path
            if index_path is None:
                index_path = artifacts / "index.scip"
                config_path = artifacts / "rust-analyzer.json"
                log_path = artifacts / "rust-analyzer.log"
                config_path.write_text(json.dumps(config), encoding="utf-8")
                index_path.unlink(missing_ok=True)
                run([options.analyzer, "scip", ".", "--output", str(index_path),
                     "--config-path", str(config_path), "--exclude-vendored-libraries"], root, log_path)
                diagnostics = log_path.read_text(encoding="utf-8")
                errors = sum(" ERROR " in line and "ConfigErrors([])" not in line for line in diagnostics.splitlines())
                warnings = sum("warning:" in line or " WARN " in line for line in diagnostics.splitlines())
                graph.scan.update(analyzer_error_count=errors, analyzer_warning_count=warnings)
                if errors or warnings:
                    graph.warnings.append(f"Analyzer diagnostics: {errors} error lines, {warnings} warning lines; "
                                          "the export may be incomplete. Use --artifacts-dir to retain rust-analyzer.log.")
            index = load_index(index_path)
            uri = urlparse(index.metadata.project_root)
            if uri.scheme != "file" or uri.netloc not in {"", "localhost"}:
                raise ValueError("SCIP project_root must be a local file:// URI")
            index_root = Path(unquote(uri.path)).resolve()
            if index_root != root:
                raise ValueError(f"SCIP index belongs to {index_root}, not {root}")
            graph.scan.update(analyzer_version=index.metadata.tool_info.version or "unknown",
                              analyzer_name=index.metadata.tool_info.name or "unknown",
                              analyzer_arguments=list(index.metadata.tool_info.arguments),
                              scip_sha256=hashlib.sha256(index_path.read_bytes()).hexdigest(),
                              index_source="provided" if options.scip_path else "generated")
            if options.scip_path or options.metadata_path:
                graph.warnings.append("Provided exports: revision and configuration describe this import; "
                                      "their match to the original exports is not verified.")
            ScipImporter(graph, cargo).import_index(index)
            indexed = {n.id for n in graph.nodes.values()
                       if "Symbol" in n.labels and not n.properties["external"]}
            owned = any(e.kind == "CONTAINS" and e.target in indexed
                        and "Crate" in graph.nodes[e.source].labels
                        and graph.nodes[e.source].properties.get("workspace_member")
                        for e in graph.edges.values())
            if indexed and not owned:
                raise ValueError(
                    f"SCIP from {graph.scan['analyzer_version']} does not identify Cargo target ownership. "
                    "Use a recent standalone rust-analyzer with --rust-analyzer /path/to/binary "
                    "(validated: 0.3.3105), or use --cargo-only. The database was not changed.")
        else:
            graph.warnings.append("Cargo-only scan: symbol definitions and references are unavailable.")
        graph.scan["completed_at"] = datetime.now(timezone.utc).isoformat()
        if options.artifacts_dir:
            graph.scan["artifacts_dir"] = str(artifacts)
            (artifacts / "scan.json").write_text(json.dumps(graph.summary(), indent=2) + "\n", encoding="utf-8")
        return graph
