Rust indexing fixture
====================

`rust_workspace` is a dependency-free Cargo workspace with a library, binary,
integration test, renamed path dependency, inline module, `#[path]` module, two
different `Config` structs, inherent/trait methods, locals and a function used as
a callback.

`rust.scip` was generated from these sources using
`rust-analyzer 0.3.3105-standalone (b98172d80f 2026-11-01)`:

```sh
cd tests/fixtures/rust_workspace
cargo metadata --format-version 1 --locked --offline > /tmp/fixture-cargo.json
rust-analyzer scip . --output /tmp/fixture.scip --exclude-vendored-libraries
```

The checked-in export has its metadata project root replaced with `file:///FIXTURE`.
`cargo-metadata.json` is the real Cargo output with the workspace path replaced
with `{workspace}`. Tests restore both to the fixture's current location. This
lets the standard suite exercise genuine semantic output without Cargo,
rust-analyzer, network access or Neo4j. To regenerate, apply those same path
substitutions to the new exports and review any changes to expected locations.
