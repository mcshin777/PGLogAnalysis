from __future__ import annotations

import json
import urllib.error
import urllib.request


def summarize_with_lmstudio(
    evidence: dict,
    base_url: str = "http://localhost:1234/v1",
    model: str = "google/gemma-4-e4b",
    timeout: int = 60,
) -> str:
    endpoint = base_url.rstrip("/") + "/chat/completions"
    compact_evidence = _compact_evidence(evidence)
    prompt = (
        "You are analyzing PostgreSQL slow query and auto_explain findings. "
        "Use only the supplied evidence. Write a concise Korean DBA report summary "
        "with operational risks, query optimization priorities, and next actions.\n\n"
        f"Evidence JSON:\n{json.dumps(compact_evidence, ensure_ascii=False)}"
    )
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "Use only evidence. Do not invent metrics."},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"LM Studio request failed: {exc}") from exc
    return payload["choices"][0]["message"]["content"]


def _compact_evidence(evidence: dict, max_findings: int = 8, max_observations: int = 6) -> dict:
    """Keep local-model prompts small enough for 4K-ish context windows."""
    return {
        "summary": evidence.get("summary", {}),
        "top_findings": [_compact_finding(item) for item in evidence.get("top_findings", [])[:max_findings]],
        "top_observations": [
            _compact_observation(item) for item in evidence.get("top_observations", [])[:max_observations]
        ],
    }


def _compact_finding(finding: dict) -> dict:
    evidence = finding.get("evidence", {})
    return {
        "id": finding.get("finding_id"),
        "severity": finding.get("severity"),
        "title": finding.get("title"),
        "description": _truncate(finding.get("description", ""), 240),
        "recommendation": _truncate(finding.get("recommendation", ""), 240),
        "evidence": {
            "calls": evidence.get("calls"),
            "total_duration_ms": evidence.get("total_duration_ms"),
            "max_duration_ms": evidence.get("max_duration_ms"),
            "score": evidence.get("score"),
            "signal_kinds": evidence.get("signal_kinds"),
            "kind": evidence.get("kind"),
            "count": evidence.get("count"),
            "severity_counts": evidence.get("severity_counts"),
            "sample_query": _truncate(evidence.get("sample_query", ""), 320),
        },
    }


def _compact_observation(observation: dict) -> dict:
    return {
        "id": observation.get("observation_id"),
        "duration_ms": observation.get("duration_ms"),
        "application": observation.get("application"),
        "database": observation.get("database"),
        "user": observation.get("user"),
        "redacted_query": _truncate(observation.get("redacted_query", ""), 320),
        "plan_signals": [
            {
                "kind": signal.get("kind"),
                "severity": signal.get("severity"),
                "message": _truncate(signal.get("message", ""), 160),
            }
            for signal in observation.get("plan_signals", [])[:5]
        ],
    }


def _truncate(value: object, limit: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 15].rstrip() + " ...[truncated]"
