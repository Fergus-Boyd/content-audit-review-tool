"""Compiles a parsed search query (see ``search.parser``) to SQL.

A query becomes one WHERE fragment plus a params dict, against a single
text column. Each line ANDs together; within a line, brackets/`|` expand to
a small set of literal alternative phrases that OR together - so
``import (licence | permit)`` becomes the two literal phrases
``import licence`` and ``import permit`` (concatenation is real string
concatenation, not "these two conditions both hold somewhere"). A `NOT`
line's OR-chain is wrapped in ``NOT COALESCE((...), FALSE)`` so a NULL/empty
column counts as "does not match", not as an automatic pass - without that a
NOT search would silently drop every row with nothing in the column.

No Streamlit or database import here, so this is testable with plain
strings and a plain dict.
"""

from __future__ import annotations

from ..params import ParamGenerator
from .parser import Concat, Line, Or, Phrase, QueryError, parse

__all__ = ["compile_query", "QueryError"]


def _expand(node) -> list[str]:
    """Every literal phrase this node can mean, as plain strings."""
    if isinstance(node, Phrase):
        return [node.text]
    if isinstance(node, Or):
        options: list[str] = []
        for branch in node.branches:
            options.extend(_expand(branch))
        return options
    if isinstance(node, Concat):
        combos = [""]
        for part in node.parts:
            part_options = _expand(part)
            combos = [
                f"{combo} {option}".strip()
                for combo in combos
                for option in part_options
            ]
        return combos
    raise TypeError(f"Unexpected search AST node: {node!r}")


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _compile_line(line: Line, column: str, pig: ParamGenerator) -> tuple[str, dict]:
    alternatives = _expand(line.expr)
    clauses = []
    params: dict = {}
    for alt in alternatives:
        p = pig.generate()
        params[p] = f"%{_escape_like(alt.lower())}%"
        clauses.append(f"LOWER({column}) LIKE :{p} ESCAPE '\\'")
    fragment = "(" + " OR ".join(clauses) + ")"
    if line.negated:
        fragment = f"NOT COALESCE({fragment}, FALSE)"
    return fragment, params


def compile_query(
    column: str, query: str, pig: ParamGenerator
) -> tuple[str | None, dict]:
    """Compile a search box's whole query against ``column``.

    Returns ``(None, {})`` for a blank query, so the caller knows not to add
    a WHERE clause at all. Raises ``QueryError`` (from ``search.parser``) if
    the query cannot be understood - callers should show that message next
    to the search box and leave the applied scope unchanged.
    """
    lines = parse(query)
    if not lines:
        return None, {}

    fragments = []
    all_params: dict = {}
    for line in lines:
        fragment, params = _compile_line(line, column, pig)
        fragments.append(fragment)
        all_params.update(params)
    return " AND ".join(fragments), all_params
