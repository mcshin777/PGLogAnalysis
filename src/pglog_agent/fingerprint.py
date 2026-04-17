from __future__ import annotations

import re

from .redaction import redact_sql

_COMMENTS = re.compile(r"(--[^\n]*|/\*.*?\*/)", re.DOTALL)


def fingerprint_query(sql: str) -> str:
    text = _COMMENTS.sub(" ", sql)
    text = redact_sql(text, max_length=4000)
    text = text.lower()
    text = re.sub(r"\$\d+", "?", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text

