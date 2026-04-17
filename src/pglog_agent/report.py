from __future__ import annotations

from collections import Counter

from .models import Finding, QueryObservation


def render_report(
    observations: list[QueryObservation],
    findings: list[Finding],
    summary: dict,
    llm_summary: str | None = None,
) -> str:
    lines: list[str] = []
    lines.append("# PostgreSQL Log Analysis Report")
    lines.append("")
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(f"- Total log events parsed: {summary['total_log_events']}")
    lines.append(f"- Query observations: {summary['query_observations']}")
    lines.append(f"- Findings: {summary['findings']}")
    lines.append(f"- Finding severities: {summary['severity_counts']}")
    lines.append("")
    if llm_summary:
        lines.append("## Local LLM Summary")
        lines.append("")
        lines.append(llm_summary.strip())
        lines.append("")
    lines.append("## Operational Risk Findings")
    lines.append("")
    operational = [finding for finding in findings if "Operational signal" in finding.title or "Error severity" in finding.title]
    if operational:
        for finding in operational:
            _append_finding(lines, finding)
    else:
        lines.append("No operational risk findings were detected from the parsed log sample.")
        lines.append("")

    lines.append("## Query Optimization Candidates")
    lines.append("")
    query_findings = [finding for finding in findings if finding.title == "Query optimization candidate"]
    if query_findings:
        for finding in query_findings[:10]:
            _append_finding(lines, finding)
    else:
        lines.append("No query optimization candidates were detected.")
        lines.append("")

    lines.append("## Workload Breakdown")
    lines.append("")
    lines.append("### Applications")
    lines.append("")
    for name, count in summary["applications"].items():
        lines.append(f"- `{name}`: {count} observations")
    lines.append("")
    lines.append("### Databases")
    lines.append("")
    for name, count in summary["databases"].items():
        lines.append(f"- `{name}`: {count} observations")
    lines.append("")

    lines.append("## Slowest Observations")
    lines.append("")
    for obs in sorted(observations, key=lambda item: item.duration_ms, reverse=True)[:10]:
        signal_counts = Counter(signal.kind for signal in obs.plan_signals)
        lines.append(f"### {obs.observation_id} - {obs.duration_ms:.3f} ms")
        lines.append("")
        lines.append(f"- Application: `{obs.application or '<unknown>'}`")
        lines.append(f"- Database: `{obs.database or '<unknown>'}`")
        lines.append(f"- User: `{obs.user or '<unknown>'}`")
        lines.append(f"- Source: `{obs.source}:{obs.start_line}`")
        lines.append(f"- Plan signals: {dict(signal_counts)}")
        lines.append("")
        lines.append("```sql")
        lines.append(obs.redacted_query)
        lines.append("```")
        lines.append("")

    lines.append("## Limits")
    lines.append("")
    lines.append("- Queries below the configured logging thresholds are not visible in this report.")
    lines.append("- CPU, disk, and network bottlenecks cannot be confirmed from PostgreSQL logs alone.")
    lines.append("- Index recommendations are candidates unless schema, cardinality, and fresh plans are provided.")
    lines.append("- `auto_explain.log_analyze = on` can add runtime overhead to logged workloads.")
    lines.append("")
    return "\n".join(lines)


def _append_finding(lines: list[str], finding: Finding) -> None:
    lines.append(f"### {finding.finding_id} - {finding.title}")
    lines.append("")
    lines.append(f"- Severity: `{finding.severity}`")
    lines.append(f"- Description: {finding.description}")
    lines.append(f"- Recommendation: {finding.recommendation}")
    lines.append("")
    lines.append("Evidence:")
    lines.append("")
    lines.append("```json")
    lines.append(_compact_json_like(finding.evidence))
    lines.append("```")
    lines.append("")


def _compact_json_like(value: object) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, indent=2)

