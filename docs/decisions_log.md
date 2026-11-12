# Decision Log

## Day 1

- Set up the project so all settings come from environment variables, with DRY_RUN on by default so nothing gets written to payroll by accident. Code is written with Claude Code (Opus 5.5) and reviewed by me.
- Started the API clients with payroll, using Claude as well, and had it leave a short comment on each function. Reads retry on 429/5xx with backoff; the payroll write is sent once, since a resend after a lost reply could duplicate an absence.
- Every feature gets its own tests in the same commit, using fake HTTP replies, so the behaviour is checked without the real systems running.
- The dispatch token is refreshed 30s before it expires and retried only once on a 401, so a bad secret fails loudly instead of looping; 429s wait for the server's Retry-After.
- Reading the client files: the HR roster, both time clock layouts and both call-out sources are read into one shape each. Layouts and headers are detected from the content, not file names or column positions, so another month's export still works.
- Contracts and finance reports are read with plain code, not the LLM, since they follow fixed templates. Every finance report is checked against its own printed totals; a mismatch goes to exceptions instead of being hidden.
- Buildings use the Schedule B names from the contracts. Other spellings are matched by their words (TWR = TOWER, 1 = ONE); codes with no name (HR's BLD-xx) are mapped by where those people actually punch in. Ambiguous names like "Bramwell" are never guessed.
- People are matched across HR, time clock, payroll and dispatch. Shared IDs are trusted only when the names agree (dispatch's hr_employee_no is typed by hand); messy names like "Angela B." or "Brown, Angela" are matched with building and shift as tie-breakers, and two people with the same name are left for review instead of guessed.
- Day words ("tonight", "Friday", "10/22") are turned into dates by code, counted from when the employee actually called, not when reception typed it. A voicemail after midnight saying "tomorrow" means the coming shift, flagged with lower confidence. Notice is hours from that call to the shift start (day 07:00, night 16:00).
