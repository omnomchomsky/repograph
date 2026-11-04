# RepoGraph

RepoGraph indexes Rust workspaces into Neo4j using Cargo metadata and
rust-analyzer's SCIP export. The Python CLI remains the entry point. The former
Python import extractor has been replaced.

## Setup

Requires Python 3.12+, Cargo, rust-analyzer with the `scip` subcommand, and Neo4j
5+ (validated with Neo4j 2026.10.1). Install dependencies with `uv sync` and, if
needed, the analyzer with `rustup component add rust-analyzer rust-src` for the
workspace's toolchain. `--rust-analyzer /path/to/binary` selects another binary.
The symbol importer requires an exporter that includes Cargo target descriptors;
the standalone 0.3.3105 release was validated. The toolchain's 1.99.1 exporter
omits those descriptors and is rejected before storage, because it cannot
establish the owning crate. Updating a rustup toolchain component does not
necessarily install the newest standalone analyzer.

Neo4j settings load from `repograph/env` automatically; environment variables
override file values. Use `repograph/env.example` as a template, or:

```sh
export REPOGRAPH_NEO4J_URI=neo4j://127.0.0.1:7687
export REPOGRAPH_NEO4J_USERNAME=neo4j
export REPOGRAPH_NEO4J_PASSWORD='your-password'
```

Every database command accepts `--env-file /path/to/env`. Env files are parsed
as data, never executed, and ignored by Git for new untracked files.

## Scan and inspect

```sh
# Generate Cargo metadata and binary SCIP, validate, then store in Neo4j.
uv run repograph ingest /path/to/workspace --artifacts-dir artifacts/my-project

# Build/export without Neo4j or credentials.
uv run repograph ingest /path/to/workspace --dry-run --export graph.json

# Package graph only; skips rust-analyzer.
uv run repograph ingest /path/to/workspace --cargo-only

# Supply exports from another run on the same workspace path.
uv run repograph ingest /path/to/workspace \
  --cargo-metadata /path/to/cargo-metadata.json --scip /path/to/index.scip

# Query the latest stored scan; these commands never rescan or write.
uv run repograph symbols /path/to/workspace Config
uv run repograph context /path/to/workspace 'full-SCIP-symbol-or-symbol-ID'
uv run repograph view /path/to/workspace packages
uv run repograph view /path/to/workspace structure
```

`python -m repograph` and `python main.py` also work in the installed environment.
`context` accepts an exact display name when unique; otherwise it reports the
matching IDs. `symbols`, `context`, and `view` accept `--limit` (default 100).
`--json` exposes all stored source coordinates and properties. Reference counts
and truncation are explicit. Text output uses one-based coordinates; stored/JSON
ranges are zero-based, half-open, with the SCIP document's position encoding.

Scans use `cargo metadata --format-version 1 --locked` and
`rust-analyzer scip . --output ... --config-path ... --exclude-vendored-libraries`.
`--features a,b`, `--all-features`, `--no-default-features`, `--target TRIPLE`, and
`--offline` are passed consistently to Cargo and the generated analyzer config.
Without `--target`, Cargo resolves dependencies for all platforms while indexing
uses the workspace's default target. Project Cargo configuration still applies.
Cargo/analyzer errors abort before any database write. A successful analyzer can
still report diagnostics: counts are recorded and the scan warns that its export
may be incomplete. `--artifacts-dir` retains generated inputs, config,
`rust-analyzer.log`, and `scan.json`; otherwise intermediate files are temporary.

Binary SCIP is decoded directly with vendored Protobuf bindings. Protobuf JSON
exports are accepted with a `.json` extension; no SCIP CLI is required. Supplied
exports must have the same workspace root. Their original revision/configuration
cannot be verified, so such imports are explicitly marked in scan warnings.

## Graph model

| Edge | Meaning |
| --- | --- |
| `Repository → HAS_PACKAGE → Package` | Workspace membership |
| `Package → DEPENDS_ON → Package` | Resolved Cargo dependency, including alias, kind and platform condition |
| `Package → HAS_TARGET → Crate` | Distinct library, binary, test, example, benchmark or build-script target |
| `Crate → ROOT_FILE → File` | Target source root |
| `Crate/Module/Symbol → CONTAINS → Module/Symbol` | SCIP descriptor hierarchy or explicit enclosing symbol |
| `Symbol → DEFINED_IN → File` | Definition occurrence, with source and enclosing ranges |
| `File → REFERENCES → Symbol` | Resolved occurrence, with source range and role bitmask |

All indexed constructs have `Symbol` plus appropriate labels such as `Module`,
`Struct`, `Enum`, `Trait`, `Function`, or `Method`. Static/trait methods also have
`Method`. External references retain target symbol nodes without inventing
definition locations. All resolved Cargo packages/targets are represented;
`workspace_member` distinguishes the project's own packages and targets.

Packages and crates are separate nodes. Modules and files are separate nodes,
including inline and explicitly pathed modules. Containment comes from SCIP's
descriptor grammar and `enclosing_symbol`, never from directory names. Missing
hierarchy ancestors are marked `inferred`; their containment edges record
provenance. Crate descriptors such as `lib/`, `bin/name/`, and `test/name/` select
the matching Cargo target. Ambiguous ownership is retained and reported. SCIP
cannot disambiguate every exporter collision; multiple definition locations are
kept rather than silently choosing one.

Entity IDs are scoped by repository; full SCIP identities distinguish equal
display names, and local symbols additionally include their document path.
The repository ID defaults to a hash of its canonical workspace path. Pass the
same `--repo-id` to ingest and query commands for an explicit identity.

An import atomically replaces only that repository's current `RepoGraphNode`
snapshot. Other repositories and legacy Python graph nodes are preserved. Stable
entity IDs survive repeat scans; relationships reflect the current scan. Scan
metadata is retained separately (revision/dirty status, configuration, analyzer
version, input hashes, timestamps and warnings). Old graph snapshots are not
retained. Queries use only the latest successful snapshot.

`CALLS`, `FOR_TYPE`, and `IMPLEMENTS` are reserved for semantic enrichment and
are **not emitted** in this milestone. A function reference, including a callback,
is not proof of a call. Inferred `Impl` containers do not establish type or trait
implementation semantics. LSP call hierarchy and implementation analysis are
the next pass.

## Neo4j visualization

[docs/neo4j-views.cypher](docs/neo4j-views.cypher) contains Browser queries for a
package overview, module/type structure, and a selected symbol's neighborhood.
Style by node labels and use `name` as the caption. Each occurrence remains a
separate relationship so source locations can be inspected.

For a reproducible example, index the bundled Rust fixture with an explicit
repository ID that also matches the Browser query examples:

```sh
uv run repograph ingest tests/fixtures/rust_workspace --repo-id example-workspace
uv run repograph context tests/fixtures/rust_workspace \
  'rust-analyzer cargo graph-fixture 0.1.0 lib/model/Config#' \
  --repo-id example-workspace
```

The fixture's `model::Config` belongs to package `graph-fixture`, crate
`graph_fixture`, and module `model`. Its definition is at `src/model.rs:1`,
with seven indexed references. The tests verify these relationships against the
fixture's checked-in SCIP export.

## Tests

```sh
uv run python -m unittest discover -v
REPOGRAPH_TEST_NEO4J=1 uv run python -m unittest tests.test_neo4j_integration -v
```

The standard suite uses a checked-in real SCIP export from a small Rust
workspace; it needs no external tools or services. The opt-in Neo4j suite creates
and removes isolated test data, covering symbol context, repeat imports,
unrelated-data preservation and rollback. See [tests/fixtures/README.md](tests/fixtures/README.md).

Format references: [Cargo metadata](https://doc.rust-lang.org/cargo/commands/cargo-metadata.html),
[SCIP schema](https://github.com/scip-code/scip/blob/e01e97efac2f6b8c266b4d04825f1f1eab7b8f6c/scip.proto),
[rust-analyzer exporter](https://github.com/rust-lang/rust-analyzer/blob/b98172d80f/crates/rust-analyzer/src/cli/scip.rs).
