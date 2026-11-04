"""Read binary/JSON SCIP and preserve semantic identities and occurrence locations."""

from __future__ import annotations

from dataclasses import dataclass
from collections import defaultdict
from pathlib import Path, PurePosixPath
import re

from google.protobuf.json_format import Parse

from .cargo import CargoGraph
from .model import Node, RustGraph
from .proto import scip_pb2 as pb


def load_index(path: Path) -> pb.Index:
    index = pb.Index()
    if path.suffix.lower() == ".json":
        Parse(path.read_text(encoding="utf-8"), index)
    else:
        index.ParseFromString(path.read_bytes())
    if not index.metadata.project_root or not index.documents:
        raise ValueError("SCIP index must contain project_root metadata and documents")
    return index


@dataclass(frozen=True)
class Descriptor:
    name: str
    suffix: str
    raw: str


@dataclass(frozen=True)
class Symbol:
    header: str
    package: str
    version: str
    descriptors: tuple[Descriptor, ...]

    def prefix(self, length: int) -> str:
        return self.header + "".join(d.raw for d in self.descriptors[:length])


def parse_symbol(value: str) -> Symbol | None:
    """Parse SCIP's descriptor grammar, including escaped names and parameters."""
    if value.startswith("local "):
        return None
    pos = 0
    header_parts = []
    for _ in range(4):
        token = []
        while pos < len(value):
            if value[pos:pos + 2] == "  ":
                token.append(" ")
                pos += 2
            elif value[pos] == " ":
                pos += 1
                break
            else:
                token.append(value[pos])
                pos += 1
        else:
            raise ValueError(f"Invalid SCIP symbol header: {value!r}")
        header_parts.append("".join(token))
    header = value[:pos]
    descriptors = []

    def name() -> str:
        nonlocal pos
        if pos < len(value) and value[pos] == "`":
            pos += 1
            result = []
            while pos < len(value):
                if value[pos:pos + 2] == "``":
                    result.append("`")
                    pos += 2
                elif value[pos] == "`":
                    pos += 1
                    return "".join(result)
                else:
                    result.append(value[pos])
                    pos += 1
            raise ValueError(f"Unterminated SCIP identifier: {value!r}")
        match = re.match(r"[\w+$-]+", value[pos:], flags=re.ASCII)
        if not match:
            raise ValueError(f"Invalid SCIP descriptor: {value!r}")
        pos += len(match[0])
        return match[0]

    while pos < len(value):
        start = pos
        opening = value[pos] if value[pos] in "[(" else ""
        if opening:
            pos += 1
        descriptor_name = name()
        if opening:
            suffix = "]" if opening == "[" else ")"
            if value[pos:pos + 1] != suffix:
                raise ValueError(f"Invalid SCIP parameter: {value!r}")
            pos += 1
        elif value[pos:pos + 1] == "(":
            pos += 1
            if value[pos:pos + 1] != ")":
                name()  # method disambiguator
            if value[pos:pos + 2] != ").":
                raise ValueError(f"Invalid SCIP method: {value!r}")
            pos += 2
            suffix = "()."
        else:
            suffix = value[pos:pos + 1]
            if suffix not in {"/", "#", ".", ":", "!"}:
                raise ValueError(f"Invalid SCIP descriptor suffix: {value!r}")
            pos += 1
        descriptors.append(Descriptor(descriptor_name, suffix, value[start:pos]))
    if not descriptors:
        raise ValueError(f"SCIP symbol has no descriptors: {value!r}")
    return Symbol(header, header_parts[2], header_parts[3], tuple(descriptors))


def occurrence_range(occ: pb.Occurrence, *, enclosing: bool = False) -> list[int]:
    which = occ.WhichOneof("typed_enclosing_range" if enclosing else "typed_range")
    if which:
        r = getattr(occ, which)
        if which.startswith("single_line"):
            result = [r.line, r.start_character, r.line, r.end_character]
        else:
            result = [r.start_line, r.start_character, r.end_line, r.end_character]
    else:
        result = list(occ.enclosing_range if enclosing else occ.range)
        if len(result) == 3:
            result = [result[0], result[1], result[0], result[2]]
    if not result and enclosing:
        return []
    if len(result) != 4 or min(result) < 0 or result[:2] > result[2:]:
        raise ValueError(f"Invalid SCIP source range: {result}")
    return result


def location(path: str, span: list[int], encoding: str) -> dict:
    return dict(path=path, start_line=span[0], start_character=span[1],
                end_line=span[2], end_character=span[3], position_encoding=encoding)


class ScipImporter:
    def __init__(self, graph: RustGraph, cargo: CargoGraph):
        self.graph = graph
        self.cargo = cargo
        self.symbols: dict[tuple[str, str], Node] = {}
        self.parsed: dict[str, Symbol | None] = {}

    def crate_root(self, parsed: Symbol) -> tuple[int, list[Node]]:
        """Current rust-analyzer uses lib/, bin/name/, test/name/, etc."""
        descriptors = parsed.descriptors
        first = descriptors[0].name
        crates = self.cargo.package_crates.get((parsed.package, parsed.version), [])
        libraries = {"lib", "rlib", "dylib", "cdylib", "staticlib", "proc-macro"}
        if first == "lib":
            return 1, [c for c in crates if libraries.intersection(c.properties["target_kinds"])]
        if first in {"proc-macro", "custom-build"}:
            return 1, [c for c in crates if first in c.properties["target_kinds"]]
        if first in {"bin", "test", "bench", "example", "build-script"} and len(descriptors) >= 2:
            kind = "custom-build" if first == "build-script" else first
            return 2, [c for c in crates if kind in c.properties["target_kinds"]
                       and c.properties["crate_name"] == descriptors[1].name]
        # Older/custom exporters can use the crate name directly.
        return 1, self.cargo.crates.get((parsed.package, parsed.version, first), [])

    def symbol(self, value: str, path: str = "") -> Node:
        local = value.startswith("local ")
        key = (value, path if local else "")
        if key in self.symbols:
            return self.symbols[key]
        parsed = self.parsed.setdefault(value, parse_symbol(value))
        identity = f"{path}|{value}" if local else value
        node = self.graph.node("Symbol", identity, scip_symbol=value, name=value if local else parsed.descriptors[-1].name,
                               local=local, external=True, inferred=True, kind="Unknown")
        self.symbols[key] = node
        if local:
            node.properties["document_path"] = path
            return node
        descriptors = parsed.descriptors
        leaf = descriptors[-1]
        node.properties["qualified_name"] = "::".join(d.name for d in descriptors)
        if leaf.suffix == "/":
            node.labels.add("Module")
        if leaf.name == "impl" and leaf.suffix == "#":
            node.labels.add("Impl")
        root_depth, candidates = self.crate_root(parsed)
        if len(descriptors) > root_depth:
            parent = self.symbol(parsed.prefix(len(descriptors) - 1), path)
            self.graph.edge(parent, "CONTAINS", node, provenance="scip_descriptor")
        else:
            # Tests, examples and binaries can share a crate descriptor. Keep ambiguity explicit.
            for crate in candidates:
                self.graph.edge(crate, "CONTAINS", node, provenance="scip_crate_descriptor",
                                ambiguous=len(candidates) > 1)
            if len(candidates) > 1:
                self.graph.warnings.append(f"Ambiguous Cargo target ownership: {value}")
            elif not candidates:
                node.properties["cargo_target_unresolved"] = True
        return node

    def information(self, info: pb.SymbolInformation, path: str) -> Node:
        node = self.symbol(info.symbol, path)
        try:
            kind = pb.SymbolInformation.Kind.Name(info.kind)
        except ValueError:
            kind = "Unknown"
        if kind != "UnspecifiedKind" and kind != "Unknown":
            node.properties["kind"] = kind
            node.labels.add(kind)
            if kind in {"TraitMethod", "StaticMethod", "AbstractMethod"}:
                node.labels.add("Method")
        node.properties.update(inferred=False, documentation=list(info.documentation))
        if info.display_name:
            node.properties["name"] = info.display_name
        if info.signature_documentation.text:
            node.properties["signature"] = info.signature_documentation.text
        if info.enclosing_symbol:
            parent = self.symbol(info.enclosing_symbol, path)
            self.graph.edge(parent, "CONTAINS", node, provenance="scip_enclosing_symbol")
        return node

    def import_index(self, index: pb.Index) -> None:
        for info in index.external_symbols:
            self.information(info, "")
        for document in index.documents:
            path = document.relative_path
            parts = PurePosixPath(path).parts
            if not parts or "\\" in path or any(p in {"", ".", ".."} for p in path.split("/")):
                raise ValueError(f"SCIP document path must be canonical and relative: {path!r}")
            if document.language and document.language.lower() != "rust":
                continue
            encoding = pb.PositionEncoding.Name(document.position_encoding)
            file = self.graph.file(path, language="rust", position_encoding=encoding)
            for info in document.symbols:
                self.information(info, path)
            for occurrence in document.occurrences:
                if not occurrence.symbol:
                    continue  # highlighting-only occurrences
                node = self.symbol(occurrence.symbol, path)
                span = occurrence_range(occurrence)
                props = location(path, span, encoding)
                props["symbol_roles"] = occurrence.symbol_roles
                if occurrence.symbol_roles & pb.Definition:
                    node.properties["external"] = False
                    body = occurrence_range(occurrence, enclosing=True)
                    if body:
                        props["enclosing_range"] = body
                    self.graph.edge(node, "DEFINED_IN", file, **props)
                else:
                    # File-level references are always supported. SCIP does not establish callers.
                    self.graph.edge(file, "REFERENCES", node, **props)
        # An inferred ancestor of an indexed definition is internal even if it has no occurrence.
        parents = defaultdict(list)
        for edge in self.graph.edges.values():
            if edge.kind == "CONTAINS":
                parents[edge.target].append(edge.source)
        pending = [n.id for n in self.symbols.values() if not n.properties["external"]]
        visited = set(pending)
        while pending:
            for parent in parents[pending.pop()]:
                node = self.graph.nodes[parent]
                if "Symbol" in node.labels and parent not in visited:
                    node.properties["external"] = False
                    visited.add(parent)
                    pending.append(parent)
        for node in self.symbols.values():
            if node.properties.get("cargo_target_unresolved") and not node.properties["external"]:
                self.graph.warnings.append(f"Cargo target unresolved for indexed root: {node.properties['scip_symbol']}")
        self.graph.warnings = sorted(set(self.graph.warnings))
