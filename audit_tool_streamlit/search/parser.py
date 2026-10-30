"""A small search mini-language for the free-text search boxes.

Rules (see also ``definitions.search_help_markdown``):

- One rule per line. Every line must match (lines are ANDed).
- ``NOT`` at the start of a line means that line's condition must NOT match.
- ``a | b`` is either: it matches if the text contains ``a`` or ``b``.
- Brackets group an either/or: ``import (licence | permit)`` matches
  ``import licence`` or ``import permit``. Without brackets, ``|`` splits the
  whole line.
- A phrase is typed as it is, with no quote marks, and must appear exactly.
  Matching happens inside words: ``licen`` finds ``licence`` and
  ``licensing``.

This module only builds the parse tree (``Line`` / ``Phrase`` / ``Or`` /
``Concat``); turning it into SQL is ``search.compiler``. No Streamlit or
database import here, so it is plain-dictionary/plain-string testable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(r"\(|\)|\||[^()|]+")


class QueryError(ValueError):
    """A query the user typed could not be understood."""


@dataclass(frozen=True)
class Phrase:
    text: str


@dataclass(frozen=True)
class Or:
    branches: tuple


@dataclass(frozen=True)
class Concat:
    parts: tuple


@dataclass(frozen=True)
class Line:
    """One line of the query. ``expr`` is a Phrase/Or/Concat tree."""

    expr: object
    negated: bool


def _tokenize(line: str) -> list[str]:
    return _TOKEN_RE.findall(line)


def _parse_atom(tokens: list[str], pos: int, line_no: int):
    tok = tokens[pos]
    if tok == "(":
        node, pos = _parse_alt(tokens, pos + 1, line_no)
        if pos >= len(tokens) or tokens[pos] != ")":
            raise QueryError(f"Line {line_no}: this bracket is never closed.")
        return node, pos + 1
    if tok == ")":
        raise QueryError(f"Line {line_no}: this bracket is never opened.")
    text = tok.strip()
    return Phrase(text), pos + 1


def _parse_seq(tokens: list[str], pos: int, line_no: int):
    parts = []
    while pos < len(tokens) and tokens[pos] not in ("|", ")"):
        atom, pos = _parse_atom(tokens, pos, line_no)
        parts.append(atom)
    if not parts:
        raise QueryError(
            f"Line {line_no}: there should be a word or phrase here, "
            "not a bracket or |."
        )
    if len(parts) == 1:
        return parts[0], pos
    return Concat(tuple(parts)), pos


def _parse_alt(tokens: list[str], pos: int, line_no: int):
    first, pos = _parse_seq(tokens, pos, line_no)
    branches = [first]
    while pos < len(tokens) and tokens[pos] == "|":
        nxt, pos = _parse_seq(tokens, pos + 1, line_no)
        branches.append(nxt)
    if len(branches) == 1:
        return branches[0], pos
    return Or(tuple(branches)), pos


def _parse_line(raw: str, line_no: int) -> Line:
    stripped = raw.strip()
    negated = False
    if stripped[:3].upper() == "NOT" and (len(stripped) == 3 or stripped[3].isspace()):
        negated = True
        stripped = stripped[3:].strip()

    tokens = [t for t in _tokenize(stripped) if t.strip() or t in ("(", ")", "|")]
    # Phrase tokens keep their trimmed text; bracket/pipe tokens pass through.
    tokens = [t if t in ("(", ")", "|") else t.strip() for t in tokens]
    if not tokens:
        raise QueryError(f"Line {line_no}: NOT needs something after it.")

    node, pos = _parse_alt(tokens, 0, line_no)
    if pos != len(tokens):
        raise QueryError(f"Line {line_no}: this bracket is never opened.")
    return Line(node, negated)


def parse(query: str) -> list[Line]:
    """Parse a whole search box into one ``Line`` per non-blank line.

    Blank lines are ignored rather than treated as an empty rule. Raises
    ``QueryError`` with a plain-English message naming the offending line.
    """
    lines: list[Line] = []
    for i, raw in enumerate((query or "").split("\n"), start=1):
        if not raw.strip():
            continue
        lines.append(_parse_line(raw, i))
    return lines
