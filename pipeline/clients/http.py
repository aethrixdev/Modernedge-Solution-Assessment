"""One shared helper for sending HTTP requests with retries.

All three clients use `send()`, so retry behaviour lives in one place.

Retry rules:
- Network errors (connection refused, timeout) are retried.
- 429 (too many requests) and 5xx (server busy or broken) are retried.
- We wait 0.5s, 1s, 2s, 4s ... between tries (exponential backoff), capped.
- Anything else (200, 404, 422 ...) is returned to the caller right away.

`retry=False` turns retries off. The payroll POST uses that, because blindly
re-sending a write can create a duplicate absence.
"""
import logging
import time

import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}

# Tests replace this with a no-op so they don't actually wait.
sleep = time.sleep


def backoff_delay(attempt: int, base: float = 0.5, cap: float = 30.0) -> float:
    """Attempt 1 -> 0.5s, 2 -> 1s, 3 -> 2s, ... never more than `cap`."""
    return min(cap, base * (2 ** (attempt - 1)))


def send(
    session: requests.Session,
    method: str,
    url: str,
    *,
    retry: bool = True,
    max_attempts: int = 6,
    timeout: float = 30,
    **kwargs,
) -> requests.Response:
    """Send one HTTP request, retrying network errors, 429 and 5xx with backoff (unless retry=False)."""
    attempt = 0
    while True:
        attempt += 1
        try:
            resp = session.request(method, url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if not retry or attempt >= max_attempts:
                raise
            delay = backoff_delay(attempt)
            log.warning("%s %s failed (%s); retry %d in %.1fs", method, url, exc, attempt, delay)
            sleep(delay)
            continue

        if retry and resp.status_code in RETRY_STATUSES and attempt < max_attempts:
            delay = backoff_delay(attempt)
            log.warning("%s %s -> %d; retry %d in %.1fs", method, url, resp.status_code, attempt, delay)
            sleep(delay)
            continue

        return resp
