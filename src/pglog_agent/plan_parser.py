from __future__ import annotations

import re
from collections import Counter

from .models import PlanSignal, PlanSummary

NODE_RE = re.compile(
    r"(?:->\s*)?(?P<node>[A-Z][A-Za-z ]+?)(?:\s+on\s+(?P<relation>[A-Za-z0-9_\.]+))?\s+"
    r"(?:[A-Za-z_][A-Za-z0-9_]*\s+)?"
    r"\(cost=.*?rows=(?P<est_rows>\d+).*?\)\s+"
    r"\(actual(?:\s+time=[^\)]*?)?\s+rows=(?P<actual_rows>\d+)\s+loops=(?P<loops>\d+)\)"
)
BUFFERS_RE = re.compile(r"Buffers:\s+(?P<body>.*)")
BUFFER_VALUE_RE = re.compile(r"(hit|read|dirtied|written)=(\d+)")
ROWS_REMOVED_RE = re.compile(r"Rows Removed by Filter:\s+(?P<count>\d+)")
BATCHES_RE = re.compile(r"Batches:\s+(?P<count>\d+)")
SORT_METHOD_RE = re.compile(r"Sort Method:\s+(?P<method>.+)")


def parse_plan_text(plan_text: str) -> PlanSummary:
    summary = PlanSummary()
    buffer_totals: Counter[str] = Counter()

    for raw_line in plan_text.splitlines():
        line = raw_line.strip()
        node_match = NODE_RE.search(line)
        if node_match:
            node = {
                "node_type": node_match.group("node").strip(),
                "relation": node_match.group("relation"),
                "estimated_rows": int(node_match.group("est_rows")),
                "actual_rows": int(node_match.group("actual_rows")),
                "loops": int(node_match.group("loops")),
            }
            summary.nodes.append(node)
            _add_node_signals(summary, node)

        buffers_match = BUFFERS_RE.search(line)
        if buffers_match:
            for key, value in BUFFER_VALUE_RE.findall(buffers_match.group("body")):
                buffer_totals[f"shared_{key}"] += int(value)

        rows_removed_match = ROWS_REMOVED_RE.search(line)
        if rows_removed_match:
            removed = int(rows_removed_match.group("count"))
            summary.rows_removed_by_filter += removed
            if removed >= 100000:
                summary.signals.append(
                    PlanSignal(
                        kind="high_rows_removed",
                        severity="high",
                        message=f"Rows Removed by Filter is high ({removed}).",
                        evidence={"rows_removed_by_filter": removed},
                    )
                )

        batches_match = BATCHES_RE.search(line)
        if batches_match:
            batches = int(batches_match.group("count"))
            if batches > 1:
                summary.signals.append(
                    PlanSignal(
                        kind="hash_batches",
                        severity="medium",
                        message=f"Hash operation used {batches} batches; memory may be tight.",
                        evidence={"batches": batches},
                    )
                )

        sort_match = SORT_METHOD_RE.search(line)
        if sort_match:
            method = sort_match.group("method")
            if "external" in method.lower() or "Disk:" in method:
                summary.signals.append(
                    PlanSignal(
                        kind="external_sort",
                        severity="high",
                        message=f"Sort spilled to disk ({method}).",
                        evidence={"sort_method": method},
                    )
                )

        if line.startswith("Settings:"):
            summary.settings = line.removeprefix("Settings:").strip()

    summary.buffers = dict(buffer_totals)
    if buffer_totals.get("shared_read", 0) >= 1000:
        summary.signals.append(
            PlanSignal(
                kind="read_heavy",
                severity="medium",
                message=f"Plan read {buffer_totals['shared_read']} shared buffers.",
                evidence={"shared_read": buffer_totals["shared_read"]},
            )
        )
    return summary


def _add_node_signals(summary: PlanSummary, node: dict) -> None:
    node_type = node["node_type"]
    actual_rows = node["actual_rows"]
    loops = node["loops"]
    est_rows = node["estimated_rows"]
    relation = node.get("relation")

    if node_type == "Seq Scan" and actual_rows >= 100000:
        summary.signals.append(
            PlanSignal(
                kind="large_seq_scan",
                severity="high",
                message=f"Large sequential scan on {relation or 'unknown relation'} ({actual_rows} rows).",
                evidence=node,
            )
        )
    if node_type == "Nested Loop" and loops >= 1000:
        summary.signals.append(
            PlanSignal(
                kind="high_loop_nested_loop",
                severity="high",
                message=f"Nested Loop executed many loops ({loops}).",
                evidence=node,
            )
        )
    if est_rows > 0:
        ratio = max(actual_rows / est_rows, est_rows / max(actual_rows, 1))
        if ratio >= 20 and actual_rows >= 1000:
            summary.signals.append(
                PlanSignal(
                    kind="row_estimate_mismatch",
                    severity="medium",
                    message=f"Estimated rows and actual rows differ by {ratio:.1f}x.",
                    evidence=node | {"estimate_ratio": round(ratio, 2)},
                )
            )
