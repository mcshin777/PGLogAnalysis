from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LogEvent:
    event_id: str
    source: str
    start_line: int
    end_line: int
    timestamp: str
    pid: int | None
    user: str | None
    database: str | None
    application: str | None
    severity: str
    message: str
    raw_lines: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SlowQueryEvent:
    event_id: str
    duration_ms: float
    statement: str
    parameters: str | None = None


@dataclass
class PlanSignal:
    kind: str
    severity: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlanSummary:
    nodes: list[dict[str, Any]] = field(default_factory=list)
    signals: list[PlanSignal] = field(default_factory=list)
    buffers: dict[str, int] = field(default_factory=dict)
    rows_removed_by_filter: int = 0
    settings: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": self.nodes,
            "signals": [asdict(signal) for signal in self.signals],
            "buffers": self.buffers,
            "rows_removed_by_filter": self.rows_removed_by_filter,
            "settings": self.settings,
        }


@dataclass
class PlanEvent:
    event_id: str
    duration_ms: float
    query_text: str
    plan_text: str
    summary: PlanSummary


@dataclass
class QueryObservation:
    observation_id: str
    fingerprint: str
    representative_query: str
    redacted_query: str
    duration_ms: float
    timestamp: str
    pid: int | None
    user: str | None
    database: str | None
    application: str | None
    slow_event_id: str | None = None
    plan_event_id: str | None = None
    plan_signals: list[PlanSignal] = field(default_factory=list)
    source: str | None = None
    start_line: int | None = None
    end_line: int | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["plan_signals"] = [asdict(signal) for signal in self.plan_signals]
        return data


@dataclass
class Finding:
    finding_id: str
    severity: str
    title: str
    description: str
    evidence: dict[str, Any]
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

