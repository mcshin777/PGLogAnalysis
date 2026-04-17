# pglog-agent

PostgreSQL 13 text log analyzer for slow query logs and `auto_explain` text plans.

The MVP focuses on logs generated with:

```conf
log_min_duration_statement = '3s'
auto_explain.log_min_duration = '1s'
auto_explain.log_format = 'text'
auto_explain.log_analyze = on
auto_explain.log_buffers = on
```

## Usage

From a source checkout:

```bash
$env:PYTHONPATH="src"
python -m pglog_agent analyze ./samples --output ./out
```

After editable install:

```bash
python -m pip install -e .
pglog-agent analyze ./samples --output ./out
```

Optional LM Studio summary:

```bash
pglog-agent analyze ./samples --output ./out --llm lmstudio
```

Outputs:

- `report.md`
- `findings.json`
- `observations.jsonl`
- `parse_errors.jsonl`
