from typing import Iterable

from neo4j import GraphDatabase, Driver

from .file_dependencies import FileDependency

class RepoGraph:
    def __init__(self, uri: str, username: str, password: str) -> None:
        self._driver: Driver = GraphDatabase.driver(uri, auth=(username, password))

    def close(self) -> None:
        self._driver.close()

    def reset(self) -> None:
        query = """
        MATCH (n)
        DETACH DELETE n
        """
        with self._driver.session() as session:
            session.run(query)

    def create_file_nodes(self, file_path: Iterable[str]) -> None:
        query = """
        UNWIND $file_path AS file_path
        MERGE (f:File {name: file_path})
        """
        with self._driver.session() as session:
            session.run(query, file_path=file_path)

    def create_import_relationships(self, deps: Iterable[FileDependency]) -> None:
        query = """
        UNWIND $deps AS dep
        MERGE (src:File {name: dep.source})
        MERGE (tgt:File {name: dep.target})
        MERGE (src)-[:IMPORTS]->(tgt)
        """
        payload = [{"source": d.source, "target": d.target} for d in deps]
        with self._driver.session() as session:
            session.run(query, deps=payload)

    def get_direct_dependencies(self, file_name: str) -> list[str]:
        query       = """
        MATCH (:File {name: $file_name})-[:IMPORTS]->(dep:File)
        RETURN dep.name AS name
        ORDER BY name  
        """
        with self._driver.session() as session:
            result = session.run(query, file_name=file_name)
            return [record["name"] for record in result]

    def get_transitive_dependencies(self, self_name: str) -> list[str]:
        query = """
        MATCH (:File {name: $file_name})-[:IMPORTS*]->(dep:File)
        RETURN DISTINCT dep.name AS name
        ORDER BY name
        """
        with self._driver.session() as session:
            result = session.run(query, file_name=self_name)
            return [record["name"] for record in result]

    def get_reverse_dependencies(self, file_name: str) -> list[str]:
        query = """
        MATCH (caller:File)-[:IMPORTS]->(:File {name: $file_name})
        RETURN caller.name AS name
        ORDER BY name
        """
        with self._driver.session() as session:
            result = session.run(query, file_name=file_name)
            return [record["name"] for record in result]
