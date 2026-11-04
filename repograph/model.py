"""Database-independent, repository-scoped Rust graph."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
from typing import Any


def repository_id(root: Path, explicit_id: str | None = None) -> str:
    return explicit_id or "repo:" + hashlib.sha256(str(root.resolve()).encode()).hexdigest()[:24]


@dataclass
class Node:
    id: str
    labels: set[str]
    properties: dict[str, Any]


@dataclass
class Edge:
    id: str
    source: str
    kind: str
    target: str
    properties: dict[str, Any]


@dataclass
class RustGraph:
    repo_id: str
    root: Path
    nodes: dict[str, Node] = field(default_factory=dict)
    edges: dict[str, Edge] = field(default_factory=dict)
    scan: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    def key(self, kind: str, identity: str) -> str:
        digest = hashlib.sha256(json.dumps([self.repo_id, kind, identity]).encode()).hexdigest()
        return f"{kind.lower()}:{digest}"

    def node(self, node_type: str, identity: str, *, labels=(), **properties: Any) -> Node:
        key = self.key(node_type, identity)
        if key not in self.nodes:
            self.nodes[key] = Node(key, {node_type}, {"repo_id": self.repo_id})
        node = self.nodes[key]
        node.labels.update(labels)
        node.properties.update({k: v for k, v in properties.items() if v is not None})
        return node

    def edge(self, source: Node, kind: str, target: Node, **properties: Any) -> Edge:
        identity = json.dumps([source.id, kind, target.id, properties], sort_keys=True)
        key = self.key("edge", identity)
        edge = Edge(key, source.id, kind, target.id, properties)
        self.edges[key] = edge
        return edge

    def file(self, path: str, **properties: Any) -> Node:
        return self.node("File", path, name=path, path=path, **properties)

    def summary(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "scan": self.scan,
            "nodes": dict(sorted(Counter(label for n in self.nodes.values() for label in n.labels).items())),
            "relationships": dict(sorted(Counter(e.kind for e in self.edges.values()).items())),
            "warnings": self.warnings,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "nodes": [dict(id=n.id, labels=sorted(n.labels), properties=n.properties)
                      for n in sorted(self.nodes.values(), key=lambda n: n.id)],
            "edges": [dict(id=e.id, source=e.source, type=e.kind, target=e.target,
                           properties=e.properties)
                      for e in sorted(self.edges.values(), key=lambda e: e.id)],
        }
