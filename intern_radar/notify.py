"""Discord notifications.

Discord accepts at most 10 embeds per webhook message, so alerts are batched.
Each embed links straight to the application page.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

from .http import post_json
from .models import Job

log = logging.getLogger(__name__)

EMBEDS_PER_MESSAGE = 10
DEFAULT_COLORS = {
    "silicon": 0x7C3AED,
    "aerospace": 0x0EA5E9,
    "electrical": 0x10B981,
    "other": 0x64748B,
}
CATEGORY_LABEL = {
    "silicon": "Silicon / RTL",
    "aerospace": "Aerospace HW",
    "electrical": "Electrical",
    "other": "Hardware",
}


def _fmt_date(epoch: int | None) -> str:
    if not epoch:
        return "Unknown"
    try:
        return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%b %d, %Y")
    except (OverflowError, OSError, ValueError):
        return "Unknown"


def _embed(job: Job, colors: dict) -> dict:
    color = colors.get(job.category, DEFAULT_COLORS.get(job.category, 0x64748B))

    fields = [
        {"name": "Company", "value": job.company or "Unknown", "inline": True},
        {"name": "Location", "value": job.location_str, "inline": True},
        {"name": "Posted", "value": _fmt_date(job.posted_at), "inline": True},
    ]
    if job.terms:
        fields.append({"name": "Term", "value": ", ".join(job.terms[:3]), "inline": True})
    if job.sponsorship and job.sponsorship.lower() != "other":
        # Worth surfacing: a lot of hardware and defense roles are ITAR-restricted.
        fields.append({"name": "Sponsorship", "value": job.sponsorship[:100], "inline": True})
    if job.reasons:
        matched = ", ".join(r.rsplit(" (", 1)[0] for r in job.reasons[:6])
        fields.append({"name": "Matched on", "value": f"`{matched}`", "inline": False})

    return {
        "title": job.title[:250],
        "url": job.url,
        "color": color,
        "fields": fields,
        "footer": {
            "text": f"{CATEGORY_LABEL.get(job.category, 'Hardware')} "
                    f"| score {job.score} | via {job.source}"
        },
    }


def send(webhook_url: str, jobs: list[Job], colors: dict | None = None) -> int:
    """Post alerts for `jobs`. Returns how many were delivered."""
    if not jobs:
        return 0
    if not webhook_url:
        log.warning("no webhook configured; skipping %d alerts", len(jobs))
        return 0

    colors = colors or DEFAULT_COLORS
    delivered = 0
    batches = [
        jobs[i:i + EMBEDS_PER_MESSAGE]
        for i in range(0, len(jobs), EMBEDS_PER_MESSAGE)
    ]

    for index, batch in enumerate(batches):
        content = ""
        if index == 0:
            plural = "opening" if len(jobs) == 1 else "openings"
            content = f"**{len(jobs)} new hardware internship {plural}**"

        status, body = post_json(
            webhook_url,
            {
                "username": "Intern Radar",
                "content": content,
                "embeds": [_embed(j, colors) for j in batch],
            },
        )

        if 200 <= status < 300:
            delivered += len(batch)
        else:
            log.error("discord batch %d failed: HTTP %s %s", index + 1, status, body[:300])

        if index < len(batches) - 1:
            time.sleep(1.0)  # stay well inside the webhook rate limit

    log.info("delivered %d/%d alerts", delivered, len(jobs))
    return delivered


def send_heartbeat(webhook_url: str, message: str) -> bool:
    if not webhook_url:
        return False
    status, _ = post_json(
        webhook_url, {"username": "Intern Radar", "content": message}
    )
    return 200 <= status < 300
