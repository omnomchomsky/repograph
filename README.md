# RepoGraph

RepoGraph parses Python import relationships, builds a graph in Neo4j, 
and exposes dependency context for analysis and LLM-oriented workflows.

## Features

- AST-based Python import parsing
- Internal vs external dependency resolution
- Graph modeling with Neo4j
- Direct, transitive, and reverse dependency queries

---

## Setup

```bash
export REPOGRAPH_NEO4J_URI=your-neo4j-uri:7687
export REPOGRAPH_NEO4J_USERNAME=your-neo4j-user
export REPOGRAPH_NEO4J_PASSWORD=your-neo4j-password
```
---

## Usage

```bash
python -m repograph ingest /path/to/repo
python -m repograph context /path/to/repo main.py

# Or without installation

uv run -m repograph ingest /path/to/repo
uv run -m repograph context /path/to/repo main.py
```
---

## Example Output

Dependency context for main.py:
- Direct dependencies
- Transitive dependencies
- Reverse dependencies
