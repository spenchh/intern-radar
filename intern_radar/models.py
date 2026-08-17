"""Core data model shared by every source."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from typing import Any

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[^a-z0-9 ]+")


def normalize(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace, for stable hashing."""
    return _WS.sub(" ", _PUNCT.sub(" ", (text or "").lower())).strip()


@dataclass
class Job:
    source: str
    company: str
    title: str
    url: str
    locations: list[str] = field(default_factory=list)
    posted_at: int | None = None          # epoch seconds
    terms: list[str] = field(default_factory=list)
    sponsorship: str = ""
    category: str = ""

    # Filled in by the pipeline.
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    first_seen: int | None = None

    @property
    def uid(self) -> str:
        """Stable identity across sources.

        Deliberately excludes the URL and source: the same posting reached via
        Simplify and via the company's own Greenhouse board must collapse to one
        entry, otherwise you get notified twice for the same job.

        Two further normalizations, both driven by duplicates seen in real data:

        * Title words are sorted, so "Intern, Optical Packaging Engineer" and
          "Optical Packaging Engineer Intern" hash alike.
        * Only the city is kept, so "San Mateo, CA" and "San Mateo, California,
          United States" hash alike. The city is still part of the key, so the
          same role genuinely opened in Chicago and London stays two entries.
        """
        title_key = " ".join(sorted(normalize(self.title).split()))
        city = normalize(self.locations[0].split(",")[0]) if self.locations else ""
        key = f"{normalize(self.company)}|{title_key}|{city}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]

    @property
    def location_str(self) -> str:
        if not self.locations:
            return "Not specified"
        if len(self.locations) <= 2:
            return " / ".join(self.locations)
        return f"{self.locations[0]} +{len(self.locations) - 1} more"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["uid"] = self.uid
        return d
