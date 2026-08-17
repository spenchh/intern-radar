"""Minimal HTTP helpers built on the standard library.

Deliberately dependency-free so the poller keeps working even if a pinned
third-party HTTP library breaks in CI.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) intern-radar/1.0 "
    "(+https://github.com/) personal internship monitor"
)


def get_json(
    url: str,
    *,
    timeout: int = 30,
    retries: int = 3,
    backoff: float = 2.0,
    headers: dict[str, str] | None = None,
) -> Any:
    """GET a URL and parse JSON, retrying transient failures.

    Returns None if every attempt fails - a single dead board must never abort
    the whole run.
    """
    req_headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    last_err: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=req_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            last_err = e
            # 404 means the board slug is wrong; retrying will not help.
            if e.code in (404, 410):
                log.debug("%s -> HTTP %s (permanent)", url, e.code)
                return None
        except Exception as e:  # noqa: BLE001 - network layer, log and retry
            last_err = e

        if attempt < retries - 1:
            time.sleep(backoff * (attempt + 1))

    log.warning("giving up on %s: %s", url, last_err)
    return None


def post_json(
    url: str,
    payload: Any,
    *,
    timeout: int = 20,
    retries: int = 4,
) -> tuple[int, str]:
    """POST JSON, honouring Discord-style 429 Retry-After. Returns (status, body)."""
    body = json.dumps(payload).encode("utf-8")
    headers = {"User-Agent": USER_AGENT, "Content-Type": "application/json"}

    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", errors="replace")
            if e.code == 429 and attempt < retries - 1:
                wait = 5.0
                try:
                    wait = float(json.loads(text).get("retry_after", 5.0))
                except Exception:  # noqa: BLE001
                    pass
                log.info("rate limited, sleeping %.1fs", wait)
                time.sleep(min(wait + 0.5, 30))
                continue
            if 500 <= e.code < 600 and attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            return e.code, text
        except Exception as e:  # noqa: BLE001
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
                continue
            return 0, str(e)

    return 0, "exhausted retries"
