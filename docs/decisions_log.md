# Decision Log

## Day 1

- GETs retry on 429/5xx, but the payroll POST only goes out once. If the reply gets lost and I resend, I could end up with a duplicate absence.
- HR, time clock and call-out files: I detect the layout from the headers, not the file name. The time clock switched formats mid-month, and next month's export might too.
- Contracts and finance reports follow fixed templates, so plain code is enough here, no LLM. Each finance report gets checked against its own printed totals. If they don't match, it goes to exceptions.
- Building names come from Schedule B in the contracts. Other spellings get matched word by word (TWR = TOWER). HR's BLD-xx codes have no name at all, so I map them by where those people actually punch in. If a name like "Bramwell" could be two buildings, I don't guess.
- Matching people: I only trust a shared ID if the names agree too, since dispatch's HR number is typed by hand and can be wrong. Two people with the same name get left for review.
- "Tonight", "Friday", "10/22" get turned into dates in code, counted from when the person actually called, not when reception typed it up. A voicemail at 00:25 saying "tomorrow" means the coming shift. I flag that one as less certain.
- Payroll codes are plain code, never the LLM. Same order as the coding guide: eligible? then 4h notice? then sick days left? If I can't tell when the call came in, I don't pick a code. It goes to review. Billing math is checked against amounts on real invoices. The LLM just reads the message; the code decides the rest.

## Day 2

- Decided a transfer form (PAF) only counts if all three people signed it: HR, Operations and Payroll. Claude just reads the scan; the code checks the signatures. So Brian Walker's transfer, signed only by HR, doesn't change anything.
- Payroll gets checked before every write, so a re-run or a lost reply never makes a duplicate.

## Other conflicts in the data

- Corlett Amendment No. 2 raises Corlett's night posts from 7 to 8 from Sept 1, 2026. So October uses 8. Amendments apply from their effective date.
- Only cost-plus clients pay for coverage and have an Exhibit C note template. Coverage at fixed-fee buildings is never "unbilled".
- An approved PAF wins over the roster, because the roster can lag behind (Mimoza's transfer from 10/19 isn't in it yet).
- The first time-clock file has local times with no zone, the second has an offset. Both are converted to the site's time zone from dispatch.
- Night shifts cross midnight. A shift counts on the date it starts.
- Missing clock-out: I keep the shift and log it. On a Template A backup note the time says `Not recorded`.
- The same absence can come in through reception and the managers' chat (e.g. reception row 12 and the chat on 10/09, both E10020). One employee on one date = one absence, written to payroll once.
- Calls that aren't absences (direct deposit question, confirming an earlier call, running 20 min late) are left out and listed in exceptions. Planned vacation isn't a call-out either.
- Bereavement and jury duty can cover several days. Each day is its own absence (row 38 is BRV on 10/22 and 10/23). More than 3 bereavement days goes to review.
- Row 31 has no VM Received time, so I can't prove the notice. Held for review. Row 46's employee is PENDING_SETUP in payroll, so payroll would reject it. Held too.
- Dispatch posting history can assign, unassign and reassign. The last assignment is the one that counts (e.g. P-1009-009 ended with W-5629 on OT).
- Dispatch: a cancelled posting wasn't needed, so it isn't counted. An expired one was needed but nobody took it, so it counts as unfilled.
- Some October invoices are dated in November. Revenue follows the service period, not the invoice date.
- A credit memo with no invoice stays open at its own (negative) amount, aged from its own date.
- data_guide.md mentions `billing_note_formats.md`, but the file is `backup_note_templates.md`. Same content, so I used it.
