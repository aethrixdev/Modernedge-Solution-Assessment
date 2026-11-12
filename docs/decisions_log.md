# Decision Log

## Day 1

- Set up the project so all settings come from environment variables, with DRY_RUN on by default so nothing gets written to payroll by accident. Code is written with Claude Code (Opus 5.5) and reviewed by me.
- Started the API clients with payroll, using Claude as well, and had it leave a short comment on each function. Reads retry on 429/5xx with backoff; the payroll write is sent once, since a resend after a lost reply could duplicate an absence.
- Every feature gets its own tests in the same commit, using fake HTTP replies, so the behaviour is checked without the real systems running.
- The dispatch token is refreshed 30s before it expires and retried only once on a 401, so a bad secret fails loudly instead of looping; 429s wait for the server's Retry-After.
