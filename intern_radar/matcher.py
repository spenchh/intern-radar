"""Decide whether a posting is a hardware internship you care about.

Design notes
------------
* Titles are normalized (lowercased, punctuation -> spaces) before matching, so
  "Mixed-Signal", "mixed signal" and "MIXED_SIGNAL" all collapse to one form.
* Every term is matched on word boundaries. This is why "Internal Audit" and
  "International Sales" are not treated as internships - a naive substring
  search on "intern" matches both.
* Filters run cheapest-first: the internship check alone discards ~90% of the
  feed before any scoring work happens.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Job

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# "co-op" and "co op" both normalize to "co op"; "summer analyst" is how a few
# hardware companies label seasonal intern roles.
INTERNSHIP_RE = re.compile(
    r"\b(intern|interns|internship|internships|co op|coop|summer analyst)\b"
)
YEAR_RE = re.compile(r"\b(20\d{2})\b")

# Terms that colour the alert and group the dashboard.
_SILICON = {
    "rtl", "asic", "fpga", "vlsi", "verilog", "systemverilog", "vhdl", "uvm",
    "soc", "tapeout", "tape out", "silicon", "semiconductor", "digital design",
    "digital logic", "logic design", "physical design", "design verification",
    "ic design", "integrated circuit", "chip design", "chip", "dft",
    "static timing", "place and route", "serdes", "cmos", "microelectronics",
    "post silicon", "pre silicon", "processor design", "cpu design",
    "gpu design", "memory design", "mixed signal", "analog design",
}
_AEROSPACE = {
    "avionics", "propulsion", "flight hardware", "spacecraft", "satellite",
    "launch vehicle", "aerospace", "aerodynamics", "structures", "mechanisms",
}
_ELECTRICAL = {
    "electrical", "electronics", "electrical engineering", "pcb", "schematic",
    "board design", "signal integrity", "power electronics", "power integrity",
    "rf", "radio frequency", "antenna", "circuit design", "analog", "layout",
}


def normalize(text: str) -> str:
    return _NON_ALNUM.sub(" ", (text or "").lower()).strip()


@dataclass
class Verdict:
    matched: bool
    score: int
    reasons: list[str]
    category: str
    rejected_by: str = ""


class Matcher:
    def __init__(self, cfg: dict):
        self.require_internship = cfg.get("require_internship", True)
        self.min_score = int(cfg.get("min_score", 2))
        self.allowed_years = {int(y) for y in (cfg.get("allowed_years") or [])}
        self.exclude_sponsorship = [
            normalize(s) for s in (cfg.get("exclude_sponsorship") or [])
        ]
        self.allowed_locations = [
            str(s).lower().strip() for s in (cfg.get("allowed_locations") or []) if s
        ]
        self.excluded_locations = [
            str(s).lower().strip() for s in (cfg.get("excluded_locations") or []) if s
        ]

        # Normalization collapses spelling variants ("mixed-signal" and
        # "mixed signal" both become "mixed signal"), so dedupe afterwards or a
        # term would be counted once per variant and inflate the score. Where
        # variants disagree on weight, the strongest signal wins.
        best: dict[str, int] = {}
        for weight, terms in (cfg.get("weights") or {}).items():
            w = int(weight)
            for term in terms or []:
                norm = normalize(str(term))
                if not norm:
                    continue
                if norm not in best or abs(w) > abs(best[norm]):
                    best[norm] = w

        # weight, compiled regex, display term
        self.weighted: list[tuple[int, re.Pattern, str]] = [
            (w, self._term_re(t), t) for t, w in best.items()
        ]

        self.vetoes: list[tuple[re.Pattern, str]] = []
        for term in cfg.get("veto") or []:
            norm = normalize(str(term))
            if norm:
                self.vetoes.append((self._term_re(norm), norm))

    @staticmethod
    def _term_re(norm_term: str) -> re.Pattern:
        return re.compile(rf"\b{re.escape(norm_term)}\b")

    # ----------------------------------------------------------------- checks
    def _year_ok(self, haystack: str) -> bool:
        if not self.allowed_years:
            return True
        found = {int(y) for y in YEAR_RE.findall(haystack)}
        # A posting that names no year at all is kept - most ATS titles omit it.
        return not found or bool(found & self.allowed_years)

    def evaluate(self, job: Job) -> Verdict:
        title_n = normalize(job.title)
        terms_n = normalize(" ".join(job.terms))
        haystack = f"{title_n} {terms_n}".strip()

        # 1. Is it an internship at all? Cheapest and most selective.
        if self.require_internship and not INTERNSHIP_RE.search(haystack):
            return Verdict(False, 0, [], "", "not an internship")

        # 2. Year window.
        if not self._year_ok(haystack):
            return Verdict(False, 0, [], "", "wrong year")

        # 3. Hard vetoes - software and non-engineering roles.
        for rx, term in self.vetoes:
            if rx.search(title_n):
                return Verdict(False, 0, [], "", f"veto: {term}")

        # 4. Sponsorship exclusions.
        sponsor_n = normalize(job.sponsorship)
        for bad in self.exclude_sponsorship:
            if bad and bad in sponsor_n:
                return Verdict(False, 0, [], "", f"sponsorship: {job.sponsorship}")

        # 5. Location. A posting with no stated location is never dropped here -
        #    plenty of real listings omit it, and dropping them loses good roles.
        if job.locations:
            locs = [loc.lower() for loc in job.locations]
            if self.allowed_locations and not any(
                want in loc for loc in locs for want in self.allowed_locations
            ):
                return Verdict(False, 0, [], "", "location not allowed")
            if self.excluded_locations and all(
                any(bad in loc for bad in self.excluded_locations) for loc in locs
            ):
                return Verdict(False, 0, [], "", "location excluded")

        # 6. Weighted hardware score.
        score = 0
        hits: list[str] = []
        for weight, rx, term in self.weighted:
            if rx.search(haystack):
                score += weight
                hits.append(f"{term} ({weight:+d})")

        if score < self.min_score:
            return Verdict(False, score, hits, "", f"score {score} < {self.min_score}")

        return Verdict(True, score, hits, self._categorize(hits))

    @staticmethod
    def _categorize(hits: list[str]) -> str:
        terms = {h.rsplit(" (", 1)[0] for h in hits}
        if terms & _SILICON:
            return "silicon"
        if terms & _AEROSPACE:
            return "aerospace"
        if terms & _ELECTRICAL:
            return "electrical"
        return "other"


def filter_jobs(jobs: list[Job], matcher: Matcher) -> tuple[list[Job], dict[str, int]]:
    """Score every posting, keep the matches, and collapse cross-source duplicates.

    When the same role arrives from both Simplify and the employer's own board,
    the employer record wins: it carries the canonical apply URL.
    """
    stats: dict[str, int] = {}
    by_uid: dict[str, Job] = {}

    for job in jobs:
        verdict = matcher.evaluate(job)
        if not verdict.matched:
            reason = verdict.rejected_by.split(":")[0].strip()
            stats[reason] = stats.get(reason, 0) + 1
            continue

        job.score = verdict.score
        job.reasons = verdict.reasons
        if verdict.category:
            job.category = verdict.category

        existing = by_uid.get(job.uid)
        if existing is None:
            by_uid[job.uid] = job
            continue

        stats["duplicate"] = stats.get("duplicate", 0) + 1
        if existing.source == "Simplify" and job.source != "Simplify":
            # Keep the richer Simplify metadata we would otherwise lose.
            job.terms = job.terms or existing.terms
            job.sponsorship = job.sponsorship or existing.sponsorship
            job.posted_at = job.posted_at or existing.posted_at
            by_uid[job.uid] = job
        elif existing.posted_at is None and job.posted_at is not None:
            existing.posted_at = job.posted_at

    stats["matched"] = len(by_uid)
    return list(by_uid.values()), stats
