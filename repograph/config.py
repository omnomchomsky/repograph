from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    username: str
    password: str


def load_config() -> Neo4jConfig:
    password = os.environ.get("REPOGRAPH_NEO4J_PASSWORD")
    if not password:
        raise RuntimeError("REPOGRAPH_NEO4J_PASSWORD environment variable not set")

    return Neo4jConfig(
        uri=os.environ.get("REPOGRAPH_NEO4J_URI", "bolt://localhost:7687"),
        username=os.environ.get("REPOGRAPH_NEO4J_USERNAME", "neo4j"),
        password=password,
    )

