"""Job sources.

Two tiers, fetched concurrently:

  1. Simplify  - a broad community-maintained aggregate feed (thousands of roles).
  2. ATS APIs  - Greenhouse / Lever / Ashby public job-board endpoints, read
                 straight from the employer. This is the fastest possible signal:
                 a posting appears here the instant it goes live, typically well
                 before any aggregator indexes it.

Every parser is defensive. A malformed record is skipped, a dead board returns
an empty list, and neither aborts the run.
"""

from __future__ import annotations

import calendar
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from .http import get_json
from .models import Job

log = logging.getLogger(__name__)

# Slugs are lowercase and punctuation-free; these render them the way a human
# writes them. Anything not listed falls back to title-casing the slug.
DISPLAY_NAMES = {
    "rocketlab": "Rocket Lab",
    "ursamajor": "Ursa Major",
    "muonspace": "Muon Space",
    "asteralabs": "Astera Labs",
    "sambanovasystems": "SambaNova",
    "pacificfusion": "Pacific Fusion",
    "kairospower": "Kairos Power",
    "redwoodmaterials": "Redwood Materials",
    "lucidmotors": "Lucid Motors",
    "agilityrobotics": "Agility Robotics",
    "shieldai": "Shield AI",
    "atomcomputing": "Atom Computing",
    "base-power": "Base Power",
    "formenergy": "Form Energy",
    "physicalintelligence": "Physical Intelligence",
    "psiquantum": "PsiQuantum",
    "ionq": "IonQ",
    "1x": "1X",
    "vast": "Vast",
    "relativity": "Relativity Space",
    "aeva": "Aeva",
    "sila": "Sila",
    "xcimer": "Xcimer Energy",
    "etched": "Etched",
    "saronic": "Saronic",
    "skydio": "Skydio",
    "helion": "Helion Energy",
    "cerebras": "Cerebras",
    "axelera": "Axelera AI",
    "cobot": "Cobot",
    "hermeus": "Hermeus",
    "zoox": "Zoox",
    "rigetti": "Rigetti",
    "epirus": "Epirus",
    "astranis": "Astranis",
    "lattice": "Lattice Semiconductor",
    "graphcore": "Graphcore",
    "tenstorrent": "Tenstorrent",
    "lightmatter": "Lightmatter",
    "formlabs": "Formlabs",
    "markforged": "Markforged",
    "carbon": "Carbon",
    "oklo": "Oklo",
    "archer": "Archer Aviation",
    "waymo": "Waymo",
    "nuro": "Nuro",
    "kodiak": "Kodiak Robotics",
    "figure": "Figure",
    "peloton": "Peloton",
}


def pretty(slug: str) -> str:
    if slug in DISPLAY_NAMES:
        return DISPLAY_NAMES[slug]
    return slug.replace("-", " ").replace("_", " ").title()


def _parse_iso(value: str | None) -> int | None:
    """Parse an ISO-8601 timestamp into epoch seconds."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    # Trim fractional seconds to 6 digits, which is all fromisoformat accepts.
    text = re.sub(r"\.(\d{6})\d+", r".\1", text)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return calendar.timegm(dt.utctimetuple())


def _as_list(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(v) for v in value if v]
    return []


# --------------------------------------------------------------------------
# Simplify aggregate feed
# --------------------------------------------------------------------------
def fetch_simplify(url: str) -> list[Job]:
    data = get_json(url, timeout=60)
    if not isinstance(data, list):
        log.warning("simplify: unexpected payload from %s", url)
        return []

    jobs: list[Job] = []
    for rec in data:
        if not isinstance(rec, dict):
            continue
        # The feed keeps historical rows around; skip anything retired.
        if rec.get("active") is False or rec.get("is_visible") is False:
            continue
        title = rec.get("title") or ""
        company = rec.get("company_name") or ""
        link = rec.get("url") or rec.get("company_url") or ""
        if not title or not link:
            continue

        posted = rec.get("date_posted") or rec.get("date_updated")
        jobs.append(
            Job(
                source="Simplify",
                company=company,
                title=title,
                url=link,
                locations=_as_list(rec.get("locations")),
                posted_at=int(posted) if isinstance(posted, (int, float)) else None,
                terms=_as_list(rec.get("terms")),
                sponsorship=str(rec.get("sponsorship") or ""),
                category=str(rec.get("category") or ""),
            )
        )

    log.info("simplify: %d active postings", len(jobs))
    return jobs


# --------------------------------------------------------------------------
# Employer ATS boards
# --------------------------------------------------------------------------
def fetch_greenhouse(slug: str) -> list[Job]:
    data = get_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs")
    if not isinstance(data, dict):
        return []

    jobs = []
    for rec in data.get("jobs") or []:
        title = rec.get("title")
        link = rec.get("absolute_url")
        if not title or not link:
            continue
        locs = []
        if isinstance(rec.get("location"), dict):
            locs = _as_list(rec["location"].get("name"))
        jobs.append(
            Job(
                source="Greenhouse",
                company=pretty(slug),
                title=title,
                url=link,
                locations=locs,
                posted_at=_parse_iso(rec.get("first_published") or rec.get("updated_at")),
            )
        )
    return jobs


def fetch_lever(slug: str) -> list[Job]:
    data = get_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")
    if not isinstance(data, list):
        return []

    jobs = []
    for rec in data:
        title = rec.get("text")
        link = rec.get("hostedUrl") or rec.get("applyUrl")
        if not title or not link:
            continue
        cats = rec.get("categories") or {}
        locs = _as_list(cats.get("location"))
        for extra in rec.get("additionalLocations") or []:
            if extra not in locs:
                locs.append(str(extra))
        created = rec.get("createdAt")
        jobs.append(
            Job(
                source="Lever",
                company=pretty(slug),
                title=title,
                url=link,
                locations=locs,
                # Lever reports milliseconds.
                posted_at=int(created / 1000) if isinstance(created, (int, float)) else None,
                category=str(cats.get("team") or ""),
            )
        )
    return jobs


def fetch_ashby(slug: str) -> list[Job]:
    data = get_json(f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    if not isinstance(data, dict):
        return []

    jobs = []
    for rec in data.get("jobs") or []:
        if rec.get("isListed") is False:
            continue
        title = rec.get("title")
        link = rec.get("jobUrl") or rec.get("applyUrl")
        if not title or not link:
            continue
        locs = _as_list(rec.get("location"))
        for extra in rec.get("secondaryLocations") or []:
            name = extra.get("location") if isinstance(extra, dict) else extra
            if name and name not in locs:
                locs.append(str(name))
        jobs.append(
            Job(
                source="Ashby",
                company=pretty(slug),
                title=title,
                url=link,
                locations=locs,
                posted_at=_parse_iso(rec.get("publishedAt") or rec.get("updatedAt")),
                category=str(rec.get("department") or rec.get("team") or ""),
            )
        )
    return jobs


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------
_ATS = {
    "greenhouse": fetch_greenhouse,
    "lever": fetch_lever,
    "ashby": fetch_ashby,
}


def fetch_all(cfg: dict, max_workers: int = 16) -> list[Job]:
    """Fetch every configured source concurrently and return one flat list."""
    tasks: list[tuple[str, callable, str]] = []

    simplify = cfg.get("simplify") or {}
    if simplify.get("enabled", True):
        for url in simplify.get("urls") or []:
            parts = [p for p in url.split("/") if p]
            label = parts[4] if len(parts) > 4 else "feed"
            tasks.append((f"simplify:{label}", fetch_simplify, url))

    for ats, fn in _ATS.items():
        block = cfg.get(ats) or {}
        if not block.get("enabled", True):
            continue
        for slug in block.get("companies") or []:
            tasks.append((f"{ats}:{slug}", fn, str(slug)))

    if not tasks:
        return []

    started = time.time()
    results: list[Job] = []
    ok = 0

    def run(task):
        label, fn, arg = task
        try:
            return label, fn(arg)
        except Exception as e:  # noqa: BLE001 - one bad board must not kill the run
            log.warning("source %s failed: %s", label, e)
            return label, []

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for label, jobs in pool.map(run, tasks):
            if jobs:
                ok += 1
                results.extend(jobs)
            log.debug("%s -> %d", label, len(jobs))

    log.info(
        "fetched %d postings from %d/%d sources in %.1fs",
        len(results), ok, len(tasks), time.time() - started,
    )
    return results
