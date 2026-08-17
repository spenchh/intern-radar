"""Persistent record of every posting already seen, so alerts fire exactly once."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path

from .models import Job

log = logging.getLogger(__name__)


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.jobs: dict[str, dict] = {}
        self.last_run: int | None = None
        self.runs: int = 0
        self._existed = path.exists()
        self._load()

    @property
    def is_first_run(self) -> bool:
        """True when there is no prior state - used to avoid a mass backfill alert."""
        return not self._existed or not self.jobs

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 - corrupt state should not be fatal
            log.warning("could not read %s (%s); starting fresh", self.path, e)
            return
        self.jobs = data.get("jobs") or {}
        self.last_run = data.get("last_run")
        self.runs = int(data.get("runs") or 0)
        log.info("loaded %d known postings from %s", len(self.jobs), self.path.name)

    def new_among(self, jobs: list[Job]) -> list[Job]:
        """Return only postings never recorded before, newest first."""
        fresh = [j for j in jobs if j.uid not in self.jobs]
        fresh.sort(key=lambda j: j.posted_at or 0, reverse=True)
        return fresh

    def mark_seen(self, jobs: list[Job], now: int | None = None) -> None:
        now = now or int(time.time())
        for job in jobs:
            self.jobs[job.uid] = {
                "first_seen": now,
                "company": job.company,
                "title": job.title,
                "url": job.url,
            }
            job.first_seen = now

    def hydrate(self, jobs: list[Job]) -> None:
        """Attach the stored first_seen timestamp to postings we already know."""
        for job in jobs:
            rec = self.jobs.get(job.uid)
            if rec and rec.get("first_seen"):
                job.first_seen = rec["first_seen"]

    def prune(self, forget_after_days: int, active_uids: set[str]) -> int:
        """Drop long-gone postings so the state file does not grow without bound."""
        if forget_after_days <= 0:
            return 0
        cutoff = int(time.time()) - forget_after_days * 86400
        stale = [
            uid
            for uid, rec in self.jobs.items()
            if uid not in active_uids and int(rec.get("first_seen") or 0) < cutoff
        ]
        for uid in stale:
            del self.jobs[uid]
        if stale:
            log.info("pruned %d stale entries", len(stale))
        return len(stale)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.runs += 1
        payload = {
            "last_run": int(time.time()),
            "runs": self.runs,
            "count": len(self.jobs),
            "jobs": self.jobs,
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=True), encoding="utf-8")
        tmp.replace(self.path)
        log.info("saved %d postings to %s", len(self.jobs), self.path.name)
