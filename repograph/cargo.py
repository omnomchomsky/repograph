"""Import Cargo's resolved package graph without conflating packages and targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .model import Node, RustGraph


@dataclass
class CargoGraph:
    packages: dict[str, Node] = field(default_factory=dict)
    # SCIP carries a Cargo package name/version and a crate descriptor.
    crates: dict[tuple[str, str, str], list[Node]] = field(default_factory=dict)
    package_crates: dict[tuple[str, str], list[Node]] = field(default_factory=dict)


def relative_path(path: str, root: Path) -> str:
    return Path(path).resolve().relative_to(root.resolve()).as_posix()


def import_cargo(graph: RustGraph, metadata: dict[str, Any]) -> CargoGraph:
    if metadata.get("version") != 1:
        raise ValueError("Cargo metadata must use --format-version 1")
    if not metadata.get("packages") or not metadata.get("workspace_members"):
        raise ValueError("Cargo metadata contains no workspace packages")
    result = CargoGraph()
    workspace = set(metadata["workspace_members"])
    repo = graph.node("Repository", graph.repo_id, name=graph.root.name, path=str(graph.root))
    for package in metadata["packages"]:
        pid = package["id"]
        member = pid in workspace
        manifest = package["manifest_path"]
        try:
            manifest = relative_path(manifest, graph.root)
        except ValueError:
            pass
        # Cargo IDs are opaque; retain them rather than parsing path/registry syntax.
        pnode = graph.node("Package", pid, name=package["name"], version=package["version"],
                           cargo_id=pid, workspace_member=member, manifest_path=manifest,
                           source=package.get("source"), edition=package.get("edition"))
        result.packages[pid] = pnode
        if member:
            graph.edge(repo, "HAS_PACKAGE", pnode)
        for target in package.get("targets", []):
            root = target["src_path"]
            try:
                root = relative_path(root, graph.root)
            except ValueError:
                pass
            kinds = target["kind"]
            name = target["name"].replace("-", "_")
            identity = f"{pid}|{','.join(kinds)}|{target['name']}|{root}"
            crate = graph.node("Crate", identity, name=target["name"], crate_name=name,
                               package_id=pnode.id, target_kinds=kinds,
                               crate_types=target.get("crate_types", []), root_path=root,
                               required_features=target.get("required-features", []),
                               edition=target.get("edition", package.get("edition")),
                               workspace_member=member)
            graph.edge(pnode, "HAS_TARGET", crate)
            result.crates.setdefault((package["name"], package["version"], name), []).append(crate)
            result.package_crates.setdefault((package["name"], package["version"]), []).append(crate)
            if member:
                graph.edge(crate, "ROOT_FILE", graph.file(root))

    resolve = metadata.get("resolve")
    if resolve is None:
        graph.warnings.append("Cargo resolve is absent: resolved DEPENDS_ON edges are unavailable (do not use --no-deps).")
    else:
        for node in resolve.get("nodes", []):
            source = result.packages.get(node["id"])
            if source is None:
                raise ValueError(f"Cargo resolve refers to missing package {node['id']}")
            source.properties["enabled_features"] = node.get("features", [])
            for dep in node.get("deps", []):
                target = result.packages.get(dep["pkg"])
                if target is None:
                    raise ValueError(f"Cargo resolve refers to missing package {dep['pkg']}")
                for kind in dep.get("dep_kinds", []) or [{}]:
                    graph.edge(source, "DEPENDS_ON", target, alias=dep["name"],
                               dependency_kind=kind.get("kind") or "normal",
                               target_condition=kind.get("target") or "")
    return result
