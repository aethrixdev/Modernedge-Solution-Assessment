# How I used AI, and what I checked myself

The brief allows AI tools, as long as I can explain and change every line. This note covers both kinds of AI use:
the AI that runs inside the system, and the AI I used while building it.

## AI inside the system

The rule I followed: **the LLM reads, the code decides.** The model only turns messy input into structured data.
Anything that affects money or payroll is plain Python that I can test.

| What the LLM does | Model | What stops it from going wrong |
|---|---|---|
| Reads call-out messages (reception log and managers' chat, in English, Spanish and Albanian) | Claude Opus 5.5, structured outputs against a Pydantic schema (`pipeline/llm/callout_extractor.py`) | It only reports what was said: the name, building, shift, day words like "tomorrow", the reason and any time the caller mentioned. Dates, notice, eligibility, sick-day balances and payroll codes are worked out in code. Answers that don't fit the schema are rejected, and anything below 0.75 confidence goes to review. |
| Reads the scanned Personnel Action Forms (image PDFs) | Same, with the PDF sent directly | It only reports what's on the form and which approval boxes are signed. Whether the form takes effect (all three approvals needed) is decided in code. |
| Answers questions in the chatbot | Same, with one read-only `run_sql` tool | Read-only database transaction. It has to cite the source rows, and it has to say so when the data can't answer. Citations are checked against the rows the queries actually returned. |

If the API is down or the answer is invalid, nothing is guessed. The item is held for review. Answers are cached by
a hash of the request, so a re-run gives the same result and costs nothing.

### Groq first, then Claude

I started with Groq (`gpt-oss-120b`) because I didn't have an Anthropic key yet; I'd asked the company about it. On
Day 2 I switched to Claude Opus 5.5 with my own key, because Groq's free tier ran out before a full run could finish.
Since all LLM calls go through one function (`pipeline/llm/client.py`), the switch only touched that file.

## AI as a coding assistant

I built this with Claude Code (Opus 5.5). How I worked with it:

- **One feature per branch,** with its tests in the same commit. I read the diff and ran the tests before merging.
- **The judgment calls are mine.** Claude often suggested options, e.g. for the voicemail "tomorrow" after midnight,
  PAFs without all approvals, or what to do when two employees share a name. I picked one and wrote down why in
  `docs/decisions_log.md`.
- **Docs.** Claude helped draft the README, this note and the decisions log. I rewrote and checked them.

## What I checked myself

- **Payroll decisions:** went through the 52 absences in `output/payroll_submissions.csv` and checked the date, notice
  and code against the raw reception log, chat, roster and Article 14. The hand-checked readings of all 70 messages are
  the golden set in `tests/fixtures/golden_llm.py`.
- **Payroll safety:** reset payroll, ran once, ran again. 50 entries, all `POSTED`, 2 held, no duplicates, including
  after 503s where the entry had in fact been accepted.
- **Billing formula:** reproduced amounts on the client's own invoices (tests in `tests/test_rules.py`).
- **Meridian One KPIs:** recomputed by hand from the raw files (worked examples in the README).
- **Finance:** every invoice register, AR report and GL report ties to its own printed totals.
- **Contracts and wage schedule:** checked the parsed posts, pricing, markup, Exhibit C template, the Corlett amendment
  date, rates and burden against the PDFs.
- **PAFs:** opened all four scans myself and checked which ones carry all three approvals.
- **Unseen data:** searched the code for names, IDs, buildings and dates from this dataset and removed what I found.
