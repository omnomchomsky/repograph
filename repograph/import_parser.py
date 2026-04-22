from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from . import repo_graph
from .file_dependencies import FileDependency

@dataclass(frozen=True)
class RawImport:
    source_file: str
    module: str | None
    imported_names: tuple[str, ...]
    level: int
    is_from_import: bool


@dataclass(frozen=True)
class RepoDependencies:
    files: list[str]
    deps: list[FileDependency]


def walk_python_files(repo_root: str | Path) -> list[Path]:
    repo_root = Path(repo_root).resolve()
    files: list[Path] = []

    for py_file in repo_root.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        if any(part.startswith(".") for part in py_file.parts):
            continue
        files.append(py_file)

    return files


def extract_imports(file_path: str | Path, repo_root: str | Path) -> list[RawImport]:
    file_path = Path(file_path).resolve()
    repo_root = Path(repo_root).resolve()

    raw_imports: list[RawImport] = []

    try:
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return raw_imports

    source_rel = str(file_path.relative_to(repo_root).as_posix())

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                raw_imports.append(
                    RawImport(
                        source_file=source_rel,
                        module=alias.name,
                        imported_names=(),
                        level=0,
                        is_from_import=False,
                    )
                )

        elif isinstance(node, ast.ImportFrom):
            imported_names = tuple(alias.name for alias in node.names)

            raw_imports.append(
                RawImport(
                    source_file=source_rel,
                    module=node.module,
                    imported_names=imported_names,
                    level=node.level,
                    is_from_import=True,
                )
            )

    return raw_imports


def candidate_internal_paths(
    raw_import: RawImport,
    source_file: str,
) -> list[str]:
    """
    Generate plausible internal target paths.
    This is intentionally simple for v1.
    """
    candidates: list[str] = []

    source_path = Path(source_file)
    source_dir = source_path.parent

    if raw_import.level > 0:
        base_dir = source_dir
        for _ in range(raw_import.level - 1):
            base_dir = base_dir.parent.as_posix()

        if raw_import.module:
            candidates.append((base_dir / Path(raw_import.module.replace(".", "/")).with_suffix(".py")).as_posix())
            candidates.append((base_dir / raw_import.module.replace(".", "/") / "__init__.py").as_posix())

        for name in raw_import.imported_names:
            candidates.append((base_dir / f"{name}.py").as_posix())
            candidates.append((base_dir / name / "__init__.py").as_posix())

    elif raw_import.module:
        candidates.append(f"{raw_import.module.replace('.', '/')}.py")
        candidates.append(f"{raw_import.module.replace('.', '/')}/__init__.py")

    return candidates


def resolve_dependency(raw_import: RawImport, repo_files: set[str]) -> FileDependency:
    """
    Resolve to a concrete internal file if possible; otherwise keep as external.
    """
    candidates = candidate_internal_paths(raw_import, raw_import.source_file)

    for candidate in candidates:
        if candidate in repo_files:
            return FileDependency(source=raw_import.source_file, target=candidate)

    if raw_import.module:
        external_target = f"external:{raw_import.module}"
    elif raw_import.imported_names:
        external_target = f"external:{raw_import.imported_names[0]}"
    else:
        external_target = "external:unknown"

    return FileDependency(source=raw_import.source_file, target=external_target)



def build_dependency_graph(repo_root: str | Path) -> RepoDependencies:
    repo_root = Path(repo_root).resolve()
    python_files = walk_python_files(repo_root)

    rel_files = [str(path.relative_to(repo_root).as_posix()) for path in python_files]
    repo_files = set(rel_files)

    deps: list[FileDependency] = []

    for py_file in python_files:
        raw_imports = extract_imports(py_file, repo_root)
        for raw_import in raw_imports:
            deps.append(resolve_dependency(raw_import, repo_files))

    deduped_deps = sorted(set(deps), key=lambda d: (d.source, d.target))
    rel_files.sort()

    return RepoDependencies(files=rel_files, deps=deduped_deps)


def load_into_graph(
    graph: RepoGraph.RepoGraph,
    repo_deps: RepoDependencies,
    *,
    reset: bool = True,
) -> None:
    if reset:
        graph.reset()

    graph.create_file_nodes(repo_deps.files)
    graph.create_file_nodes(
        [dep.target for dep in repo_deps.deps if dep.target.startswith("external:")]
    )
    graph.create_import_relationships(repo_deps.deps)


def get_llm_ready_context(graph: RepoGraph.RepoGraph, target_file: str) -> str:
    direct = graph.get_direct_dependencies(target_file)
    transitive = graph.get_transitive_dependencies(target_file)
    reverse = graph.get_reverse_dependencies(target_file)

    return f"""Dependency context for {target_file}:

Direct dependencies:
{chr(10).join(f"- {d}" for d in direct) if direct else "- none"}

Transitive dependencies:
{chr(10).join(f"- {d}" for d in transitive) if transitive else "- none"}

Reverse dependencies:
{chr(10).join(f"- {d}" for d in reverse) if reverse else "- none"}
"""