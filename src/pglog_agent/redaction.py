from __future__ import annotations

import re

_SINGLE_QUOTED = re.compile(r"'(?:''|[^'])*'")
_DOLLAR_QUOTED = re.compile(r"\$[A-Za-z_][A-Za-z0-9_]*\$.*?\$[A-Za-z_][A-Za-z0-9_]*\$", re.DOTALL)
_NUMERIC = re.compile(r"(?<![A-Za-z_])\b\d+(?:\.\d+)?\b(?![A-Za-z_])")
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TOKEN = re.compile(r"\b[A-Fa-f0-9]{24,}\b")


def redact_sql(sql: str, max_length: int = 2000) -> str:
    text = _DOLLAR_QUOTED.sub("'?'", sql)
    text = _SINGLE_QUOTED.sub("'?'", text)
    text = _EMAIL.sub("?", text)
    text = _TOKEN.sub("?", text)
    text = _NUMERIC.sub("?", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_length:
        return text[: max_length - 15].rstrip() + " ...[truncated]"
    return text


def mask_identity(value: str | None) -> str | None:
    if not value:
        return value
    return "<redacted>"

