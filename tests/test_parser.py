import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from pglog_agent.analyzer import analyze_events
from pglog_agent.parser import parse_file


class ParserTests(unittest.TestCase):
    def test_basic_slow_and_plan(self):
        events, errors = parse_file(ROOT / "samples" / "pg13_text_basic.log")
        self.assertEqual(errors, [])
        observations, findings, summary = analyze_events(events)
        self.assertEqual(summary["query_observations"], 1)
        self.assertTrue(any(f.title == "Query optimization candidate" for f in findings))

    def test_multiline_query(self):
        events, errors = parse_file(ROOT / "samples" / "pg13_text_multiline_query.log")
        self.assertEqual(errors, [])
        observations, _, _ = analyze_events(events)
        self.assertEqual(len(observations), 1)
        self.assertIn("GROUP BY", observations[0].representative_query)

    def test_operational_findings(self):
        events, errors = parse_file(ROOT / "samples" / "pg13_text_errors_locks.log")
        self.assertEqual(errors, [])
        _, findings, _ = analyze_events(events)
        titles = [finding.title for finding in findings]
        self.assertTrue(any("Error severity" in title for title in titles))
        self.assertTrue(any("deadlock" in finding.evidence.get("kind", "") for finding in findings))

    def test_plan_node_with_alias_is_parsed(self):
        events, errors = parse_file(ROOT / "samples" / "pg13_text_basic.log")
        self.assertEqual(errors, [])
        observations, _, _ = analyze_events(events)
        kinds = {signal.kind for signal in observations[0].plan_signals}
        self.assertIn("high_rows_removed", kinds)


if __name__ == "__main__":
    unittest.main()
