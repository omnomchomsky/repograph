"""Neo4j storage and read-only views of the latest Rust scan per repository."""

from collections import defaultdict
from itertools import islice
import re

from neo4j import GraphDatabase

from .model import RustGraph


def batches(items, size=1000):
    iterator = iter(items)
    while batch := list(islice(iterator, size)):
        yield batch


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid graph label or relationship type: {value!r}")
    return value


class RepoGraph:
    def __init__(self, uri: str, username: str, password: str) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(username, password))

    def close(self) -> None:
        self._driver.close()

    def load(self, graph: RustGraph) -> None:
        node_groups = defaultdict(list)
        for node in graph.nodes.values():
            labels = ":".join(identifier(label) for label in sorted(node.labels))
            node_groups[labels].append({"id": node.id, "properties": node.properties})
        edge_groups = defaultdict(list)
        for edge in graph.edges.values():
            edge_groups[identifier(edge.kind)].append(dict(id=edge.id, source=edge.source,
                                                         target=edge.target, properties=edge.properties))

        def write(tx):
            # The repository anchor serializes concurrent imports of this repository.
            tx.run("MERGE (r:RepoGraphRepository {repo_id: $repo_id}) SET r.scan_id = $scan_id",
                   repo_id=graph.repo_id, scan_id=graph.scan["id"]).consume()
            tx.run("MATCH (n:RepoGraphNode {repo_id: $repo_id}) DETACH DELETE n",
                   repo_id=graph.repo_id).consume()
            for labels, nodes in node_groups.items():
                for batch in batches(nodes):
                    tx.run(f"UNWIND $rows AS row CREATE (n:RepoGraphNode:{labels}) "
                           "SET n = row.properties, n.id = row.id, n.scan_id = $scan_id",
                           rows=batch, scan_id=graph.scan["id"]).consume()
            for kind, edges in edge_groups.items():
                for batch in batches(edges):
                    tx.run("UNWIND $rows AS row MATCH (s:RepoGraphNode {id: row.source}), "
                           "(t:RepoGraphNode {id: row.target}) "
                           f"CREATE (s)-[r:{kind}]->(t) SET r = row.properties, r.id = row.id",
                           rows=batch).consume()
            tx.run("MATCH (r:RepoGraphRepository {repo_id: $repo_id}) "
                   "CREATE (s:Scan) SET s = $scan, s.repo_id = $repo_id "
                   "CREATE (r)-[:HAS_SCAN]->(s)", repo_id=graph.repo_id,
                   scan={**graph.scan, "warnings": graph.warnings}).consume()

        with self._driver.session() as session:
            session.run("CREATE CONSTRAINT repograph_node_id IF NOT EXISTS "
                        "FOR (n:RepoGraphNode) REQUIRE n.id IS UNIQUE").consume()
            session.run("CREATE CONSTRAINT repograph_repository_id IF NOT EXISTS "
                        "FOR (n:RepoGraphRepository) REQUIRE n.repo_id IS UNIQUE").consume()
            session.execute_write(write)

    def _read(self, query: str, **parameters) -> list[dict]:
        with self._driver.session() as session:
            return session.execute_read(lambda tx: tx.run(query, **parameters).data())

    def symbols(self, repo_id: str, search: str, limit: int = 30) -> list[dict]:
        return self._read(
            "MATCH (s:RepoGraphNode:Symbol {repo_id: $repo_id}) "
            "WHERE s.name CONTAINS $search OR s.scip_symbol CONTAINS $search "
            "RETURN s.id AS id, s.name AS name, s.kind AS kind, s.scip_symbol AS scip_symbol, "
            "s.external AS external ORDER BY external, name, id LIMIT $limit",
            repo_id=repo_id, search=search, limit=limit)

    def context(self, repo_id: str, selector: str, limit: int = 100) -> dict:
        candidates = self._read(
            "MATCH (s:RepoGraphNode:Symbol {repo_id: $repo_id}) "
            "WHERE s.id = $selector OR s.scip_symbol = $selector OR s.name = $selector "
            "RETURN properties(s) AS symbol ORDER BY s.id LIMIT 21",
            repo_id=repo_id, selector=selector)
        if not candidates:
            raise ValueError(f"Symbol not found: {selector}. Use 'repograph symbols' to search.")
        if len(candidates) > 1:
            choices = "\n".join(f"  {c['symbol']['id']}  {c['symbol']['scip_symbol']}" for c in candidates)
            raise ValueError(f"Ambiguous symbol {selector!r}; use a full symbol ID:\n{choices}")
        symbol = candidates[0]["symbol"]
        ownership = self._read(
            "MATCH (s:RepoGraphNode:Symbol {id: $id}) "
            "OPTIONAL MATCH p=(c:Crate)-[:CONTAINS*0..64]->(s) "
            "OPTIONAL MATCH (pkg:Package)-[:HAS_TARGET]->(c) "
            "RETURN DISTINCT properties(pkg) AS package, properties(c) AS crate, "
            "[n IN nodes(p) WHERE n:Module | properties(n)] AS modules LIMIT $limit",
            id=symbol["id"], limit=limit)
        definitions = self._read(
            "MATCH (s:RepoGraphNode:Symbol {id: $id})-[d:DEFINED_IN]->(f:File) "
            "RETURN f.path AS file, properties(d) AS location ORDER BY file, d.start_line LIMIT $limit",
            id=symbol["id"], limit=limit)
        references = self._read(
            "MATCH (f:RepoGraphNode:File)-[r:REFERENCES]->(s:Symbol {id: $id}) "
            "RETURN f.path AS file, properties(r) AS location "
            "ORDER BY file, r.start_line, r.start_character LIMIT $limit",
            id=symbol["id"], limit=limit)
        count = self._read("MATCH ()-[r:REFERENCES]->(s:RepoGraphNode:Symbol {id: $id}) "
                           "RETURN count(r) AS count", id=symbol["id"])[0]["count"]
        return dict(symbol=symbol, ownership=ownership, definitions=definitions, references=references,
                    reference_count=count, references_truncated=count > limit)

    def view(self, repo_id: str, view: str, limit: int = 100) -> list[dict]:
        queries = {
            "packages": "MATCH (s:RepoGraphNode:Package {repo_id: $repo_id})-[r:DEPENDS_ON|HAS_TARGET]->(t) "
                        "WHERE s.workspace_member RETURN properties(s) AS source, type(r) AS relationship, "
                        "properties(r) AS properties, properties(t) AS target ORDER BY s.name, t.name LIMIT $limit",
            "structure": "MATCH (s:RepoGraphNode {repo_id: $repo_id})-[r:CONTAINS]->(t) "
                         "WHERE coalesce(t.external, false) = false "
                         "RETURN properties(s) AS source, type(r) AS relationship, properties(t) AS target "
                         "ORDER BY s.name, t.name LIMIT $limit",
        }
        return self._read(queries[view], repo_id=repo_id, limit=limit)
