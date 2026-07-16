from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from tree_sitter import Language, Node, Parser
import tree_sitter_python

MAX_PARSE_BYTES = 1_000_000
PYTHON_EXTENSIONS = {".py", ".pyi"}
STRUCTURAL_INDEX_SCHEMA_VERSION = 1
TREE_SITTER_PYTHON_LANGUAGE = Language(tree_sitter_python.language())


class SnapshotFile(Protocol):
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class IndexedFile:
    path: str
    size_bytes: int
    sha256: str
    language: str | None
    line_count: int
    parse_status: str
    parse_error_count: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "language": self.language,
            "line_count": self.line_count,
            "parse_status": self.parse_status,
            "parse_error_count": self.parse_error_count,
        }


@dataclass(frozen=True)
class IndexedSymbol:
    path: str
    language: str
    kind: str
    name: str
    qualified_name: str
    parent_qualified_name: str | None
    start_line: int
    end_line: int
    start_column: int
    end_column: int
    start_byte: int
    end_byte: int
    signature: str | None

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "language": self.language,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "parent_qualified_name": self.parent_qualified_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "start_column": self.start_column,
            "end_column": self.end_column,
            "start_byte": self.start_byte,
            "end_byte": self.end_byte,
            "signature": self.signature,
        }


@dataclass(frozen=True)
class IndexedImport:
    path: str
    language: str
    kind: str
    module: str | None
    names: list[dict[str, str | None]]
    start_line: int
    end_line: int

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "language": self.language,
            "kind": self.kind,
            "module": self.module,
            "names": self.names,
            "start_line": self.start_line,
            "end_line": self.end_line,
        }


@dataclass(frozen=True)
class StructuralIndex:
    files: list[IndexedFile]
    symbols: list[IndexedSymbol]
    imports: list[IndexedImport]

    @property
    def indexed_file_count(self) -> int:
        return sum(1 for file in self.files if file.parse_status == "parsed")


@dataclass(frozen=True)
class SymbolContext:
    qualified_name: str
    kind: str


class StructuralIndexer:
    def __init__(self) -> None:
        self.python_parser = Parser(TREE_SITTER_PYTHON_LANGUAGE)

    def index(self, repository_path: Path, files: list[SnapshotFile]) -> StructuralIndex:
        indexed_files: list[IndexedFile] = []
        symbols: list[IndexedSymbol] = []
        imports: list[IndexedImport] = []

        for file in files:
            path = repository_path / file.path
            language = language_for_path(file.path)
            line_count = count_lines(path)

            if language is None:
                indexed_files.append(
                    IndexedFile(
                        path=file.path,
                        size_bytes=file.size_bytes,
                        sha256=file.sha256,
                        language=None,
                        line_count=line_count,
                        parse_status="unsupported_language",
                        parse_error_count=0,
                    )
                )
                continue

            if file.size_bytes > MAX_PARSE_BYTES:
                indexed_files.append(
                    IndexedFile(
                        path=file.path,
                        size_bytes=file.size_bytes,
                        sha256=file.sha256,
                        language=language,
                        line_count=line_count,
                        parse_status="too_large",
                        parse_error_count=0,
                    )
                )
                continue

            source = path.read_bytes()
            tree = self.python_parser.parse(source)
            parse_error_count = count_error_nodes(tree.root_node)
            parse_status = "parsed_with_errors" if parse_error_count else "parsed"
            indexed_files.append(
                IndexedFile(
                    path=file.path,
                    size_bytes=file.size_bytes,
                    sha256=file.sha256,
                    language=language,
                    line_count=line_count,
                    parse_status=parse_status,
                    parse_error_count=parse_error_count,
                )
            )
            symbols.extend(collect_python_symbols(file.path, tree.root_node, source))
            imports.extend(collect_python_imports(file.path, tree.root_node))

        return StructuralIndex(files=indexed_files, symbols=symbols, imports=imports)


def language_for_path(path: str) -> str | None:
    if Path(path).suffix.lower() in PYTHON_EXTENSIONS:
        return "python"
    return None


def count_lines(path: Path) -> int:
    content = path.read_bytes()
    if not content:
        return 0
    return content.count(b"\n") + (0 if content.endswith(b"\n") else 1)


def count_error_nodes(node: Node) -> int:
    count = 1 if node.type == "ERROR" or node.is_missing else 0
    for child in node.named_children:
        count += count_error_nodes(child)
    return count


def collect_python_symbols(path: str, root_node: Node, source: bytes) -> list[IndexedSymbol]:
    symbols: list[IndexedSymbol] = []
    walk_python_symbols(
        path=path,
        node=root_node,
        source=source,
        parent_stack=[],
        symbols=symbols,
    )
    return symbols


def walk_python_symbols(
    *,
    path: str,
    node: Node,
    source: bytes,
    parent_stack: list[SymbolContext],
    symbols: list[IndexedSymbol],
) -> None:
    definition_node = python_definition_node(node)
    if definition_node is not None:
        name_node = definition_node.child_by_field_name("name")
        if name_node is None:
            return
        name = node_text(name_node)
        symbol_kind = python_symbol_kind(definition_node, parent_stack)
        parent_qualified_name = parent_stack[-1].qualified_name if parent_stack else None
        qualified_name = ".".join([*(context.qualified_name for context in parent_stack[-1:]), name])
        if not parent_stack:
            qualified_name = name
        span_node = node if node.type == "decorated_definition" else definition_node
        symbol = IndexedSymbol(
            path=path,
            language="python",
            kind=symbol_kind,
            name=name,
            qualified_name=qualified_name,
            parent_qualified_name=parent_qualified_name,
            start_line=span_node.start_point.row + 1,
            end_line=span_node.end_point.row + 1,
            start_column=span_node.start_point.column,
            end_column=span_node.end_point.column,
            start_byte=span_node.start_byte,
            end_byte=span_node.end_byte,
            signature=signature_text(definition_node, source),
        )
        symbols.append(symbol)
        body = definition_node.child_by_field_name("body")
        if body is not None:
            walk_python_symbols(
                path=path,
                node=body,
                source=source,
                parent_stack=[*parent_stack, SymbolContext(qualified_name=qualified_name, kind=symbol_kind)],
                symbols=symbols,
            )
        return

    for child in node.named_children:
        walk_python_symbols(
            path=path,
            node=child,
            source=source,
            parent_stack=parent_stack,
            symbols=symbols,
        )


def python_definition_node(node: Node) -> Node | None:
    if node.type in {"class_definition", "function_definition"}:
        return node
    if node.type == "decorated_definition":
        definition = node.child_by_field_name("definition")
        if definition is not None and definition.type in {"class_definition", "function_definition"}:
            return definition
    return None


def python_symbol_kind(definition_node: Node, parent_stack: list[SymbolContext]) -> str:
    if definition_node.type == "class_definition":
        return "class"
    if parent_stack and parent_stack[-1].kind == "class":
        return "method"
    return "function"


def signature_text(definition_node: Node, source: bytes) -> str | None:
    body = definition_node.child_by_field_name("body")
    end_byte = body.start_byte if body is not None else definition_node.end_byte
    signature = source[definition_node.start_byte:end_byte].decode("utf-8", errors="replace")
    first_line = signature.strip().splitlines()[0].strip()
    return first_line.removesuffix(":")


def collect_python_imports(path: str, root_node: Node) -> list[IndexedImport]:
    imports: list[IndexedImport] = []
    walk_python_imports(path=path, node=root_node, imports=imports)
    return imports


def walk_python_imports(*, path: str, node: Node, imports: list[IndexedImport]) -> None:
    if node.type == "import_statement":
        imports.append(
            IndexedImport(
                path=path,
                language="python",
                kind="import",
                module=None,
                names=import_names(node.named_children),
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
            )
        )
        return
    if node.type == "import_from_statement":
        module_nodes: list[Node] = []
        name_nodes: list[Node] = []
        seen_import = False
        for child in node.children:
            if child.type == "import":
                seen_import = True
                continue
            if not child.is_named:
                continue
            if seen_import:
                name_nodes.append(child)
            else:
                module_nodes.append(child)
        imports.append(
            IndexedImport(
                path=path,
                language="python",
                kind="from_import",
                module="".join(node_text(module_node) for module_node in module_nodes) or None,
                names=import_names(name_nodes),
                start_line=node.start_point.row + 1,
                end_line=node.end_point.row + 1,
            )
        )
        return

    for child in node.named_children:
        walk_python_imports(path=path, node=child, imports=imports)


def import_names(nodes: list[Node]) -> list[dict[str, str | None]]:
    return [import_name(node) for node in nodes]


def import_name(node: Node) -> dict[str, str | None]:
    if node.type == "aliased_import":
        name_node = node.child_by_field_name("name")
        alias_node = node.child_by_field_name("alias")
        return {
            "name": node_text(name_node) if name_node is not None else node_text(node),
            "alias": node_text(alias_node) if alias_node is not None else None,
        }
    return {"name": node_text(node), "alias": None}


def node_text(node: Node) -> str:
    return node.text.decode("utf-8", errors="replace")
