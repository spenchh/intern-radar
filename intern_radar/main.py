"""Entry point: fetch -> match -> notify -> publish.

    python -m intern_radar.main                 # normal run
    python -m intern_radar.main --dry-run       # match and report, send nothing
    python -m intern_radar.main --explain       # show why postings were rejected
    python -m intern_radar.main --backfill      # alert on everything already known
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import dashboard, notify, sources
from .matcher import Matcher, filter_jobs
from .store import Store

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("intern_radar")


def load_config(path: Path) -> dict:
    if not path.exists():
        sys.exit(f"config not found: {path}")
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="intern-radar", description=__doc__)
    p.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    p.add_argument("--state", type=Path, default=ROOT / "data" / "seen.json")
    p.add_argument("--docs", type=Path, default=ROOT / "docs")
    p.add_argument("--dry-run", action="store_true", help="do not send or save anything")
    p.add_argument("--explain", action="store_true", help="print match/reject breakdown")
    p.add_argument("--backfill", action="store_true", help="alert on existing matches too")
    p.add_argument("--no-dashboard", action="store_true")
    p.add_argument("--limit", type=int, default=None, help="override max alerts per run")
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )

    cfg = load_config(args.config)
    started = time.time()

    # 1. Fetch every source concurrently.
    raw = sources.fetch_all(cfg.get("sources") or {})
    if not raw:
        log.error("no postings fetched - every source failed; aborting without changes")
        return 1

    # 2. Score and filter.
    matcher = Matcher(cfg.get("matching") or {})
    matched, stats = filter_jobs(raw, matcher)
    log.info("%d of %d postings matched", len(matched), len(raw))

    if args.explain:
        print("\n--- filter breakdown " + "-" * 40)
        for reason, count in sorted(stats.items(), key=lambda kv: -kv[1]):
            print(f"  {count:6d}  {reason}")
        print("\n--- top matches " + "-" * 45)
        for job in sorted(matched, key=lambda j: -j.score)[:25]:
            terms = ", ".join(r.rsplit(" (", 1)[0] for r in job.reasons[:5])
            print(f"  [{job.score:>2}] {job.company:<24.24} {job.title:<58.58} {terms}")
        print()

    # 3. Work out what is genuinely new.
    store = Store(args.state)
    store.hydrate(matched)
    fresh = store.new_among(matched)

    notify_cfg = cfg.get("notify") or {}
    cap = args.limit if args.limit is not None else int(notify_cfg.get("max_per_run", 25))
    webhook = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()

    seeding = store.is_first_run and not notify_cfg.get("on_first_run", False) and not args.backfill
    if args.backfill:
        fresh = sorted(matched, key=lambda j: j.posted_at or 0, reverse=True)

    log.info("%d new since last run", len(fresh))

    # 4. Notify.
    to_send: list = []
    if seeding:
        # First ever run: record what exists rather than firing hundreds of alerts.
        log.info("first run - seeding %d postings without alerting", len(matched))
    elif fresh:
        to_send = fresh[:cap]
        if len(fresh) > cap:
            # The remainder stay unmarked, so they go out next run rather than vanish.
            log.info("capping at %d alerts; %d will follow next run", cap, len(fresh) - cap)

    if args.dry_run:
        for job in to_send:
            log.info("WOULD ALERT: %-22.22s %s", job.company, job.title)
        log.info("dry run - no alerts sent, no state written")
    else:
        if to_send:
            if not webhook:
                log.error(
                    "DISCORD_WEBHOOK_URL is not set - %d alerts not delivered. "
                    "State left untouched so they retry next run.", len(to_send)
                )
                to_send = []
            else:
                delivered = notify.send(webhook, to_send, notify_cfg.get("colors") or {})
                if delivered == 0:
                    log.error("delivery failed; leaving state untouched to retry")
                    to_send = []

        # 5. Persist. Seeding marks everything; a normal run marks only what was sent.
        store.mark_seen(matched if seeding else to_send)
        store.hydrate(matched)
        store.prune(
            int((cfg.get("store") or {}).get("forget_after_days", 150)),
            {j.uid for j in matched},
        )
        store.save()

    # 6. Publish the dashboard.
    if not args.no_dashboard:
        store.hydrate(matched)
        dashboard.render(matched, args.docs)

    log.info(
        "done in %.1fs | %d tracked | %d new | %d alerted | %s",
        time.time() - started,
        len(matched),
        len(fresh),
        len(to_send),
        datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
