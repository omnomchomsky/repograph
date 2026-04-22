import argparse
from pathlib import Path

from .config import load_config
from repograph.repo_graph import RepoGraph
from repograph.import_parser import (
    build_dependency_graph,
    load_into_graph,
    get_llm_ready_context,
)

def main() -> None:
    parser = argparse.ArgumentParser(prog="repograph")
    subparsers = parser.add_subparsers(dest="command", required=True)

    ingest = subparsers.add_parser("ingest")
    ingest.add_argument("repo")

    context = subparsers.add_parser("context")
    context.add_argument("repo")
    context.add_argument("target_file")

    args = parser.parse_args()

    config = load_config()
    graph = RepoGraph(config.uri, config.username, config.password)

    try:
        repo_path = Path(args.repo)

        if args.command == "ingest":
            repo_deps = build_dependency_graph(repo_path)
            load_into_graph(graph, repo_deps)
            print(f"Ingested {len(repo_deps.files)} files, {len(repo_deps.deps)} dependencies")

        elif args.command == "context":
            repo_deps = build_dependency_graph(repo_path)
            load_into_graph(graph, repo_deps)
            print(get_llm_ready_context(graph, args.target_file))

    finally:
        if graph is not None:
            graph.close()