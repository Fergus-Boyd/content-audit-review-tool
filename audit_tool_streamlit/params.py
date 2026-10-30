"""A tiny named-SQL-parameter counter, shared by every query builder.

Has no ``streamlit``/``dwutils`` import, so pure-Python query builders
(``search.compiler``, ``scope``) stay unit-testable without those packages
installed.

One instance must be shared across every piece of SQL going into the same
query, so that param names never collide.
"""

from __future__ import annotations


class ParamGenerator:
    def __init__(self, label: str = "p_"):
        self.i = 0
        self.label = label

    def generate(self) -> str:
        name = f"{self.label}{self.i}"
        self.i += 1
        return name

    def multi_generate(self, n: int) -> list[str]:
        return [self.generate() for _ in range(n)]
