import argparse
import json
from pathlib import Path
import sys

from google.protobuf.message import DecodeError
from google.protobuf.json_format import ParseError
from neo4j.exceptions import Neo4jError, DriverError

from .config import load_config
from .model import repository_id
from .repo_graph import RepoGraph
from .scan import ScanOptions, build_rust_graph


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def print_context(context: dict) -> None:
    symbol = context["symbol"]
    print(f"{symbol['name']} ({symbol['kind']})\nID: {symbol['id']}\nSCIP: {symbol['scip_symbol']}")
    for owner in context["ownership"]:
        if owner["crate"]:
            print(f"Package: {owner['package']['name']}  Crate: {owner['crate']['name']}")
            print("Modules: " + "::".join(m["name"] for m in owner["modules"] or []))
    for heading, key in [("Definitions", "definitions"), ("References", "references")]:
        print(f"\n{heading}:")
        for item in context[key]:
            loc = item["location"]
            print(f"  {item['file']}:{loc['start_line'] + 1}:{loc['start_character'] + 1}")
        if not context[key]:
            print("  none indexed")
    if context["references_truncated"]:
        print(f"Showing {len(context['references'])} of {context['reference_count']} references; increase --limit.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="repograph", description="Rust code graphs from Cargo and SCIP")
    commands = parser.add_subparsers(dest="command", required=True)
    ingest = commands.add_parser("ingest", help="scan a Cargo workspace and replace its current graph")
    ingest.add_argument("repo", type=Path)
    ingest.add_argument("--scip", type=Path, dest="scip_path", help="existing binary SCIP or Protobuf JSON index")
    ingest.add_argument("--cargo-metadata", type=Path, dest="metadata_path", help="existing Cargo metadata JSON")
    ingest.add_argument("--cargo-only", action="store_true", help="import only packages and crate targets")
    ingest.add_argument("--rust-analyzer", dest="analyzer", default="rust-analyzer")
    ingest.add_argument("--cargo", default="cargo")
    ingest.add_argument("--target", help="Cargo target triple, also passed to rust-analyzer")
    ingest.add_argument("--features", action="append", default=[], help="comma-separated Cargo features; repeatable")
    ingest.add_argument("--all-features", action="store_true")
    ingest.add_argument("--no-default-features", action="store_true")
    ingest.add_argument("--offline", action="store_true", help="pass --offline to Cargo and rust-analyzer's Cargo")
    ingest.add_argument("--artifacts-dir", type=Path, help="retain generated metadata, SCIP, config and scan manifest")
    ingest.add_argument("--dry-run", action="store_true", help="build and validate without connecting to Neo4j")
    ingest.add_argument("--export", type=Path, help="write the assembled graph as JSON")
    context = commands.add_parser("context", help="show a previously indexed symbol's ownership, definitions and references")
    context.add_argument("repo", type=Path)
    context.add_argument("symbol", help="exact name, full SCIP symbol, or repository-scoped symbol ID")
    symbols = commands.add_parser("symbols", help="search symbols from the last successful import")
    symbols.add_argument("repo", type=Path)
    symbols.add_argument("search")
    view = commands.add_parser("view", help="query package or module/type structure")
    view.add_argument("repo", type=Path)
    view.add_argument("view", choices=["packages", "structure"])
    for command in (ingest, context, symbols, view):
        command.add_argument("--repo-id", help="explicit repository identity (defaults to canonical path hash)")
        command.add_argument("--env-file", type=Path, help="Neo4j settings; defaults to repograph/env, environment overrides")
    for command in (context, symbols, view):
        command.add_argument("--limit", type=positive_int, default=100)
        command.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args(argv)
    graph = None
    try:
        if args.command == "ingest":
            features = tuple(f for chunk in args.features for f in chunk.replace(",", " ").split())
            options = ScanOptions(**{k: getattr(args, k) for k in ScanOptions.__dataclass_fields__ if k != "features"},
                                  features=features)
            snapshot = build_rust_graph(args.repo, options)
            if args.export:
                args.export.write_text(json.dumps(snapshot.as_dict(), indent=2) + "\n", encoding="utf-8")
            if not args.dry_run:
                config = load_config(args.env_file)
                graph = RepoGraph(config.uri, config.username, config.password)
                graph.load(snapshot)
            print(json.dumps({"stored": not args.dry_run, **snapshot.summary()}, indent=2))
        else:
            config = load_config(args.env_file)
            graph = RepoGraph(config.uri, config.username, config.password)
            repo_id = repository_id(args.repo.expanduser().resolve(), args.repo_id)
            if args.command == "context":
                result = graph.context(repo_id, args.symbol, args.limit)
                if not args.json:
                    print_context(result)
                    return
            elif args.command == "symbols":
                result = graph.symbols(repo_id, args.search, args.limit)
                if not args.json:
                    for symbol in result:
                        print(f"{symbol['id']}  {symbol['kind']}  {symbol['name']}  {symbol['scip_symbol']}")
                    return
            else:
                result = graph.view(repo_id, args.view, args.limit)
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, RuntimeError, DecodeError, ParseError, Neo4jError, DriverError) as exc:
        print(f"repograph: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
    finally:
        if graph is not None:
            graph.close()
