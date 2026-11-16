# Operations data spine and call-out automation

A data spine for a unionized building-services company (Part A) and an automated call-out → payroll → billing
flow on top of it (Part B), with a React interface. One command starts everything and writes the scored files
to `./output`.

```
docker compose up --build
```

| What | Where |
|---|---|
| Scored outputs | `./output/` (`kpis_by_building.csv`, `ar_aging.csv`, `payroll_submissions.csv`, `backup_notes/`, `exceptions.csv`, `decisions_log.md`) |
| Interface | http://localhost:3000 (KPI panel, building view, review queue, payroll and exceptions, chatbot) |
| API | http://localhost:8000/docs |
| Decisions log (standing rules) | [`docs/decisions_log.md`](docs/decisions_log.md). The run appends the conflicts it found to `output/decisions_log.md` |
| Where AI was used, and what I verified | [`docs/AI_USAGE.md`](docs/AI_USAGE.md) |

---

## Setup

**Prerequisites:** Docker Desktop, and the client systems running (`cd mock_systems && docker compose up --build` in
the assessment package).

1. Put the client files in `./data` (same layout as the package's `data/`), **or** set `DATA_DIR` to their folder.
2. `cp .env.example .env` and set `ANTHROPIC_API_KEY` (used to read call-out messages, scanned forms and for the chatbot).
3. `docker compose up --build`

The `pipeline` container runs once and exits; `db`, `api` and `web` keep running.

If the mock systems run on the same machine with Docker Desktop, you can join their Docker network instead of going
through `host.docker.internal` (Docker Desktop's host forwarding crashed under load on the development machine):
`docker compose -f docker-compose.yml -f docker-compose.mocknet.yml up --build`

| Variable | Default | Meaning |
|---|---|---|
| `DATA_DIR` | `./data` | Client files |
| `PAYROLL_URL`, `DISPATCH_URL`, `FINANCE_URL` | `http://host.docker.internal:8081/8082/8083` | The client systems. In a container `localhost` is the container itself, so the default reaches the host. |
| `ANTHROPIC_API_KEY` | – | Claude. Without it, call-outs cannot be read and are all held for review |
| `LLM_MODEL` | `claude-opus-5-5` | Which Claude model to use |
| `DRY_RUN` | `false` in Compose, `true` for a local `python -m pipeline.run` (unset in `.env.example` on purpose) | `true` = never write to payroll; the audit log shows what would be written |
| `PERIOD` | the month most punches fall in | Reporting month `YYYY-MM` |
| `LLM_MODE` | `live` if a key is set, else `off` | `replay` reads saved answers from `LLM_FIXTURES_DIR` (tests/offline) |

**Without Docker** (for development): `python -m venv .venv && .venv/Scripts/pip install -r requirements.txt`, then
`python -m pipeline.run` (settings from `.env`; Postgres optional via `DATABASE_URL`), `uvicorn api.main:app`, and
`cd web && npm install && npm run dev`.

---

## Architecture

```
                 ┌───────────── client files (DATA_DIR) ─────────────┐   ┌──── client systems (APIs) ────┐
                 │ HR roster · PAF scans · time clock (2 layouts) ·  │   │ Payroll  :8081  (read + write) │
                 │ contracts · wage schedule · reception log · chat ·│   │ Dispatch :8082  (OAuth, 429)   │
                 │ labor budget                                      │   │ Finance  :8083  (async reports)│
                 └──────────────────────────┬────────────────────────┘   └───────────────┬───────────────┘
                                            ▼                                            ▼
 pipeline/ (one-shot)  ingest/  ──►  resolve/  ──►  callouts.py ──► rules/ ──► payroll_writer.py ──► payroll
                       readers      buildings,      LLM reads,      notice,     dry-run, look-up-   (exactly once)
                       per source   employees,      code decides    coding,     before-write,
                                    PAFs            dates/people    billing     retries, audit
                                            │
                                            ├──► coverage.py (final assignment, rates, invoice matching, backup notes)
                                            ├──► attendance.py (punches → worked shifts)
                                            └──► kpi.py (one fact row per source record) ──► outputs.py ──► ./output
                                                                                         └─► db.py ──► Postgres
 api/   FastAPI: KPIs + drill-down to facts + source records, building view, review queue (approve → same writer), chat
 web/   React + TypeScript: KPI panel, building view, review queue, payroll & exceptions, chatbot, source drawer
```

### Folder guide

| Path | Job |
|---|---|
| `pipeline/clients/` | One client per system. Shared retry helper (`http.py`): 429/5xx/network retried with backoff and `Retry-After`. Dispatch renews its 5-minute token; finance resubmits FAILED report jobs. **The payroll POST is never auto-retried.** |
| `pipeline/ingest/` | Read each source exactly as delivered: both time-clock layouts (detected by header) into one shape with time zones; the reception log (voicemail times without a year); the chat export; the three finance report formats with their control totals; contracts and wage schedule (text PDFs). |
| `pipeline/resolve/` | One record per building (Schedule B name), employee, department and client. Names matched by words after expanding abbreviations; codes with no name (HR `BLD-xx`, ledger departments) mapped by evidence (where those employees punch in). Employees linked by HR id ↔ badge ↔ payroll id (name-checked) ↔ dispatch worker (name-checked). PAFs applied only with all three approvals. |
| `pipeline/llm/` | Claude with structured outputs (Pydantic schema) for call-out messages and scanned PAFs. **Reads only; decides nothing.** Answers cached on disk. |
| `pipeline/rules/` | Pure, tested functions: shift times and notice, date words → dates, the Article 14 coding table, pay rates and the billable formula. |
| `pipeline/callouts.py` | Messages → absences: received time, dates, employee match, de-duplication across channels, coding in date order, review decisions. |
| `pipeline/payroll_writer.py` | The only code that writes to payroll. |
| `pipeline/coverage.py` | Dispatch postings → coverage shifts, invoice matching, unbilled coverage, backup notes. |
| `pipeline/kpi.py` | KPIs and AR aging, built from "facts" (one row per contributing source record). |
| `pipeline/db.py` | Loads raw, resolved and fact tables into Postgres (full refresh each run). |
| `api/` | HTTP API, review decisions, chatbot (`run_sql` tool on a read-only connection). |
| `web/` | Interface. |
| `tests/` | Unit tests, payroll writer against a misbehaving fake payroll, golden LLM answers. |

### Data model (Postgres)

`raw_*` source records as received (call-out reports, invoice lines, GL lines, AR items, PAFs, labor budget) ·
`dim_employee`, `dim_building`, `dim_building_alias` (every source name/code → building, with method and confidence) ·
`fact_absence`, `fact_coverage`, `fact_building_view`, `fact_kpi` (building, kpi, source, source_ref, value, detail),
`fact_ar_open` · `review_queue`, `review_action`, `payroll_audit` · `out_*` (the scored outputs).
Every derived row carries `source` + `source_ref`, which is how the UI and the chatbot link each figure to its records.

---

## How the key figures are calculated

### Gross margin

```
revenue      = Σ invoice lines with service period = the month, for the building
               (contract + coverage + sundry + credit lines; any invoice date; both legal entities)
labor cost   = Σ GL labor lines for the building's ledger department, the month, all accounts, both entities
gross margin = revenue − labor cost          gross margin % = gross margin ÷ revenue
```
Invoice lines are tied to buildings by their finance site name; GL lines by department (finance's site→department
list, completed from the HR roster where finance's list misses a department). Every report is checked against its
own printed totals first (see Part 3 of `output/decisions_log.md`).

*Worked example, Meridian One:* contract line 75,579.40 + six coverage lines 2,069.28 − service credit 1,250.00
= **76,398.68** revenue; department 420 labor **66,446.32**; gross margin **9,952.36** (13.03%).

### Unbilled coverage

1. For each dispatch posting, read its events in order: `ASSIGNED` sets the worker, `UNASSIGNED` clears it.
   A posting is covered if it is `FILLED` and a worker is still assigned at the end.
2. At buildings whose agreement makes coverage billable (cost-plus), each covered shift's billable amount is
   ```
   8 h × base rate × (1.5 if filled on OT) × (1 + burden) × (1 + markup)      rounded to the cent per shift
   ```
   where base rate and burden belong to the **covering** employee: the CBA rate for their job class
   (union) or the rate card (non-union/temp); burden 32% union / 18% non-union; markup from Schedule B.
3. Each invoice coverage line is matched to a posting by its dispatch reference, or (no reference) by building +
   service date + shift, then the replacement's name, then the amount.
4. **Unbilled** = covered billable shifts with no matching invoice line: count and Σ billable amount.

*Worked example, Meridian One:* P-1012-014 (temp, standard) 8 × 21.00 × 1.18 × 1.15 = **227.98**; P-1015-026
(union floater) 8 × 24.10 × 1.32 × 1.15 = **292.67** → 2 shifts, **520.65**. The same formula reproduces every
amount on the client's own invoices (tested in `tests/test_rules.py`).

### Payroll codes

Article 14 and the coding guide, in the guide's order: **eligibility → notice → balance**, applied in date order per
employee so sick days run down through the month. The full table is in `pipeline/rules/coding.py` and every row has a
test. Notice is measured from when the call-out was **received** (voicemail system time, call time, or when the area
manager says the text arrived), never from when reception transcribed it.

---

## Payroll safety

* **Dry run:** `DRY_RUN=true` sends nothing.
* **Exactly once:** key = (payroll employee id, date). Before each POST the writer asks payroll what it already holds
  for that employee and date: an entry with the same code is adopted; an entry this system wrote with a different code
  is voided and replaced; an entry someone else made is never touched (the absence is held for review).
* **Lost 202s:** after any 503/429/network error the writer looks again before retrying. In testing against the
  client system, 4 of 50 "failed" POSTs had in fact been accepted, and none was duplicated.
* **Audit:** every look-up, post, void and poll is logged (`payroll_audit` table, `output/payroll_audit.json`).
* **Final status:** each submission is polled until payroll reports `POSTED` (or `REJECTED`).
* **Review queue:** low confidence, ambiguous identity, missing voicemail timestamp, employee not active in payroll,
  and similar cases are held with a reason. A reviewer approves (written through the same writer) or rejects in the UI,
  and the next pipeline run respects that decision.

Verified: payroll reset → one run → 50 submissions, all `POSTED`, 2 held → second run → 0 new posts.

---

## Tests

```
docker compose --profile test run --rm tests        # or: .venv/Scripts/python -m pytest -q
```

115 tests: the coding table row by row (including check order and the 90-day boundary); date words (after-midnight
"tomorrow", weekday names, multi-day absences); notice from voicemail time; billable amounts against the client's
invoices; wage-rate effective dates; building and name matching; PAF transfer dates; invoice/AR/GL parsing and
tie-outs; AR aging with credit memos; last-assignment-wins; invoice matching by amount; and the payroll writer against a
fake payroll that loses 202s, returns 503s and 422s, and holds someone else's entry.

`tests/test_llm_golden.py` compares the live LLM's readings of all 70 messages with hand-checked answers
(`tests/fixtures/golden_llm.py`). It calls Claude (costs money), so it only runs with `RUN_LIVE_LLM=1` and a key set; otherwise it is skipped.

**Hand verification:** every KPI for Meridian One and every payroll code were recomputed by hand from the raw files
(see the worked examples above and the reasons in `output/payroll_submissions.csv`).

---

## Working on unseen data

Nothing from this dataset is hard-coded: buildings come from the agreements, codes are mapped by evidence, the month
comes from the data, file layouts are detected from headers, column names with dates in them are found by prefix,
and employees and sites are discovered through the APIs. Anything that cannot be resolved goes to `exceptions.csv`
with a reason and confidence rather than being guessed.

---

## What I deferred, and why

| Deferred | Why / what instead |
|---|---|
| A truly *live* building view | The view is a snapshot as of the last pipeline run (re-run to refresh). A live feed needs event hooks the client systems don't offer; polling the APIs is a small step from here. |
| Contract amendments beyond post-count changes | Amendments are parsed for dated post changes (the case present). Other kinds of change are flagged for manual review, not guessed. An LLM extractor like the PAF one would be the next step. |
| Public holidays | The agreements exclude "holidays observed by the Building" but no holiday list is provided; all weekdays count. |
| Labor budget reconciliation | Used only as a cross-check (it is stale); differences are reported, nothing is reconciled. |
| Chatbot sophistication | One `run_sql` tool over the documented tables, with citations. Each citation is checked against the rows its queries actually returned; one that cannot be verified is not linked. The prompt is given the dataset's real building names, KPI names and reporting period. No conversation memory beyond the session, no charts. |
| Authentication on the UI/API | Local tool. It would need SSO before real use. |
| Incremental loads | The pipeline does a full refresh each run (small data). Payroll writes are idempotent either way. |
