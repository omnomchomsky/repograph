"""Opt in with REPOGRAPH_TEST_NEO4J=1; all test data is isolated and removed."""

import os
import unittest
from uuid import uuid4

from neo4j.exceptions import Neo4jError

from repograph.config import load_config
from repograph.repo_graph import RepoGraph
from tests.test_rust_graph import make_graph, PREFIX


@unittest.skipUnless(os.environ.get("REPOGRAPH_TEST_NEO4J") == "1", "set REPOGRAPH_TEST_NEO4J=1 for live Neo4j tests")
class Neo4jIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.repo_id = "repograph-test:" + str(uuid4())
        config = load_config()
        self.db = RepoGraph(config.uri, config.username, config.password)
        self.snapshot = make_graph(self.repo_id)
        self.snapshot.scan = {"id": str(uuid4()), "analyzer_version": "fixture"}
        self.db.load(self.snapshot)

    def tearDown(self):
        try:
            with self.db._driver.session() as session:
                session.run("MATCH (n {repo_id: $repo_id}) DETACH DELETE n", repo_id=self.repo_id).consume()
        finally:
            self.db.close()

    def test_acceptance_and_read_only_views(self):
        context = self.db.context(self.repo_id, PREFIX + "lib/model/Config#", limit=2)
        self.assertEqual(context["ownership"][0]["package"]["name"], "graph-fixture")
        self.assertEqual(context["ownership"][0]["crate"]["name"], "graph_fixture")
        self.assertEqual(context["definitions"][0]["file"], "src/model.rs")
        self.assertEqual(context["reference_count"], 7)
        self.assertTrue(context["references_truncated"])
        self.assertEqual(len(context["references"]), 2)
        self.assertTrue(self.db.view(self.repo_id, "packages"))
        self.assertTrue(self.db.view(self.repo_id, "structure"))
        self.assertTrue(self.db.symbols(self.repo_id, "Config"))
        with self.assertRaisesRegex(ValueError, "Ambiguous symbol"):
            self.db.context(self.repo_id, "Config")

    def test_reimport_replaces_only_selected_repository_and_retains_scan_metadata(self):
        marker_id = str(uuid4())
        with self.db._driver.session() as session:
            session.run("CREATE (:File {id: $id, repo_id: $repo_id, name: 'unrelated-python-file'})",
                        id=marker_id, repo_id=self.repo_id).consume()
        obsolete = self.snapshot.file("obsolete.rs")
        self.snapshot.scan["id"] = str(uuid4())
        self.db.load(self.snapshot)
        self.snapshot.nodes.pop(obsolete.id)
        self.snapshot.scan["id"] = str(uuid4())
        self.db.load(self.snapshot)
        with self.db._driver.session() as session:
            self.assertEqual(session.run("MATCH (n:RepoGraphNode {repo_id: $id}) RETURN count(n) AS n",
                                         id=self.repo_id).single()["n"], len(self.snapshot.nodes))
            self.assertEqual(session.run("MATCH (s:Scan {repo_id: $id}) RETURN count(s) AS n",
                                         id=self.repo_id).single()["n"], 3)
            self.assertEqual(session.run("MATCH (f:File {id: $id}) RETURN count(f) AS n",
                                         id=marker_id).single()["n"], 1)
            self.assertEqual(session.run("MATCH (n:RepoGraphNode {id: $id}) RETURN count(n) AS n",
                                         id=obsolete.id).single()["n"], 0)

    def test_failed_write_rolls_back_previous_snapshot_and_scan_pointer(self):
        old_scan_id = self.snapshot.scan["id"]
        self.snapshot.scan["id"] = str(uuid4())
        next(iter(self.snapshot.nodes.values())).properties["invalid_property"] = {"map": "not a Neo4j property"}
        with self.assertRaises(Neo4jError):
            self.db.load(self.snapshot)
        context = self.db.context(self.repo_id, PREFIX + "lib/model/Config#")
        self.assertEqual(context["reference_count"], 7)
        with self.db._driver.session() as session:
            self.assertEqual(session.run("MATCH (r:RepoGraphRepository {repo_id: $id}) RETURN r.scan_id AS id",
                                         id=self.repo_id).single()["id"], old_scan_id)
            self.assertEqual(session.run("MATCH (s:Scan {repo_id: $id}) RETURN count(s) AS n",
                                         id=self.repo_id).single()["n"], 1)
