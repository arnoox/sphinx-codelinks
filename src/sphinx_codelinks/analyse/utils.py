from collections.abc import ByteString, Callable
import configparser
from pathlib import Path
from typing import TypedDict
from urllib.request import pathname2url

from giturlparse import parse  # type: ignore[import-untyped]
from tree_sitter import Language, Parser, Point, Query, QueryCursor
from tree_sitter import Node as TreeSitterNode

from sphinx_codelinks.config import UNIX_NEWLINE, CommentCategory
from sphinx_codelinks.logger import get_logger
from sphinx_codelinks.source_discover.config import CommentType

# Language-specific node types for scope detection.
#
# YAML and JSONC are intentionally absent. They are data formats, not code, so a
# comment associates with the surrounding data structure (key/value pair, list
# item, or scalar) rather than with an enclosing or following declaration. That
# needs a different algorithm (inline same-row association first, scalar targets,
# grammar-specific traversal), implemented in find_yaml_associated_structure and
# find_jsonc_associated_structure and dispatched from find_associated_scope.
# Those bespoke finders never read this table (only find_next_scope and
# find_enclosing_scope do), so an entry here would be dead.
SCOPE_NODE_TYPES = {
    # @Python Scope Node Types, IMPL_PY_2, impl, [FE_PY]
    CommentType.python: {"function_definition", "class_definition"},
    # @C and C++ Scope Node Types, IMPL_C_2, impl, [FE_C_SUPPORT, FE_CPP]
    CommentType.cpp: {"function_definition", "class_definition"},
    CommentType.cs: {"method_declaration", "class_declaration", "property_declaration"},
    # @TypeScript Scope Node Types, IMPL_TS_2, impl, [FE_TS]
    CommentType.ts: {
        "function_declaration",
        "class_declaration",
        "method_definition",
        "lexical_declaration",
        "variable_declaration",
    },
    # @Rust Scope Node Types, IMPL_RUST_2, impl, [FE_RUST];
    CommentType.rust: {
        "function_item",
        "struct_item",
        "enum_item",
        "impl_item",
        "trait_item",
        "mod_item",
    },
    # @Go Scope Node Types, IMPL_GO_4, impl, [FE_GO]
    CommentType.go: {
        "function_declaration",
        "method_declaration",
        "type_declaration",
        "type_spec",
    },
    # @Bash Scope Node Types, IMPL_BASH_2, impl, [FE_BASH]
    CommentType.bash: {"function_definition"},
}

logger = get_logger(__name__)

GIT_HOST_URL_TEMPLATE = {
    "github": "https://github.com/{owner}/{repo}/blob/{rev}/{path}#L{lineno}",
    "gitlab": "https://gitlab.com/{owner}/{repo}/-/blob/{rev}/{path}#L{lineno}",
}

PYTHON_QUERY = """
                ; Match comments
                (comment) @comment

                ; Match docstrings inside modules, functions, or classes
                (module (expression_statement (string)) @comment)
                (function_definition (block (expression_statement (string)) @comment))
                (class_definition (block (expression_statement (string)) @comment))
            """
CPP_QUERY = """(comment) @comment"""
C_SHARP_QUERY = """(comment) @comment"""
# @TypeScript comment query for tree-sitter, IMPL_TS_3, impl, [FE_TS]
TYPE_SCRIPT_QUERY = """(comment) @comment"""
YAML_QUERY = """(comment) @comment"""
RUST_QUERY = """
    (line_comment) @comment
    (block_comment) @comment
"""
# @Go comment query for tree-sitter, IMPL_GO_3, impl, [FE_GO]
GO_QUERY = """
    (comment) @comment
"""
JSONC_QUERY = """(comment) @comment"""
# @Bash comment query for tree-sitter, IMPL_BASH_3, impl, [FE_BASH]
BASH_QUERY = """(comment) @comment"""

# JSON value node types that can be associated with a comment.
JSON_STRUCTURE_TYPES = {
    "pair",
    "object",
    "array",
    "string",
    "number",
    "true",
    "false",
    "null",
}

# TypeScript's own module variants. Legacy angle-bracket type assertions
# (``<T>x``) are valid syntax only here, not under the TSX grammar (see the
# CommentType.ts branch of init_tree_sitter for what goes wrong otherwise), so
# these three suffixes get the plain TypeScript grammar and everything else in
# the JS/TS family falls back to TSX.
TS_STRICT_GRAMMAR_SUFFIXES = {".ts", ".mts", ".cts"}


def ts_grammar_key(src_path: Path) -> str:
    """Return which tree-sitter-typescript grammar ``src_path`` needs.

    ``"typescript"`` for TypeScript's own module variants (``.ts``, ``.mts``,
    ``.cts``); ``"tsx"`` for the rest of the JavaScript/TypeScript family
    (``.tsx``, ``.jsx``, ``.js``, ``.mjs``, ``.cjs``). Used both to pick the
    grammar in ``init_tree_sitter`` and, by callers that parse many files, to
    cache one parser per grammar instead of rebuilding one per file.
    """
    return "typescript" if src_path.suffix in TS_STRICT_GRAMMAR_SUFFIXES else "tsx"


def is_text_file(filepath: Path, sample_size: int = 2048) -> bool:
    """Return True if file is likely text, False if binary."""
    try:
        with filepath.open("rb") as f:
            chunk = f.read(sample_size)
        # Quick binary heuristic: null byte present
        if b"\x00" in chunk:
            return False
        # Try UTF-8 decode on the sample
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


# @Tree-sitter parser initialization for multiple languages, IMPL_LANG_1, impl, [FE_C_SUPPORT, FE_CPP, FE_PY, FE_YAML, FE_RUST, FE_GO, FE_JSONC, FE_BASH, FE_TS]
def init_tree_sitter(
    comment_type: CommentType, src_path: Path | None = None
) -> tuple[Parser, Query]:
    """Build the (parser, query) pair for ``comment_type``.

    ``src_path`` only matters for ``CommentType.ts``, whose grammar varies by
    file suffix (see ``ts_grammar_key``); every other comment type ignores it
    and uses a single grammar. When ``src_path`` is omitted the TSX grammar is
    assumed, which is the safe default for the whole JS/TS family except
    TypeScript's own module variants.
    """
    if comment_type == CommentType.cpp:
        import tree_sitter_cpp  # noqa: PLC0415

        parsed_language = Language(tree_sitter_cpp.language())
        query = Query(parsed_language, CPP_QUERY)
    elif comment_type == CommentType.python:
        import tree_sitter_python  # noqa: PLC0415

        parsed_language = Language(tree_sitter_python.language())
        query = Query(parsed_language, PYTHON_QUERY)
    elif comment_type == CommentType.cs:
        import tree_sitter_c_sharp  # noqa: PLC0415

        parsed_language = Language(tree_sitter_c_sharp.language())
        query = Query(parsed_language, C_SHARP_QUERY)
    elif comment_type == CommentType.ts:
        import tree_sitter_typescript  # noqa: PLC0415

        # Legacy angle-bracket type assertions (``<T>x``) are valid TypeScript
        # syntax in .ts/.mts/.cts, but the same text is JSX syntax under the TSX
        # grammar: ``<string>x`` parses as a jsx_opening_element and swallows the
        # rest of the file into a single jsx_text node, silently dropping every
        # marker after it. So the plain TypeScript grammar is required for those
        # three suffixes. The TSX grammar remains the fallback for the rest of
        # the JS/TS family (.tsx, .jsx, .js, .mjs, .cjs): JavaScript has no such
        # cast syntax, so TSX is safe there, and it additionally handles JSX
        # embedded in plain .js.
        if src_path is not None and ts_grammar_key(src_path) == "typescript":
            parsed_language = Language(tree_sitter_typescript.language_typescript())
        else:
            parsed_language = Language(tree_sitter_typescript.language_tsx())
        query = Query(parsed_language, TYPE_SCRIPT_QUERY)
    elif comment_type == CommentType.yaml:
        import tree_sitter_yaml  # noqa: PLC0415

        parsed_language = Language(tree_sitter_yaml.language())
        query = Query(parsed_language, YAML_QUERY)
    elif comment_type == CommentType.rust:
        import tree_sitter_rust  # noqa: PLC0415

        parsed_language = Language(tree_sitter_rust.language())
        query = Query(parsed_language, RUST_QUERY)
    elif comment_type == CommentType.go:
        import tree_sitter_go  # noqa: PLC0415

        parsed_language = Language(tree_sitter_go.language())
        query = Query(parsed_language, GO_QUERY)
    elif comment_type == CommentType.jsonc:
        import tree_sitter_json  # noqa: PLC0415

        parsed_language = Language(tree_sitter_json.language())
        query = Query(parsed_language, JSONC_QUERY)
    elif comment_type == CommentType.bash:
        import tree_sitter_bash  # noqa: PLC0415

        parsed_language = Language(tree_sitter_bash.language())
        query = Query(parsed_language, BASH_QUERY)
    else:
        raise ValueError(f"Unsupported comment style: {comment_type}")
    parser = Parser(parsed_language)
    return parser, query


def wrap_read_callable_point(
    src_string: ByteString,
) -> Callable[[int, Point], ByteString]:
    def read_callable_byte_offset(byte_offset: int, _: Point) -> ByteString:
        return src_string[byte_offset : byte_offset + 1]

    return read_callable_byte_offset


# @Comment extraction from source code using tree-sitter, IMPL_EXTR_1, impl, [FE_DEF]
def extract_comments(
    src_string: ByteString, parser: Parser, query: Query
) -> list[TreeSitterNode] | None:
    """Get all comments from source files by tree-sitter."""
    read_point_fn = wrap_read_callable_point(src_string)
    tree = parser.parse(read_point_fn)
    query_cursor = QueryCursor(query)
    captures: dict[str, list[TreeSitterNode]] = query_cursor.captures(tree.root_node)

    return captures.get("comment")


TS_FUNCTION_VALUE_TYPES = {"arrow_function", "function_expression"}


def _is_function_like_lexical_declaration(node: TreeSitterNode) -> bool:
    """True if a TS lexical/variable declaration's declarator is a function.

    ``const``/``let``/``var`` declarations are only treated as scopes when they
    assign a function or arrow function, so a leading comment doesn't bind to an
    unrelated ``const`` that merely precedes the function it documents.
    """
    for declarator in node.named_children:
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        if value is not None and value.type in TS_FUNCTION_VALUE_TYPES:
            return True
    return False


def _matches_scope(
    node: TreeSitterNode, scope_types: set[str], comment_type: CommentType
) -> bool:
    if node.type not in scope_types:
        return False
    if comment_type == CommentType.ts and node.type in {
        "lexical_declaration",
        "variable_declaration",
    }:
        return _is_function_like_lexical_declaration(node)
    return True


def find_enclosing_scope(
    node: TreeSitterNode, comment_type: CommentType = CommentType.cpp
) -> TreeSitterNode | None:
    """Find the enclosing scope of a comment."""
    scope_types = SCOPE_NODE_TYPES.get(comment_type, SCOPE_NODE_TYPES[CommentType.cpp])
    current: TreeSitterNode = node
    while current:
        if _matches_scope(current, scope_types, comment_type):
            return current
        current: TreeSitterNode | None = current.parent  # type: ignore[no-redef]  # required for node traversal
    return None


def find_next_scope(
    node: TreeSitterNode, comment_type: CommentType = CommentType.cpp
) -> TreeSitterNode | None:
    """Find the next scope of a comment."""
    scope_types = SCOPE_NODE_TYPES.get(comment_type, SCOPE_NODE_TYPES[CommentType.cpp])
    current: TreeSitterNode = node
    while current:
        if _matches_scope(current, scope_types, comment_type):
            return current
        current: TreeSitterNode | None = current.next_named_sibling  # type: ignore[no-redef]  # required for node traversal
        if current and current.type in {"block", "export_statement"}:
            for child in current.named_children:
                if _matches_scope(child, scope_types, comment_type):
                    return child
    return None


def _find_yaml_structure_in_block_node(
    block_node: TreeSitterNode,
) -> TreeSitterNode | None:
    """Find YAML structure elements within a block_node."""
    for grandchild in block_node.named_children:
        if grandchild.type == "block_mapping":
            for ggchild in grandchild.named_children:
                if ggchild.type == "block_mapping_pair":
                    return ggchild
        elif grandchild.type == "block_sequence":
            for ggchild in grandchild.named_children:
                if ggchild.type == "block_sequence_item":
                    return ggchild
    return None


def find_yaml_next_structure(node: TreeSitterNode) -> TreeSitterNode | None:
    """Find the next YAML structure element after the comment node."""
    current = node.next_named_sibling
    while current:
        if current.type in {
            "block_mapping_pair",
            "block_sequence_item",
            "flow_mapping",
            "flow_sequence",
        }:
            return current
        if current.type == "document":
            for child in current.named_children:
                if child.type == "block_node":
                    result = _find_yaml_structure_in_block_node(child)
                    if result:
                        return result
        if current.type == "block_node":
            result = _find_yaml_structure_in_block_node(current)
            if result:
                return result
        current = current.next_named_sibling
    return None


def find_prev_sibling_on_same_row(node: TreeSitterNode) -> TreeSitterNode | None:
    """Find a previous named sibling that is on the same row as the comment.

    Grammar-agnostic: used to detect inline comments in both YAML and JSONC.
    """
    comment_row = node.start_point.row
    current = node.prev_named_sibling

    while current:
        # Check if this sibling ends on the same row as the comment starts
        # This indicates it's an inline comment
        if current.end_point.row == comment_row:
            return current
        # If we find a sibling that ends before the comment row, we can stop
        # as we won't find any siblings on the same row going backwards
        if current.end_point.row < comment_row:
            break
        current = current.prev_named_sibling

    return None


def find_yaml_associated_structure(node: TreeSitterNode) -> TreeSitterNode | None:
    """Find the YAML structure (key-value pair, list item, etc.) associated with a comment."""
    # First, check if this is an inline comment by looking for a previous sibling on the same row
    prev_sibling_same_row = find_prev_sibling_on_same_row(node)
    if prev_sibling_same_row:
        return prev_sibling_same_row

    # If no previous sibling on same row, try to find the next named sibling (structure after the comment)
    structure = find_yaml_next_structure(node)
    if structure:
        return structure

    # If no next sibling found, traverse up to find parent structure
    parent = node.parent
    while parent:
        if parent.type in {"block_mapping_pair", "block_sequence_item"}:
            return parent
        parent = parent.parent

    return None


# @JSONC comment-to-structure association, IMPL_JSONC_2, impl, [FE_JSONC]
def find_jsonc_associated_structure(node: TreeSitterNode) -> TreeSitterNode | None:
    """Find the JSON structure (key/value pair, value, list item) for a comment.

    JSON is data rather than code, so association follows the same intent as YAML:
    an inline comment belongs to the value on its row, a leading comment belongs to
    the following structure, otherwise it belongs to the enclosing structure.
    """
    # Inline comment: a value/pair on the same row, before the comment
    prev_sibling_same_row = find_prev_sibling_on_same_row(node)
    if prev_sibling_same_row:
        return prev_sibling_same_row

    # Leading comment: the next structure following the comment
    current = node.next_named_sibling
    while current:
        if current.type in JSON_STRUCTURE_TYPES:
            return current
        current = current.next_named_sibling

    # Otherwise: the enclosing structure
    parent = node.parent
    while parent:
        if parent.type in {"pair", "object", "array"}:
            return parent
        parent = parent.parent

    return None


def find_associated_scope(
    node: TreeSitterNode, comment_type: CommentType = CommentType.cpp
) -> TreeSitterNode | None:
    """Find the associated scope of a comment."""
    if comment_type == CommentType.yaml:
        # YAML uses different structure association logic
        return find_yaml_associated_structure(node)

    if comment_type == CommentType.jsonc:
        # JSONC uses data-aware structure association logic
        return find_jsonc_associated_structure(node)

    if node.type == CommentCategory.docstring:
        # Only for python's docstring
        return find_enclosing_scope(node, comment_type)
    # General comments regardless of comment types
    associated_scope = find_next_scope(node, comment_type)
    if not associated_scope:
        associated_scope = find_enclosing_scope(node, comment_type)
    return associated_scope


def locate_git_root(src_dir: Path) -> Path | None:
    """Traverse upwards to find git root."""
    current = src_dir.resolve()
    parents = list(current.parents)
    parents.append(current)
    for parent in parents:
        if (parent / ".git").exists() and (parent / ".git").is_dir():
            return parent
    logger.warning(
        f"git root is not found in the parent of {src_dir}",
        subtype="git_root",
        location=str(src_dir),
    )
    return None


def get_remote_url(git_root: Path, remote_name: str = "origin") -> str | None:
    """Get remote url from .git/config."""
    config_path = git_root / ".git" / "config"
    if not config_path.exists():
        logger.warning(
            f"{config_path} does not exist",
            subtype="git_config",
            location=str(config_path),
        )
        return None

    config = configparser.ConfigParser(allow_no_value=True, strict=False)
    config.read(config_path)
    section = f'remote "{remote_name}"'
    if section in config and "url" in config[section]:
        url: str = config[section]["url"]
        return url
    logger.warning(
        f"remote-url is not found in {config_path}",
        subtype="git_remote",
        location=str(config_path),
    )
    return None


def get_current_rev(git_root: Path) -> str | None:
    """Get current commit rev from .git/HEAD."""
    head_path = git_root / ".git" / "HEAD"
    if not head_path.exists():
        logger.warning(
            f"{head_path} does not exist",
            subtype="git_head",
            location=str(head_path),
        )
        return None
    head_content = head_path.read_text().strip()
    if not head_content.startswith("ref: "):
        # Detached HEAD (e.g. CI checkouts): .git/HEAD holds the commit SHA
        # directly, which is exactly the rev we want.
        return head_content

    ref_path = git_root / ".git" / head_content.split(":", 1)[1].strip()
    if not ref_path.exists():
        logger.warning(
            f"{ref_path} does not exist",
            subtype="git_ref",
            location=str(ref_path),
        )
        return None
    return ref_path.read_text().strip()


def form_https_url(
    git_url: str, rev: str, project_path: Path, filepath: Path, lineno: int
) -> str | None:
    parsed_url = parse(git_url)
    template = GIT_HOST_URL_TEMPLATE.get(parsed_url.platform)
    if not template:
        logger.warning(
            f"Unsupported Git host: {parsed_url.platform}",
            subtype="git_host",
        )
        return git_url
    https_url = template.format(
        owner=parsed_url.owner,
        repo=parsed_url.repo,
        rev=rev,
        path=pathname2url(str(filepath.absolute().relative_to(project_path))),
        lineno=str(lineno),
    )
    return https_url


def remove_leading_sequences(text: str, leading_sequences: list[str]) -> str:
    lines = text.splitlines(keepends=True)
    no_comment_lines = []
    for line in lines:
        leading_sequence_exist = False
        for leading_sequence in leading_sequences:
            leading_sequence_idx = line.find(leading_sequence)
            if leading_sequence_idx == -1:
                continue
            no_comment_lines.append(
                line[leading_sequence_idx + len(leading_sequence) :]
            )
            leading_sequence_exist = True
            break

        if not leading_sequence_exist:
            no_comment_lines.append(line)

    return "".join(no_comment_lines)


class ExtractedRstType(TypedDict):
    rst_text: str
    row_offset: int
    start_idx: int
    end_idx: int


# @Extract reStructuredText blocks embedded in comments, IMPL_RST_1, impl, [FE_RST_EXTRACTION]
def extract_rst(
    text: str, start_marker: str, end_marker: str
) -> ExtractedRstType | None:
    """Extract rst from a comment.

    Two use cases:
    1. Start_marker and end_marker one the same line.

    The rst text is wrapped by start and the end markers on the same line,
    so, there is no need to remove the leading chars.ArithmeticError
    E.g.
    @rst  .. admonition:: title here @endrst

    2. Start_marker and end_marker in different lines.

    The rst text is expected to start from the next line of the start_marker
    and ends at he previous line of the end_marker.
    E.g.
    @rst
    .. admonition:: title here
      :collapsible: open

      This example is collapsible, and initially open.
    @endrst
    """
    start_idx = text.find(start_marker)
    end_idx = text.rfind(end_marker)
    if start_idx == -1 or end_idx == -1:
        return None
    rst_text = text[start_idx + len(start_marker) : end_idx]
    row_offset = len(text[:start_idx].splitlines())
    if not rst_text.strip():
        # empty string is out of the interest
        return None
    if UNIX_NEWLINE not in rst_text:
        # single line rst text
        oneline_rst: ExtractedRstType = {
            "rst_text": rst_text,
            "row_offset": row_offset,
            "start_idx": start_idx + len(start_marker),
            "end_idx": end_idx,
        }
        return oneline_rst

    # multiline rst text

    first_newline_idx = rst_text.find(UNIX_NEWLINE)
    rst_text = rst_text[first_newline_idx + len(UNIX_NEWLINE) :]
    multiline_rst: ExtractedRstType = {
        "rst_text": rst_text,
        "row_offset": row_offset,
        "start_idx": start_idx
        + len(start_marker)
        + first_newline_idx
        + len(UNIX_NEWLINE),
        "end_idx": end_idx,
    }

    return multiline_rst
