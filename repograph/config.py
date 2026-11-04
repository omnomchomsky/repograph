from dataclasses import dataclass
import os
from pathlib import Path
import shlex

@dataclass(frozen=True)
class Neo4jConfig:
    uri: str
    username: str
    password: str


def load_config(env_file: Path | None = None) -> Neo4jConfig:
    values = {}
    path = env_file if env_file is not None else Path(__file__).with_name("env")
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and key.strip().startswith("REPOGRAPH_NEO4J_"):
                parts = shlex.split(value, comments=True)
                if len(parts) > 1:
                    raise ValueError(f"Invalid value for {key.strip()} in env file")
                values[key.strip()] = parts[0] if parts else ""
    elif env_file is not None:
        raise ValueError(f"Env file does not exist: {env_file}")
    values.update({k: v for k, v in os.environ.items() if k.startswith("REPOGRAPH_NEO4J_")})
    password = values.get("REPOGRAPH_NEO4J_PASSWORD")
    if not password:
        raise RuntimeError("Set REPOGRAPH_NEO4J_PASSWORD or provide it in repograph/env or --env-file")

    return Neo4jConfig(
        uri=values.get("REPOGRAPH_NEO4J_URI", "bolt://localhost:7687"),
        username=values.get("REPOGRAPH_NEO4J_USERNAME", "neo4j"),
        password=password,
    )

