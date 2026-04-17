from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict

from .fingerprint import fingerprint_query
from .models import Finding, LogEvent, PlanEvent, QueryObservation, SlowQueryEvent
from .parser import extract_plan, extract_slow_query
from .redaction import mask_identity, redact_sql


def analyze_events(events: list[LogEvent], redact_identities: bool = False) -> tuple[list[QueryObservation], list[Finding], dict]:
    slow_by_event: dict[str, SlowQueryEvent] = {}
    plans: list[PlanEvent] = []
    event_by_id = {event.event_id: event for event in events}

    for event in events:
        slow = extract_slow_query(event)
        if slow:
            slow_by_event[event.event_id] = slow
        plan = extract_plan(event)
        if plan:
            plans.append(plan)

    observations: list[QueryObservation] = []
    used_slow: set[str] = set()
    for plan in plans:
        event = event_by_id[plan.event_id]
        slow = _find_matching_slow(plan, event, slow_by_event, event_by_id)
        if slow:
            used_slow.add(slow.event_id)
        query = plan.query_text or (slow.statement if slow else "")
        observations.append(
            _make_observation(
                observation_id=f"Q-{len(observations) + 1:04d}",
                event=event,
                query=query,
                duration_ms=max(plan.duration_ms, slow.duration_ms if slow else 0),
                slow_event_id=slow.event_id if slow else None,
                plan_event_id=plan.event_id,
                plan_signals=plan.summary.signals,
                redact_identities=redact_identities,
            )
        )

    for slow in slow_by_event.values():
        if slow.event_id in used_slow:
            continue
        event = event_by_id[slow.event_id]
        observations.append(
            _make_observation(
                observation_id=f"Q-{len(observations) + 1:04d}",
                event=event,
                query=slow.statement,
                duration_ms=slow.duration_ms,
                slow_event_id=slow.event_id,
                plan_event_id=None,
                plan_signals=[],
                redact_identities=redact_identities,
            )
        )

    findings = build_findings(events, observations)
    summary = build_summary(events, observations, findings)
    return observations, findings, summary


def _find_matching_slow(
    plan: PlanEvent,
    plan_event: LogEvent,
    slow_by_event: dict[str, SlowQueryEvent],
    event_by_id: dict[str, LogEvent],
) -> SlowQueryEvent | None:
    plan_fp = fingerprint_query(plan.query_text)
    best: SlowQueryEvent | None = None
    best_distance = 999999
    for slow in slow_by_event.values():
        slow_event = event_by_id[slow.event_id]
        if slow_event.pid != plan_event.pid:
            continue
        if fingerprint_query(slow.statement) != plan_fp:
            continue
        distance = abs(slow_event.start_line - plan_event.start_line)
        if distance < best_distance:
            best = slow
            best_distance = distance
    return best


def _make_observation(
    observation_id: str,
    event: LogEvent,
    query: str,
    duration_ms: float,
    slow_event_id: str | None,
    plan_event_id: str | None,
    plan_signals: list,
    redact_identities: bool,
) -> QueryObservation:
    user = mask_identity(event.user) if redact_identities else event.user
    database = mask_identity(event.database) if redact_identities else event.database
    application = mask_identity(event.application) if redact_identities else event.application
    return QueryObservation(
        observation_id=observation_id,
        fingerprint=fingerprint_query(query),
        representative_query=query,
        redacted_query=redact_sql(query),
        duration_ms=duration_ms,
        timestamp=event.timestamp,
        pid=event.pid,
        user=user,
        database=database,
        application=application,
        slow_event_id=slow_event_id,
        plan_event_id=plan_event_id,
        plan_signals=plan_signals,
        source=event.source,
        start_line=event.start_line,
        end_line=event.end_line,
    )


def build_findings(events: list[LogEvent], observations: list[QueryObservation]) -> list[Finding]:
    findings: list[Finding] = []
    operational = _operational_findings(events)
    findings.extend(operational)
    findings.extend(_query_findings(observations, start_index=len(findings) + 1))
    return findings


def _operational_findings(events: list[LogEvent]) -> list[Finding]:
    findings: list[Finding] = []
    severity_counts = Counter(event.severity for event in events)
    bad_events = [event for event in events if event.severity in {"ERROR", "FATAL", "PANIC"}]
    if bad_events:
        findings.append(
            Finding(
                finding_id="F-0001",
                severity="high" if severity_counts.get("FATAL", 0) or severity_counts.get("PANIC", 0) else "medium",
                title="Error severity events were found",
                description=f"Found {len(bad_events)} ERROR/FATAL/PANIC log events.",
                evidence={"severity_counts": dict(severity_counts), "sample_event_ids": [event.event_id for event in bad_events[:5]]},
                recommendation="Review repeated errors first, especially if they align with slow query windows.",
            )
        )

    patterns = {
        "lock_wait": "still waiting for",
        "deadlock": "deadlock detected",
        "statement_timeout": "canceling statement due to statement timeout",
        "temp_file": "temporary file:",
        "checkpoint": "checkpoint",
        "autovacuum": "autovacuum",
    }
    for index, (kind, pattern) in enumerate(patterns.items(), start=len(findings) + 1):
        matched = [event for event in events if pattern in event.message.lower()]
        if not matched:
            continue
        severity = "high" if kind in {"deadlock", "statement_timeout"} else "medium"
        findings.append(
            Finding(
                finding_id=f"F-{index:04d}",
                severity=severity,
                title=f"Operational signal detected: {kind}",
                description=f"Found {len(matched)} log events matching '{pattern}'.",
                evidence={"kind": kind, "count": len(matched), "sample_event_ids": [event.event_id for event in matched[:5]]},
                recommendation="Inspect the event timestamps and related sessions to confirm operational impact.",
            )
        )
    return findings


def _query_findings(observations: list[QueryObservation], start_index: int) -> list[Finding]:
    groups: dict[str, list[QueryObservation]] = defaultdict(list)
    for obs in observations:
        groups[obs.fingerprint].append(obs)

    scored = []
    for fingerprint, items in groups.items():
        total_ms = sum(item.duration_ms for item in items)
        max_ms = max(item.duration_ms for item in items)
        signal_count = sum(len(item.plan_signals) for item in items)
        score = total_ms / 1000 + max_ms / 1000 + len(items) * 2 + signal_count * 10
        scored.append((score, fingerprint, items, total_ms, max_ms, signal_count))
    scored.sort(reverse=True, key=lambda row: row[0])

    findings: list[Finding] = []
    for offset, (score, fingerprint, items, total_ms, max_ms, signal_count) in enumerate(scored[:10], start=start_index):
        sample = max(items, key=lambda item: item.duration_ms)
        severity = "high" if score >= 60 or max_ms >= 3000 or signal_count >= 2 else "medium"
        signal_kinds = Counter(signal.kind for item in items for signal in item.plan_signals)
        findings.append(
            Finding(
                finding_id=f"F-{offset:04d}",
                severity=severity,
                title="Query optimization candidate",
                description=(
                    f"Fingerprint appeared {len(items)} times, total duration {total_ms:.1f} ms, "
                    f"max duration {max_ms:.1f} ms."
                ),
                evidence={
                    "fingerprint": fingerprint,
                    "observation_ids": [item.observation_id for item in items[:10]],
                    "calls": len(items),
                    "total_duration_ms": round(total_ms, 3),
                    "max_duration_ms": round(max_ms, 3),
                    "score": round(score, 3),
                    "signal_kinds": dict(signal_kinds),
                    "sample_query": sample.redacted_query,
                },
                recommendation=_recommend_for_signals(signal_kinds),
            )
        )
    return findings


def _recommend_for_signals(signal_kinds: Counter) -> str:
    if signal_kinds.get("large_seq_scan") or signal_kinds.get("high_rows_removed"):
        return "Check filter selectivity and candidate indexes for the referenced tables."
    if signal_kinds.get("external_sort"):
        return "Review ORDER BY/GROUP BY patterns, candidate indexes, and work_mem for this workload."
    if signal_kinds.get("high_loop_nested_loop"):
        return "Inspect join predicates and indexes on the inner side of the nested loop."
    if signal_kinds.get("hash_batches"):
        return "Check hash memory pressure and work_mem for this query shape."
    return "Review the query plan and validate with fresh EXPLAIN (ANALYZE, BUFFERS)."


def build_summary(events: list[LogEvent], observations: list[QueryObservation], findings: list[Finding]) -> dict:
    app_counts = Counter(obs.application or "<unknown>" for obs in observations)
    db_counts = Counter(obs.database or "<unknown>" for obs in observations)
    return {
        "total_log_events": len(events),
        "query_observations": len(observations),
        "findings": len(findings),
        "severity_counts": dict(Counter(finding.severity for finding in findings)),
        "applications": dict(app_counts.most_common(10)),
        "databases": dict(db_counts.most_common(10)),
    }


def evidence_bundle(observations: list[QueryObservation], findings: list[Finding], summary: dict) -> dict:
    return {
        "summary": summary,
        "top_findings": [finding.to_dict() for finding in findings[:20]],
        "top_observations": [
            obs.to_dict()
            for obs in sorted(observations, key=lambda item: item.duration_ms, reverse=True)[:20]
        ],
    }

