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
    prompt = (
        "You are analyzing PostgreSQL slow query and auto_explain findings. "
        "Use only the supplied evidence. Write a concise Korean DBA report summary "
        "with operational risks, query optimization priorities, and next actions.\n\n"
        f"Evidence JSON:\n{json.dumps(evidence, ensure_ascii=False)}"
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

