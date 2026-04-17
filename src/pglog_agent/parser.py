from __future__ import annotations

import re
from pathlib import Path

from .models import LogEvent, PlanEvent, SlowQueryEvent
from .plan_parser import parse_plan_text

LOG_START_RE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3} \S+)\s+"
    r"\[(?P<pid>\d+)\]\s+"
    r"(?:(?P<identity>\S+)\s+)?"
    r"(?P<severity>[A-Z]+):\s{1,2}(?P<message>.*)$"
)

DURATION_MESSAGE_RE = re.compile(
    r"duration:\s+(?P<duration>\d+(?:\.\d+)?)\s+ms\s+(?P<kind>statement|execute(?:\s+[^:]+)?|plan):\s*(?P<body>.*)",
    re.DOTALL,
)


class ParseIssue(Exception):
    pass


def discover_log_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in {"", ".log", ".txt"})


def parse_paths(paths: list[Path]) -> tuple[list[LogEvent], list[dict]]:
    events: list[LogEvent] = []
    errors: list[dict] = []
    for path in paths:
        parsed, file_errors = parse_file(path)
        events.extend(parsed)
        errors.extend(file_errors)
    return events, errors


def parse_file(path: Path) -> tuple[list[LogEvent], list[dict]]:
    events: list[LogEvent] = []
    errors: list[dict] = []
    current: list[str] = []
    current_start = 0

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.rstrip("\n")
            if LOG_START_RE.match(line):
                if current:
                    _append_event(events, errors, path, current_start, line_no - 1, current)
                current = [line]
                current_start = line_no
            elif current:
                current.append(line)
            elif line.strip():
                errors.append(
                    {
                        "source": str(path),
                        "line": line_no,
                        "error": "orphan_continuation",
                        "raw": line,
                    }
                )

    if current:
        _append_event(events, errors, path, current_start, current_start + len(current) - 1, current)

    return events, errors


def _append_event(
    events: list[LogEvent],
    errors: list[dict],
    path: Path,
    start_line: int,
    end_line: int,
    lines: list[str],
) -> None:
    match = LOG_START_RE.match(lines[0])
    if not match:
        errors.append(
            {
                "source": str(path),
                "line": start_line,
                "error": "invalid_event_start",
                "raw": lines[0],
            }
        )
        return
    message = match.group("message")
    if len(lines) > 1:
        message += "\n" + "\n".join(lines[1:])
    user, database, application = _parse_identity(match.group("identity"))
    events.append(
        LogEvent(
            event_id=f"{path.name}:{start_line}",
            source=str(path),
            start_line=start_line,
            end_line=end_line,
            timestamp=match.group("timestamp"),
            pid=int(match.group("pid")) if match.group("pid") else None,
            user=user,
            database=database,
            application=application,
            severity=match.group("severity"),
            message=message,
            raw_lines=lines,
        )
    )


def _parse_identity(identity: str | None) -> tuple[str | None, str | None, str | None]:
    if not identity or "@" not in identity:
        return None, None, None
    user, rest = identity.split("@", 1)
    if "/" in rest:
        database, application = rest.split("/", 1)
    else:
        database, application = rest, None
    return user or None, database or None, application or None


def extract_slow_query(event: LogEvent) -> SlowQueryEvent | None:
    match = DURATION_MESSAGE_RE.search(event.message)
    if not match:
        return None
    kind = match.group("kind")
    if kind == "plan":
        return None
    return SlowQueryEvent(
        event_id=event.event_id,
        duration_ms=float(match.group("duration")),
        statement=match.group("body").strip(),
    )


def extract_plan(event: LogEvent) -> PlanEvent | None:
    match = DURATION_MESSAGE_RE.search(event.message)
    if not match or match.group("kind") != "plan":
        return None
    duration_ms = float(match.group("duration"))
    body = match.group("body")
    query_text, plan_text = split_query_and_plan(body)
    summary = parse_plan_text(plan_text)
    return PlanEvent(
        event_id=event.event_id,
        duration_ms=duration_ms,
        query_text=query_text.strip(),
        plan_text=plan_text.strip(),
        summary=summary,
    )


def split_query_and_plan(body: str) -> tuple[str, str]:
    lines = body.splitlines()
    query_lines: list[str] = []
    plan_lines: list[str] = []
    in_query = False
    in_plan = False

    for line in lines:
        stripped = line.strip()
        if stripped.startswith("Query Text:"):
            in_query = True
            in_plan = False
            query_lines.append(stripped.removeprefix("Query Text:").strip())
            continue
        if in_query and _looks_like_plan_line(stripped):
            in_query = False
            in_plan = True
        if in_plan:
            plan_lines.append(line)
        elif in_query:
            query_lines.append(stripped)

    if not query_lines:
        return "", body
    return "\n".join(part for part in query_lines if part), "\n".join(plan_lines)


def _looks_like_plan_line(line: str) -> bool:
    return bool(
        re.match(r"(?:->\s*)?[A-Z][A-Za-z ]+\s+\(", line)
        or line.startswith("Settings:")
        or line.startswith("CTE ")
    )
