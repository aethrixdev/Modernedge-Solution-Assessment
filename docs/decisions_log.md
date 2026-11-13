# Decision Log

## Day 1

- Settings all come from env vars. DRY_RUN is on by default so I can't write to payroll by accident. I'm building with Claude Code (Opus 5.5) and reviewing each piece before I commit it.
- Started with the payroll client and had Claude add a short comment to every function. GETs retry on 429/5xx, but the payroll POST only goes out once. If the reply gets lost and I resend, I could end up with a duplicate absence.
- Every feature gets tests in the same commit. They fake the HTTP replies, so I don't need the mock systems running.
- Dispatch tokens get refreshed 30s early, and I only retry once on a 401. If the secret is wrong I want it to fail, not loop. On a 429 I wait however long Retry-After says.
- HR, time clock and call-out files: I detect the layout from the headers, not the file name. The time clock switched formats mid-month, and next month's export might too.
- Contracts and finance reports follow fixed templates, so plain code is enough here, no LLM. Each finance report gets checked against its own printed totals. If they don't match, it goes to exceptions.
- Building names come from Schedule B in the contracts. Other spellings get matched word by word (TWR = TOWER). HR's BLD-xx codes have no name at all, so I map them by where those people actually punch in. If a name like "Bramwell" could be two buildings, I don't guess.
- Matching people: I only trust a shared ID if the names agree too, since dispatch's HR number is typed by hand and can be wrong. Two people with the same name get left for review.
- "Tonight", "Friday", "10/22" get turned into dates in code, counted from when the person actually called, not when reception typed it up. A voicemail at 00:25 saying "tomorrow" means the coming shift. I flag that one as less certain.
- Payroll codes are plain code, never the LLM. Same order as the coding guide: eligible? then 4h notice? then sick days left? If I can't tell when the call came in, I don't pick a code. It goes to review. Billing math is checked against amounts on real invoices.
- Tried Groq (gpt-oss-120b) first, since I didn't have an Anthropic key yet and had asked the company about it. The LLM just reads the message; the code decides the rest.

## Day 2

- Switched to Claude (Opus 5.5) with my own Anthropic key. Groq's free tier ran out before a full run could finish in my prototype.
- Decided a transfer form (PAF) only counts if all three people signed it: HR, Operations and Payroll. Claude just reads the scan; the code checks the signatures. So Brian Walker's transfer, signed only by HR, doesn't change anything.
- Payroll gets checked before every write, so a re-run or a lost reply never makes a duplicate.
- The six output files follow the package README exactly, since that's what gets scored. output/decisions_log.md is this log plus the conflicts found in that run's data.
