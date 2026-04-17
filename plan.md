# PostgreSQL Log Analysis Agent Plan

## 1. Project Goal

PostgreSQL server log files를 분석하는 Agent를 만든다.

Primary target은 PostgreSQL 13이며, 가능하면 PostgreSQL 14+ 및 PostgreSQL 15+의 구조화 로그까지 확장 가능하게 설계한다.

초기 분석 대상은 다음 로그다.

- Slow query log
- `auto_explain` 기반 실행 계획 로그
- 일반 stderr 로그
- CSV 로그
- 향후 PostgreSQL 15+ JSON 로그

MVP의 분석 대상 원본은 PostgreSQL 13에서 다음 설정으로 남긴 **text format stderr log**로 한다.

```conf
log_min_duration_statement = '3s'

session_preload_libraries = 'auto_explain'
auto_explain.log_min_duration = '1s'
auto_explain.log_analyze = on
auto_explain.log_buffers = on
auto_explain.log_wal = on
auto_explain.log_timing = off
auto_explain.log_format = 'text'
auto_explain.log_level = 'LOG'
auto_explain.sample_rate = 1.0
```

이 설정에서는 다음처럼 로그가 남는다.

- 1초 초과, 3초 미만 쿼리: `auto_explain` plan log만 남음
- 3초 이상 쿼리: slow query log와 `auto_explain` plan log가 모두 남음

Agent는 이 조합을 기본 분석 시나리오로 삼는다.

## 2. PostgreSQL 13 Logging Research

### 2.1 Log Destinations

PostgreSQL 13의 `log_destination`은 다음 출력을 지원한다.

- `stderr`
- `csvlog`
- `syslog`
- Windows: `eventlog`

PostgreSQL 13에서는 `jsonlog`가 없다. JSON-format server log는 PostgreSQL 15에서 도입된 기능으로 보인다. 따라서 v13 중심 MVP에서는 `stderr`와 `csvlog`를 우선 지원하고, v15+ 확장 항목으로 `jsonlog`를 둔다.

관련 설정:

```conf
logging_collector = on
log_destination = 'stderr,csvlog'
log_directory = 'log'
log_filename = 'postgresql-%Y-%m-%d_%H%M%S.log'
log_line_prefix = '%m [%p] %q%u@%d/%a '
log_timezone = 'Asia/Seoul'
```

`log_line_prefix`는 stderr 로그 파싱에서 매우 중요하다. PostgreSQL 13의 기본값은 timestamp와 process id를 포함하는 `'%m [%p] '`이다. 분석 Agent는 prefix를 고정값으로 가정하지 말고, 사용자가 설정한 `log_line_prefix` 또는 자동 추론 결과를 입력으로 받을 수 있어야 한다.

추천 prefix 후보:

```conf
log_line_prefix = '%m [%p] %q%u@%d/%a '
```

이 prefix는 다음 정보를 제공한다.

- `%m`: millisecond 포함 timestamp
- `%p`: process id
- `%q`: session context가 없는 background process에서 이후 prefix 생략
- `%u`: user
- `%d`: database
- `%a`: application name

### 2.2 Slow Query Logging

PostgreSQL 13에서 slow query log는 `log_min_duration_statement`로 설정한다.

MVP 기준 예시 설정:

```conf
log_min_duration_statement = '3s'
log_statement = 'none'
log_duration = off
log_parameter_max_length = 2048
```

동작:

- 실행 시간이 threshold 이상인 statement의 duration과 query text가 로그에 남는다.
- 값을 `0`으로 설정하면 모든 statement duration과 query text가 로그에 남는다.
- 기본값 `-1`은 비활성화다.
- extended query protocol에서는 Parse, Bind, Execute 단계 duration이 독립적으로 기록될 수 있다.

Synthetic stderr sample:

```log
2026-04-17 10:15:42.318 KST [28491] app_user@sales_api/order-service LOG:  duration: 1248.512 ms  statement: SELECT o.id, o.total, c.email
	FROM orders o
	JOIN customers c ON c.id = o.customer_id
	WHERE o.created_at >= now() - interval '7 days'
	ORDER BY o.created_at DESC
	LIMIT 50;
```

Extended query protocol sample:

```log
2026-04-17 10:16:03.771 KST [28491] app_user@sales_api/order-service LOG:  duration: 612.044 ms  execute <unnamed>: SELECT * FROM orders WHERE customer_id = $1 AND status = $2
2026-04-17 10:16:03.771 KST [28491] app_user@sales_api/order-service DETAIL:  parameters: $1 = '42', $2 = 'paid'
```

### 2.3 Execution Plan Logging With auto_explain

PostgreSQL 13의 실행 계획 자동 로깅은 `auto_explain` supplied module을 사용한다.

MVP 기준 대표 설정:

```conf
session_preload_libraries = 'auto_explain'
auto_explain.log_min_duration = '1s'
auto_explain.log_analyze = on
auto_explain.log_buffers = on
auto_explain.log_wal = on
auto_explain.log_timing = off
auto_explain.log_triggers = on
auto_explain.log_verbose = off
auto_explain.log_settings = on
auto_explain.log_format = 'text'
auto_explain.log_level = 'LOG'
auto_explain.log_nested_statements = off
auto_explain.sample_rate = 1.0
```

Notes:

- `auto_explain.log_min_duration`을 설정해야 실제 plan logging이 동작한다.
- `0`은 모든 plan logging, `-1`은 비활성화다.
- `auto_explain.log_analyze = on`이면 실제 실행 통계가 포함되지만 overhead가 있다.
- `auto_explain.log_timing = off`는 per-node timing overhead를 줄이기 위한 선택지다.
- `auto_explain.log_format`은 `text`, `xml`, `json`, `yaml`을 지원한다. PostgreSQL 13에서도 auto_explain의 plan format은 JSON으로 남길 수 있다. 이건 server log destination의 `jsonlog`와는 별개다.

Synthetic `auto_explain` text sample:

```log
2026-04-17 10:15:42.319 KST [28491] app_user@sales_api/order-service LOG:  duration: 1248.512 ms  plan:
	Query Text: SELECT o.id, o.total, c.email
	FROM orders o
	JOIN customers c ON c.id = o.customer_id
	WHERE o.created_at >= now() - interval '7 days'
	ORDER BY o.created_at DESC
	LIMIT 50;
	Limit  (cost=18432.11..18432.24 rows=50 width=72) (actual rows=50 loops=1)
	  Buffers: shared hit=8231 read=401
	  ->  Sort  (cost=18432.11..18601.44 rows=67732 width=72) (actual rows=50 loops=1)
	        Sort Key: o.created_at DESC
	        Sort Method: top-N heapsort  Memory: 42kB
	        Buffers: shared hit=8231 read=401
	        ->  Hash Join  (cost=421.00..16182.55 rows=67732 width=72) (actual rows=73412 loops=1)
	              Hash Cond: (o.customer_id = c.id)
	              Buffers: shared hit=8228 read=401
	              ->  Seq Scan on orders o  (cost=0.00..14502.00 rows=67732 width=32) (actual rows=73412 loops=1)
	                    Filter: (created_at >= (now() - '7 days'::interval))
	                    Rows Removed by Filter: 1920088
	                    Buffers: shared hit=7810 read=401
	              ->  Hash  (cost=296.00..296.00 rows=10000 width=48) (actual rows=10000 loops=1)
	                    Buckets: 16384  Batches: 1  Memory Usage: 812kB
	                    Buffers: shared hit=418
	                    ->  Seq Scan on customers c  (cost=0.00..296.00 rows=10000 width=48) (actual rows=10000 loops=1)
	                          Buffers: shared hit=418
	Settings: effective_cache_size = '8GB', work_mem = '4MB'
```

Synthetic `auto_explain.log_format = 'json'` sample:

```log
2026-04-17 10:17:11.009 KST [28502] app_user@sales_api/report-worker LOG:  duration: 913.207 ms  plan:
	{
	  "Query Text": "SELECT date_trunc('day', created_at) AS day, count(*) FROM orders GROUP BY 1 ORDER BY 1",
	  "Plan": {
	    "Node Type": "Sort",
	    "Startup Cost": 18890.12,
	    "Total Cost": 18890.62,
	    "Plan Rows": 200,
	    "Plan Width": 16,
	    "Actual Rows": 31,
	    "Actual Loops": 1,
	    "Sort Key": ["date_trunc('day'::text, created_at)"],
	    "Plans": [
	      {
	        "Node Type": "Aggregate",
	        "Strategy": "Hashed",
	        "Startup Cost": 18881.48,
	        "Total Cost": 18884.48,
	        "Plan Rows": 200,
	        "Plan Width": 16,
	        "Actual Rows": 31,
	        "Actual Loops": 1,
	        "Plans": [
	          {
	            "Node Type": "Seq Scan",
	            "Relation Name": "orders",
	            "Alias": "orders",
	            "Startup Cost": 0.00,
	            "Total Cost": 14502.00,
	            "Plan Rows": 875896,
	            "Plan Width": 8,
	            "Actual Rows": 1993500,
	            "Actual Loops": 1
	          }
	        ]
	      }
	    ]
	  }
	}
```

## 3. CSV Log Sample

PostgreSQL 13 `csvlog`는 fixed column order를 가진다. 주요 컬럼은 timestamp, user, database, process id, client, session id, session line number, command tag, transaction id, severity, SQLSTATE, message, detail, query, application name, backend type 등이다.

Synthetic CSV slow query sample:

```csv
2026-04-17 10:15:42.318 KST,app_user,sales_api,28491,10.20.30.40:54322,6621fdce.6f4b,12,SELECT,2026-04-17 10:11:08 KST,3/184,781223,LOG,00000,"duration: 1248.512 ms  statement: SELECT o.id, o.total, c.email
	FROM orders o
	JOIN customers c ON c.id = o.customer_id
	WHERE o.created_at >= now() - interval '7 days'
	ORDER BY o.created_at DESC
	LIMIT 50;",,,,,,,,order-service,client backend
```

CSV parsing requirements:

- 단순 line split 금지. message field 안에 newline과 comma가 들어갈 수 있다.
- RFC 4180 호환 CSV parser 또는 언어 표준 CSV parser 사용.
- `(session_id, session_line_num)`를 event ordering 및 multi-line grouping key로 활용 가능.

## 4. Initial Agent Requirements

### 4.1 Input

- PostgreSQL 13 text format stderr log files
- Optional config hints:
  - PostgreSQL major version
  - `log_line_prefix`
  - `log_min_duration_statement`
  - `auto_explain.log_min_duration`
  - timezone

### 4.2 Parser Capabilities

- stderr text log parser
- multi-line log event grouping
- configurable `log_line_prefix` parser
- slow query event extraction
- auto_explain plan extraction
- text plan parser, at least heuristic MVP

Out of MVP:

- CSV log parser
- PostgreSQL 15+ `jsonlog` parser
- `auto_explain.log_format = json` parser

### 4.3 Analysis Capabilities

- Top slow queries by duration
- Query fingerprinting / normalization
- Frequency and total time by fingerprint
- Plan node risk detection:
  - sequential scan on large row counts
  - high rows removed by filter
  - sort spill or high memory sort
  - nested loop with high loop counts
  - hash batch count greater than 1
  - temp file usage
  - buffer read-heavy plans
- Session/application/user/database breakdown

## 5. MVP Agent Design

### 5.1 Main Use Case

사용자는 PostgreSQL 13의 text log 파일 또는 디렉터리를 Agent에 입력한다. Agent는 slow query와 execution plan을 묶어서 쿼리 성능 문제를 설명하고, 우선순위가 높은 개선 후보를 제시한다.

사용자가 로그 분석을 통해 가장 알고 싶은 질문:

1. 현재 DB 서버에 운영상 이슈가 있는가?
2. 개선해야 할 쿼리는 무엇인가?

Agent의 MVP는 이 두 질문에 직접 답하는 report를 생성한다.

기본 입력 로그는 다음 조건을 만족한다고 본다.

- `log_min_duration_statement = '3s'`
- `auto_explain.log_min_duration = '1s'`
- `auto_explain.log_format = 'text'`
- `auto_explain.log_analyze = on`
- `auto_explain.log_buffers = on`
- `auto_explain.log_timing = off`

Raw log file을 LLM에 직접 넣지 않는다. 10MB 이상의 PostgreSQL log는 token limit, 비용, latency, 정확성, 민감정보 노출 측면에서 부적합하다. MVP는 local parser/aggregator가 로그를 먼저 구조화하고, LLM은 compact evidence bundle만 받아 해석하는 구조로 한다.

```text
Raw PostgreSQL Logs
  -> streaming parser
  -> structured events
  -> query fingerprint aggregation
  -> plan risk extraction
  -> redacted compact evidence bundle
  -> local LLM analysis
```

### 5.2 Expected Output

Agent는 최소한 다음 결과를 제공한다.

- 운영상 이슈 요약
- 개선 대상 쿼리 우선순위
- 가장 오래 걸린 쿼리 목록
- 총 누적 시간이 큰 query fingerprint 목록
- plan만 존재하는 1~3초 쿼리 목록
- slow query log와 plan log가 모두 존재하는 3초 이상 쿼리 목록
- 위험 plan pattern 탐지 결과
- 쿼리별 개선 힌트
- application/user/database/session 기준 breakdown

Report의 기본 구조:

```text
1. Executive Summary
2. Operational Risk Findings
3. Query Optimization Candidates
4. Workload Breakdown
5. Evidence Appendix
```

### 5.3 Event Model

MVP 내부 모델 후보:

```text
LogEvent
  timestamp
  pid
  user
  database
  application
  severity
  message
  detail
  raw_lines

SlowQueryEvent
  duration_ms
  statement
  parameters
  log_event_ref

PlanEvent
  duration_ms
  query_text
  plan_text
  parsed_plan_summary
  log_event_ref

QueryObservation
  fingerprint
  query_text
  slow_query_event?
  plan_event?
  duration_ms
  timestamp
  pid
  user
  database
  application
```

### 5.4 Correlation Strategy

3초 이상 쿼리는 slow query log와 plan log가 둘 다 남을 수 있다. 둘을 연결하기 위한 우선순위:

1. 같은 pid
2. timestamp 근접성
3. query text exact match
4. normalized query fingerprint match
5. duration 근접성

1~3초 쿼리는 plan log만 존재하므로 `PlanEvent` 단독으로 `QueryObservation`을 만든다.

### 5.5 Text Plan Heuristic Parser

MVP에서는 PostgreSQL text plan 전체를 완전한 AST로 파싱하기보다, line 기반 heuristic parser로 시작한다.

탐지할 패턴:

- `Seq Scan on <relation>`
- `Index Scan`, `Index Only Scan`, `Bitmap Heap Scan`
- `Nested Loop`, `Hash Join`, `Merge Join`
- `Sort Method: external merge` 또는 disk 사용
- `Rows Removed by Filter`
- `Buffers: shared hit=... read=... dirtied=... written=...`
- `Batches: N` where `N > 1`
- `Memory Usage`
- `WAL: records=... bytes=...`

추출 지표:

- node type
- relation name
- estimated rows
- actual rows
- actual loops
- buffer hits/reads
- rows removed by filter
- sort method and memory/disk usage

### 5.6 Analysis Rules

초기 rule-based 분석:

- `Seq Scan` + high actual rows: index 후보 또는 full scan 의도 확인
- `Rows Removed by Filter`가 actual rows보다 매우 큼: filter selectivity 문제
- `Nested Loop` + high loops: join order/index 확인
- `Sort Method: external merge` 또는 disk sort: `work_mem` 또는 index/order by 확인
- `Hash` with `Batches > 1`: hash memory 부족 가능성
- high `shared read` vs `hit`: cache miss 또는 I/O 병목 가능성
- planning/execution setting 변경 감지: `Settings:` line 활용

운영상 이슈 탐지:

- 긴 쿼리가 특정 시간대에 몰리는지
- 특정 application/user/database에서 slow query가 집중되는지
- repeated slow query가 workload 대부분을 차지하는지
- buffer read-heavy query가 많은지
- temp file usage 또는 external sort가 반복되는지
- lock wait/deadlock 관련 로그가 있는지
- connection/authentication 관련 오류가 반복되는지
- cancellation/timeout 로그가 반복되는지
- checkpoint, autovacuum, background writer 관련 경고성 로그가 있는지
- `ERROR`, `FATAL`, `PANIC` severity 이벤트가 있는지
- plan log 기준으로 row estimate와 actual row 차이가 큰 query가 있는지

개선 대상 쿼리 선정 기준:

- max duration이 큰 쿼리
- total duration이 큰 fingerprint
- call count가 많고 평균 duration도 높은 query
- 1~3초 구간에 반복적으로 등장하는 query
- Seq Scan, high Rows Removed, external sort, high loop Nested Loop 등 risk signal이 많은 query
- 특정 application의 latency에 직접 영향을 줄 가능성이 큰 query
- 적은 수정으로 개선 가능성이 커 보이는 query

### 5.7 Additional Insights

Slow query + auto_explain text log만으로도 다음 정보를 추가로 시도해볼 수 있다.

#### Workload Profile

- 시간대별 slow/plan event 발생량
- application별 부하 기여도
- database/user별 부하 기여도
- read-heavy workload vs write-heavy workload 추정
- 반복 실행되는 hot query fingerprint

#### Index And Query Tuning Hints

- index 후보 컬럼 추정
- ORDER BY/GROUP BY와 sort node 기반 index 가능성
- WHERE filter와 Rows Removed by Filter 기반 selectivity 문제
- join condition에 필요한 index 후보
- LIMIT + ORDER BY 패턴 최적화 후보

#### Statistics And Planner Quality

- estimated rows와 actual rows의 큰 차이 탐지
- stale statistics 또는 column correlation 문제 가능성
- `ANALYZE` 필요 후보 테이블
- plan instability 확인 후보

#### Memory And I/O Pressure Signals

- external sort 반복 여부
- hash batch 증가 여부
- shared read 비율이 높은 query
- temp file log가 있다면 temp I/O 유발 query
- `work_mem` 조정 검토 후보

#### Operational Stability Signals

- lock wait/deadlock
- statement timeout/canceling statement
- connection failure/authentication failure
- repeated ERROR/FATAL
- autovacuum 관련 지연 또는 wraparound 경고
- checkpoint frequency 또는 checkpoint pressure 관련 메시지

#### Reportable Actions

- 지금 바로 확인할 SQL
- 인덱스 후보 DDL 초안
- `EXPLAIN (ANALYZE, BUFFERS)` 재확인 대상
- 통계 갱신 대상 테이블
- application별 쿼리 개선 우선순위
- 추가로 켜면 좋은 PostgreSQL log setting

#### Limits And Caveats

로그만으로는 다음을 확정할 수 없다.

- 실제 서버 CPU 사용률
- OS/disk latency
- 전체 query workload 중 threshold 아래의 빠른 query 분포
- lock graph 전체
- 테이블/인덱스 실제 크기
- 최신 schema/index 정의
- PostgreSQL 설정 전체

따라서 Agent는 필요한 경우 후속 수집 항목을 제안한다.

후속 수집 후보:

- `pg_stat_statements`
- schema/index definition
- table/index size
- PostgreSQL settings
- `pg_stat_user_tables`, `pg_stat_user_indexes`
- selected query의 fresh `EXPLAIN (ANALYZE, BUFFERS)`

### 5.8 CLI Shape Candidate

구현 단계에서의 CLI 후보:

```bash
pg-log-agent analyze ./logs/postgresql.log --prefix "%m [%p] %q%u@%d/%a "
pg-log-agent top ./logs --by duration
pg-log-agent report ./logs --format markdown --output report.md
```

### 5.9 Implementation Phases

Phase 1: Parser MVP

- text log event grouping
- slow query extraction
- auto_explain text plan extraction
- query observation correlation
- fixture 기반 테스트

Phase 2: Rule-based Analyzer

- fingerprint aggregation
- top slow queries
- risky plan pattern detection
- markdown report

Phase 3: Agent Layer

- natural language Q&A over parsed observations
- "why is this slow?" query drill-down
- improvement suggestions with evidence

Phase 4: Format Expansion

- CSV log
- PostgreSQL 15+ jsonlog
- `auto_explain.log_format = json`

## 6. Local LLM Strategy

분석 주체 LLM은 cloud LLM만 전제하지 않고, LM Studio에서 구동 가능한 local model도 지원한다. 후보 baseline은 `google/gemma-4-e4b` 같은 lightweight local model이다.

### 6.1 Feasibility

Gemma 4 E4B급 local model도 MVP의 "최종 해석 계층" 역할은 가능하다고 본다. 단, 원본 로그 전체 분석, 대규모 집계, 정확한 counting, plan tree full parsing은 LLM에게 맡기지 않는다.

LLM이 맡기 좋은 일:

- 이미 추출된 top query와 risk signal 설명
- plan evidence를 사람이 이해하기 쉬운 문장으로 변환
- 개선 후보 우선순위화
- "왜 느린가?"에 대한 가설 생성
- DBA/개발자용 report 문장 작성
- 후속 확인 SQL 제안

LLM에게 맡기지 않을 일:

- raw log 전체 읽기
- duration/frequency/percentile 집계
- CSV/text log parsing
- plan node 숫자 정확 추출
- query fingerprint 생성
- 민감정보 masking

### 6.2 LM Studio Integration Candidate

LM Studio의 OpenAI-compatible local server를 사용할 수 있도록 LLM adapter를 분리한다.

예상 설정:

```text
LLM_PROVIDER=lmstudio
LLM_BASE_URL=http://localhost:1234/v1
LLM_MODEL=google/gemma-4-e4b
```

Agent 내부 구조:

```text
Analyzer Core
  -> EvidenceBundle JSON
  -> LLM Adapter
      -> LM Studio OpenAI-compatible API
      -> Cloud/OpenAI-compatible API, optional later
```

### 6.3 Evidence Bundle Size

Local model의 context window가 크더라도, prompt는 작게 유지한다. 기본 목표:

- summary bundle: 10KB 이하
- query drill-down bundle: 20KB 이하
- full report generation bundle: chunked batches

Bundle에는 raw query 전체를 무조건 넣지 않고, 다음을 우선한다.

- normalized fingerprint
- representative query sample, redacted
- duration stats
- call count
- top plan risk signals
- selected plan lines
- relation names
- buffer/read/row statistics
- source event ids or line references

### 6.4 Model Quality Assumption

Gemma 4 E4B급 모델은 lightweight local model이므로, 성능 진단의 핵심 판단은 rule-based analyzer가 먼저 만든 evidence와 scoring에 둔다. LLM은 그 결과를 설명하고 정리하는 역할을 한다.

정확성이 필요한 문장에는 evidence id를 붙인다.

예시:

```text
Finding:
  Query F-018 has high total runtime because it ran 84 times and spent 74.3s total.
Evidence:
  calls=84, total_duration_ms=74320, max_duration_ms=1248
  plan_signals=["Seq Scan on orders", "Rows Removed by Filter: 1920088"]
```

## 7. Open Questions

- MVP target language and runtime: Python, Go, Rust, or Node.js?
- CLI first, web UI first, or Agent API first?
- Query normalization strategy: built-in heuristic vs `libpg_query` integration?
- Should we support pgbadger-like report generation, conversational analysis, or both?
- Do we want sample logs stored in `samples/` later, or only embedded in documentation first?
- Local LLM baseline을 Gemma 4 E4B로 둘지, 더 큰 model도 optional profile로 둘지?
- LM Studio만 우선 지원할지, Ollama/OpenAI-compatible endpoint를 일반화할지?

## 8. Implementation Readiness Additions

### 8.0 Current Implementation Status

MVP implementation started.

Implemented:

- Python `src` layout package
- `pglog-agent` CLI entry point
- `analyze` command
- PostgreSQL 13 stderr text log parser
- multi-line log event grouping
- slow query extraction
- `auto_explain.log_format = text` plan extraction
- slow query and plan event correlation
- heuristic text plan signal detection
- query fingerprinting
- safe-by-default SQL redaction
- operational finding detection
- rule-based query optimization findings
- output generation:
  - `report.md`
  - `findings.json`
  - `observations.jsonl`
  - `parse_errors.jsonl`
- optional LM Studio adapter
- sample log fixtures
- basic unittest coverage

Verification:

- `python -m unittest discover -s tests`
- `$env:PYTHONPATH='src'; python -m pglog_agent analyze samples --output out`
- `python -m compileall src tests`

### 8.1 MVP Scope Lock

MVP에 포함한다.

- PostgreSQL 13 stderr text log
- `log_min_duration_statement = '3s'`
- `auto_explain.log_min_duration = '1s'`
- `auto_explain.log_format = 'text'`
- slow query event extraction
- auto_explain plan event extraction
- slow query와 plan event correlation
- query fingerprint aggregation
- rule-based risk detection
- Markdown report generation
- JSON evidence bundle generation
- LM Studio integration as optional report enhancer
- LLM unavailable 시 deterministic report fallback

MVP에서 제외한다.

- CSV log parser
- PostgreSQL 15+ `jsonlog`
- `auto_explain.log_format = 'json'`
- web UI
- database direct connection
- automatic index creation
- complete PostgreSQL text plan AST parser
- distributed/multi-host log correlation

Decisions:

- MVP runtime/language: Python
- CLI package name: `pglog-agent`
- 기본 output directory convention: `./out`

### 8.2 Sample Log Fixtures

구현 단계에서 다음 fixture를 만든다.

```text
samples/
  pg13_text_basic.log
  pg13_text_multiline_query.log
  pg13_text_slow_and_plan_pair.log
  pg13_text_plan_only_1_to_3_sec.log
  pg13_text_errors_locks.log
  pg13_text_operational_warnings.log
```

각 fixture의 목적:

- `pg13_text_basic.log`: 기본 prefix, single-line slow query, basic plan event
- `pg13_text_multiline_query.log`: multi-line SQL statement grouping
- `pg13_text_slow_and_plan_pair.log`: 3초 이상 query의 slow log + plan log correlation
- `pg13_text_plan_only_1_to_3_sec.log`: 1~3초 query의 plan-only observation
- `pg13_text_errors_locks.log`: lock wait, deadlock, timeout, ERROR/FATAL detection
- `pg13_text_operational_warnings.log`: checkpoint, autovacuum, temp file, cancellation signal

Fixture 검증 항목:

- multi-line event grouping
- slow query extraction
- plan extraction
- query text extraction
- slow/plan correlation
- operational finding detection
- malformed or incomplete log resilience

### 8.3 Output Files

기본 출력 구조 후보:

```text
out/
  report.md
  findings.json
  observations.jsonl
  parse_errors.jsonl
```

파일 역할:

- `report.md`: 사람이 읽는 최종 report
- `findings.json`: LLM에 전달 가능한 compact evidence bundle
- `observations.jsonl`: query observation 중간 산출물
- `parse_errors.jsonl`: 파싱 실패 또는 partially parsed event 기록

`findings.json`은 raw log 전체를 포함하지 않는다. 필요한 경우 source line range, event id, fingerprint id를 참조한다.

### 8.4 Redaction Policy

기본값은 safe-by-default redaction으로 한다.

Redaction 대상:

- SQL string literal
- SQL numeric literal
- bind parameter value
- email-like value
- long UUID/token-like value
- long query text

보존 대상:

- relation/table name
- column name
- SQL operation type
- plan node type
- application name
- database name
- user name, optional masking profile 적용 가능

예시:

```sql
SELECT * FROM orders WHERE customer_id = 12345 AND email = 'a@example.com'
```

Redacted:

```sql
SELECT * FROM orders WHERE customer_id = ? AND email = '?'
```

Decisions:

- SQL literal, bind parameter, email/token-like value는 기본 masking한다.
- user/database/application name은 기본 보존한다.
- `--redact-identities` option으로 user/database/application name masking을 제공한다.
- report에는 redacted representative query만 포함한다.
- raw query는 기본적으로 LLM prompt와 report에 포함하지 않는다.

### 8.5 Risk Scoring

MVP는 단순한 rule-based score로 개선 후보를 정렬한다.

초기 score 후보:

```text
score =
  duration_score
  + total_time_score
  + frequency_score
  + plan_risk_score
  + operational_context_score
```

세부 기준 후보:

- `duration_score`: max duration 또는 p95 추정 duration
- `total_time_score`: fingerprint별 total duration
- `frequency_score`: 반복 발생 횟수
- `plan_risk_score`: Seq Scan, high rows removed, external sort, high loop Nested Loop, hash batch, high shared read 등
- `operational_context_score`: 특정 incident window, timeout/error 동반 여부, application 집중도

Finding severity 후보:

- `critical`: 장애 가능성이 있거나 반복 timeout/deadlock/FATAL과 연결
- `high`: 큰 total duration 또는 명확한 plan risk
- `medium`: 개선 여지는 있으나 영향 범위 제한적
- `low`: 관찰 필요

### 8.6 LM Studio Fallback Behavior

LM Studio 연동은 optional이다. 로컬 LLM이 없어도 Agent는 동작해야 한다.

Fallback rules:

- LM Studio unavailable: deterministic `report.md` 생성
- LLM timeout: `findings.json` 저장 후 report에 LLM summary skipped 명시
- LLM response parse failure: raw LLM text를 별도 section에 보존하거나 deterministic summary로 대체
- LLM hallucination 방지: report의 핵심 숫자는 `findings.json`의 evidence만 사용
- LLM prompt에는 raw log를 넣지 않고 compact evidence bundle만 전달

Decisions:

- LLM 기본값은 `off`다.
- 사용자가 `--llm lmstudio`를 지정할 때만 LM Studio를 호출한다.
- LM Studio timeout 기본값은 60초로 시작한다.

### 8.7 CLI UX

CLI first로 시작하는 후보.

```bash
pg-log-agent analyze ./logs --output ./out
pg-log-agent report ./logs/postgresql.log --output ./out --llm lmstudio
pg-log-agent inspect ./out/findings.json --fingerprint F-018
pg-log-agent validate ./samples/pg13_text_basic.log
```

명령 역할:

- `analyze`: parse + aggregate + deterministic findings
- `report`: analyze + markdown report + optional LLM summary
- `inspect`: 특정 fingerprint/event drill-down
- `validate`: sample 또는 사용자 로그가 parser에 맞는지 확인

Decisions:

- MVP에서는 `analyze` 하나로 시작한다.
- `analyze`는 `findings.json`, `observations.jsonl`, `parse_errors.jsonl`, `report.md`를 모두 생성한다.
- 이후 필요하면 `inspect`, `validate` 명령을 추가한다.

### 8.8 Explicit Limits

Agent report는 다음 한계를 명시해야 한다.

- threshold 아래 쿼리는 보이지 않는다.
- CPU, disk, network 병목은 log만으로 확정할 수 없다.
- index recommendation은 schema/index/cardinality 정보 없이는 후보 수준이다.
- `auto_explain.log_analyze = on` 자체가 overhead를 유발할 수 있다.
- application retry 또는 batch job context는 log만으로 알기 어렵다.
- plan은 해당 실행 시점의 관찰이며 항상 재현되는 것은 아니다.

### 8.9 Follow-Up Data Collection

정확도를 높이기 위해 나중에 추가 수집할 수 있는 자료:

- `pg_stat_statements`
- table/index schema
- table/index size
- `postgresql.conf` 주요 설정
- `pg_stat_user_tables`
- `pg_stat_user_indexes`
- `pg_locks` snapshot during incident
- selected query의 fresh `EXPLAIN (ANALYZE, BUFFERS)`

이 자료들은 MVP에서 DB에 직접 접속하지 않고, 사용자가 export한 파일을 입력으로 받는 방식부터 고려한다.

## 9. Sources

- PostgreSQL 13 Error Reporting and Logging: https://www.postgresql.org/docs/13/runtime-config-logging.html
- PostgreSQL 13 auto_explain: https://www.postgresql.org/docs/13/auto-explain.html
- PostgreSQL 16 Error Reporting and Logging, for later `jsonlog` reference: https://www.postgresql.org/docs/16/runtime-config-logging.html
- PostgreSQL Feature Matrix, JSON logging target context: https://www.postgresql.org/about/featurematrix/detail/383/
- LM Studio Gemma 4 model page: https://lmstudio.ai/models/gemma-4
- Hugging Face google/gemma-4-E4B model page: https://huggingface.co/google/gemma-4-E4B
