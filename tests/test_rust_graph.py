"""Regression tests against a real rust-analyzer export, with no external services."""

import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from google.protobuf.json_format import MessageToJson

from repograph.cargo import import_cargo
from repograph.cli import main
from repograph.config import load_config
from repograph.model import RustGraph, repository_id
from repograph.proto import scip_pb2 as pb
from repograph.scan import ScanOptions, build_rust_graph
from repograph.scip import ScipImporter, load_index, occurrence_range, parse_symbol


FIXTURES = Path(__file__).parent / "fixtures"
ROOT = (FIXTURES / "rust_workspace").resolve()
PREFIX = "rust-analyzer cargo graph-fixture 0.1.0 "


def metadata():
    return json.loads((FIXTURES / "cargo-metadata.json").read_text().replace("{workspace}", str(ROOT)))


def index():
    value = load_index(FIXTURES / "rust.scip")
    value.metadata.project_root = ROOT.as_uri()
    return value


def make_graph(repo_id="fixture", data=None, scip=None):
    graph = RustGraph(repo_id, ROOT)
    cargo = import_cargo(graph, metadata() if data is None else data)
    ScipImporter(graph, cargo).import_index(index() if scip is None else scip)
    return graph


def find_symbol(graph, suffix):
    return next(n for n in graph.nodes.values() if n.properties.get("scip_symbol") == PREFIX + suffix)


def ancestors(graph, node):
    found = set()
    pending = [node.id]
    while pending:
        current = pending.pop()
        for edge in graph.edges.values():
            if edge.target == current and edge.kind == "CONTAINS" and edge.source not in found:
                found.add(edge.source)
                pending.append(edge.source)
    return [graph.nodes[key] for key in found]


class RustGraphTests(unittest.TestCase):
    def setUp(self):
        self.graph = make_graph()

    def test_acceptance_symbol_crate_module_definition_and_references(self):
        config = find_symbol(self.graph, "lib/model/Config#")
        self.assertIn("Struct", config.labels)
        owners = ancestors(self.graph, config)
        self.assertEqual([n.properties["name"] for n in owners if "Crate" in n.labels], ["graph_fixture"])
        self.assertEqual({n.properties["name"] for n in owners if "Module" in n.labels}, {"lib", "model"})
        definitions = [e for e in self.graph.edges.values() if e.source == config.id and e.kind == "DEFINED_IN"]
        self.assertEqual(len(definitions), 1)
        self.assertEqual(definitions[0].properties["path"], "src/model.rs")
        self.assertEqual(definitions[0].properties["start_line"], 0)
        self.assertEqual(definitions[0].properties["position_encoding"], "UTF8CodeUnitOffsetFromLineStart")
        references = [e for e in self.graph.edges.values() if e.target == config.id and e.kind == "REFERENCES"]
        self.assertEqual(len(references), 7)
        self.assertIn("tests/api.rs", [r.properties["path"] for r in references])

    def test_package_targets_are_separate_with_correct_root_descriptors(self):
        package = next(n for n in self.graph.nodes.values() if "Package" in n.labels and n.properties["name"] == "graph-fixture")
        targets = [self.graph.nodes[e.target] for e in self.graph.edges.values() if e.source == package.id and e.kind == "HAS_TARGET"]
        self.assertEqual({t.properties["name"] for t in targets}, {"graph_fixture", "fixture-cli", "api"})
        for suffix, expected in [("lib/model/Config#", "graph_fixture"),
                                 ("bin/fixture_cli/main().", "fixture-cli"),
                                 ("test/api/smoke().", "api")]:
            owners = ancestors(self.graph, find_symbol(self.graph, suffix))
            self.assertEqual([o.properties["name"] for o in owners if "Crate" in o.labels], [expected])

    def test_inline_and_explicit_path_modules_are_not_files(self):
        inline = find_symbol(self.graph, "lib/inline/")
        engine = find_symbol(self.graph, "lib/engine/")
        for module, expected_file in [(inline, "src/lib.rs"), (engine, "src/odd_location.rs")]:
            self.assertNotIn("File", module.labels)
            definitions = [e for e in self.graph.edges.values() if e.source == module.id and e.kind == "DEFINED_IN"]
            self.assertEqual([self.graph.nodes[e.target].properties["path"] for e in definitions], [expected_file])
        evaluate = find_symbol(self.graph, "lib/engine/evaluate().")
        self.assertIn(engine, ancestors(self.graph, evaluate))

    def test_duplicate_names_and_document_local_ids_do_not_collide(self):
        configs = [n for n in self.graph.nodes.values() if n.properties.get("name") == "Config" and "Struct" in n.labels]
        self.assertEqual(len(configs), 2)
        self.assertNotEqual(configs[0].id, configs[1].id)
        locals = [n for n in self.graph.nodes.values() if n.properties.get("scip_symbol") == "local 0"]
        self.assertGreater(len(locals), 1)
        self.assertEqual(len(locals), len({n.id for n in locals}))
        for local in locals:
            self.assertTrue(ancestors(self.graph, local))

    def test_repository_scoping_and_repeat_import_identity(self):
        again = make_graph()
        other = make_graph("other-repository")
        self.assertEqual(set(self.graph.nodes), set(again.nodes))
        self.assertEqual(set(self.graph.edges), set(again.edges))
        self.assertFalse(set(self.graph.nodes) & set(other.nodes))
        self.assertEqual(repository_id(ROOT), repository_id(ROOT / "src" / ".."))

    def test_renamed_resolved_cargo_dependency(self):
        deps = [e for e in self.graph.edges.values() if e.kind == "DEPENDS_ON"]
        self.assertEqual(len(deps), 1)
        edge = deps[0]
        self.assertEqual(edge.properties["alias"], "toolkit")
        self.assertEqual(self.graph.nodes[edge.target].properties["name"], "fixture-helper")

    def test_dependency_kinds_and_conditions_remain_distinct(self):
        data = metadata()
        dep = next(n for n in data["resolve"]["nodes"] if n["deps"])["deps"][0]
        dep["dep_kinds"] = [{"kind": None, "target": None}, {"kind": "build", "target": 'cfg(unix)'}]
        graph = make_graph(data=data)
        edges = [e for e in graph.edges.values() if e.kind == "DEPENDS_ON"]
        self.assertEqual({e.properties["dependency_kind"] for e in edges}, {"normal", "build"})
        self.assertEqual({e.properties["target_condition"] for e in edges}, {"", "cfg(unix)"})

    def test_callback_is_reference_and_no_call_or_implementation_edges_are_invented(self):
        evaluate = find_symbol(self.graph, "lib/engine/evaluate().")
        refs = [e for e in self.graph.edges.values() if e.kind == "REFERENCES" and e.target == evaluate.id]
        self.assertTrue(any(e.properties["path"] == "src/lib.rs" and e.properties["start_line"] == 10 for e in refs))
        self.assertFalse({e.kind for e in self.graph.edges.values()} & {"CALLS", "FOR_TYPE", "IMPLEMENTS"})
        self.assertTrue(any("Impl" in n.labels for n in self.graph.nodes.values()))

    def test_definition_role_is_a_bitmask(self):
        value = index()
        for document in value.documents:
            for occ in document.occurrences:
                if occ.symbol_roles & pb.Definition:
                    occ.symbol_roles |= pb.ReadAccess
        graph = make_graph(scip=value)
        self.assertEqual(len([e for e in graph.edges.values() if e.kind == "DEFINED_IN"]), 29)
        self.assertEqual(len([e for e in graph.edges.values() if e.kind == "REFERENCES"]), 50)

    def test_all_edges_have_real_endpoints(self):
        for edge in self.graph.edges.values():
            self.assertIn(edge.source, self.graph.nodes)
            self.assertIn(edge.target, self.graph.nodes)

    def test_missing_cargo_resolution_is_reported(self):
        data = metadata()
        data["resolve"] = None
        graph = make_graph(data=data)
        self.assertTrue(any("resolve is absent" in w for w in graph.warnings))
        self.assertFalse(any(e.kind == "DEPENDS_ON" for e in graph.edges.values()))


class ScipFormatTests(unittest.TestCase):
    def test_escaped_descriptors_methods_and_parameters(self):
        value = parse_symbol("rust-analyzer cargo my  pkg 0.1.0 lib/`module.name`/`a``b`#method(+1).(arg)[T]")
        self.assertEqual(value.package, "my pkg")
        self.assertEqual([d.name for d in value.descriptors], ["lib", "module.name", "a`b", "method", "arg", "T"])
        self.assertTrue(value.prefix(4).endswith("method(+1)."))
        self.assertIsNone(parse_symbol("local 42"))

    def test_json_and_binary_decode_equivalently(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "index.json"
            path.write_text(MessageToJson(index()))
            graph = make_graph(scip=load_index(path))
        self.assertEqual(graph.as_dict()["nodes"], make_graph().as_dict()["nodes"])

    def test_legacy_and_typed_ranges_with_typed_precedence(self):
        occ = pb.Occurrence(range=[1, 2, 4], enclosing_range=[0, 0, 3, 1])
        self.assertEqual(occurrence_range(occ), [1, 2, 1, 4])
        self.assertEqual(occurrence_range(occ, enclosing=True), [0, 0, 3, 1])
        occ.single_line_range.CopyFrom(pb.SingleLineRange(line=2, start_character=1, end_character=5))
        self.assertEqual(occurrence_range(occ), [2, 1, 2, 5])
        occ.multi_line_enclosing_range.CopyFrom(pb.MultiLineRange(start_line=0, end_line=4, end_character=9))
        self.assertEqual(occurrence_range(occ, enclosing=True), [0, 0, 4, 9])

    def test_invalid_range_and_document_path_rejected(self):
        for span in ([0, 1], [-1, 0, 1], [3, 5, 1, 0]):
            with self.assertRaises(ValueError):
                occurrence_range(pb.Occurrence(range=span))
        for path in ("../other.rs", "/outside.rs", "src/./lib.rs", "src//lib.rs", "src/", "src\\lib.rs"):
            value = index()
            value.documents[0].relative_path = path
            with self.assertRaises(ValueError):
                make_graph(scip=value)


class ScanAndCliTests(unittest.TestCase):
    def test_generated_scan_passes_matching_configuration_to_both_tools(self):
        commands = []
        configs = []

        def execute(command, root, log_path=None):
            commands.append(command)
            self.assertEqual(root, ROOT)
            if command[1] == "metadata":
                return json.dumps(metadata())
            output = Path(command[command.index("--output") + 1])
            config = Path(command[command.index("--config-path") + 1])
            configs.append(json.loads(config.read_text()))
            output.write_bytes(index().SerializeToString())
            log_path.write_text(" ERROR Config Error(s) error_sink=ConfigErrors([])\n"
                                " ERROR Bug: unresolved macro definition\nwarning: fixture diagnostic\n")
            return ""

        with patch("repograph.scan.run", side_effect=execute):
            graph = build_rust_graph(ROOT, ScanOptions(target="aarch64-apple-darwin", features=("extra",),
                                                      no_default_features=True, offline=True))
        self.assertIn("--locked", commands[0])
        self.assertIn("--offline", commands[0])
        self.assertIn("--filter-platform", commands[0])
        self.assertEqual(configs[0]["cargo"]["target"], "aarch64-apple-darwin")
        self.assertEqual(configs[0]["cargo"]["features"], ["extra"])
        self.assertTrue(configs[0]["cargo"]["noDefaultFeatures"])
        self.assertEqual(configs[0]["cargo"]["extraArgs"], ["--locked", "--offline"])
        self.assertTrue(graph.scan["analyzer_version"].startswith("0.3.3105"))
        self.assertEqual(graph.scan["index_source"], "generated")
        self.assertEqual(graph.scan["analyzer_error_count"], 1)
        self.assertEqual(graph.scan["analyzer_warning_count"], 1)
        self.assertTrue(any("may be incomplete" in w for w in graph.warnings))

    def test_wrong_workspace_index_rejected_before_db_write(self):
        with tempfile.TemporaryDirectory() as temp:
            meta = Path(temp) / "cargo.json"
            scip = Path(temp) / "index.scip"
            meta.write_text(json.dumps(metadata()))
            value = index()
            value.metadata.project_root = Path(temp).as_uri()
            scip.write_bytes(value.SerializeToString())
            with self.assertRaisesRegex(ValueError, "SCIP index belongs to"):
                build_rust_graph(ROOT, ScanOptions(metadata_path=meta, scip_path=scip))

    def test_export_without_cargo_target_descriptors_does_not_replace_database(self):
        with tempfile.TemporaryDirectory() as temp:
            meta = Path(temp) / "cargo.json"
            scip = Path(temp) / "index.scip"
            meta.write_text(json.dumps(metadata()))
            value = index()
            # Toolchain 1.99.1 exports module paths without target prefixes.
            for document in value.documents:
                for info in document.symbols:
                    info.symbol = info.symbol.replace("0.1.0 lib/", "0.1.0 ")
                    info.enclosing_symbol = info.enclosing_symbol.replace("0.1.0 lib/", "0.1.0 ")
                for occ in document.occurrences:
                    occ.symbol = occ.symbol.replace("0.1.0 lib/", "0.1.0 ")
            # Also remove roots of the named binary and integration-test targets.
            del value.documents[:2]
            # An empty root descriptor is not a valid SCIP symbol; old exporters use crate/.
            for document in value.documents:
                for info in document.symbols:
                    if info.symbol.endswith("0.1.0 "):
                        info.symbol += "crate/"
                for occ in document.occurrences:
                    if occ.symbol.endswith("0.1.0 "):
                        occ.symbol += "crate/"
            scip.write_bytes(value.SerializeToString())
            with patch("repograph.cli.RepoGraph") as database, patch("sys.stderr", new_callable=io.StringIO):
                with self.assertRaises(SystemExit):
                    main(["ingest", str(ROOT), "--cargo-metadata", str(meta), "--scip", str(scip)])
                database.assert_not_called()

    def test_dry_run_needs_neither_credentials_nor_neo4j(self):
        with tempfile.TemporaryDirectory() as temp:
            meta = Path(temp) / "cargo.json"
            output = Path(temp) / "graph.json"
            meta.write_text(json.dumps(metadata()))
            with patch("repograph.cli.load_config", side_effect=AssertionError("credentials requested")), \
                    patch("repograph.cli.RepoGraph", side_effect=AssertionError("database requested")), \
                    patch("sys.stdout", new_callable=io.StringIO) as stdout:
                main(["ingest", str(ROOT), "--cargo-only", "--cargo-metadata", str(meta),
                      "--dry-run", "--export", str(output)])
            self.assertFalse(json.loads(stdout.getvalue())["stored"])
            self.assertTrue(json.loads(output.read_text())["edges"])

    def test_environment_overrides_env_file_without_shell_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "env"
            path.write_text("export REPOGRAPH_NEO4J_USERNAME='fixture user'\n"
                            "REPOGRAPH_NEO4J_PASSWORD='$(do-not-execute)'\n")
            with patch.dict("os.environ", {"REPOGRAPH_NEO4J_USERNAME": "override"}, clear=True):
                config = load_config(path)
            self.assertEqual(config.username, "override")
            self.assertEqual(config.password, "$(do-not-execute)")


if __name__ == "__main__":
    unittest.main()
