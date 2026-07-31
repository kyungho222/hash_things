"""Minimal database-backed SimHash duplicate checker.

Only a boolean is returned to callers so crawlers do not need to know how a
document hash is stored or compared.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Protocol

from simhash import Simhash


class Cursor(Protocol):
    def execute(self, query: str, params: tuple[Any, ...]) -> Any: ...
    def fetchone(self) -> Any: ...
    def close(self) -> Any: ...


class Connection(Protocol):
    def cursor(self) -> Cursor: ...


def make_simhash(title: str, content: str) -> int:
    """Create a deterministic 64-bit SimHash with the simhash library."""
    text = _normalize(f"{title}\n---CONTENT---\n{content}")
    return Simhash(text, f=64).value

def has_simhash_match(
    connection: Connection,
    simhash: int,
    *,
    table: str,
    column: str = "simhash",
) -> bool:
    """Return only whether an exact SimHash value exists in one DB column.

    ``table`` and ``column`` must be application-defined constant identifiers,
    not request/user input. DB-API parameters safely bind the hash value.
    """
    _validate_identifier(table)
    _validate_identifier(column)
    cursor = connection.cursor()
    try:
        cursor.execute(
            f"SELECT 1 FROM {table} WHERE {column} = %s LIMIT 1",
            (format_simhash(simhash),),
        )
        return cursor.fetchone() is not None
    finally:
        cursor.close()


def format_simhash(simhash: int) -> str:
    """Store/compare SimHash consistently as a zero-padded 16-char hex value."""
    return f"{simhash & ((1 << 64) - 1):016x}"


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()


def _validate_identifier(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("table and column must be safe SQL identifiers")

