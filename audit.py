"""Print every current match, grouped by category, to sanity-check the filters.

    python audit.py            # all matches
    python audit.py rejected   # sample of near-misses, to spot false negatives
"""

import sys
from pathlib import Path

import yaml

from intern_radar import sources
from intern_radar.matcher import Matcher, filter_jobs

cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
raw = sources.fetch_all(cfg["sources"])
matcher = Matcher(cfg["matching"])

if len(sys.argv) > 1 and sys.argv[1] == "rejected":
    # Near-misses: internship postings that scored above zero but under the bar.
    near = []
    for job in raw:
        v = matcher.evaluate(job)
        if not v.matched and v.rejected_by.startswith("score") and v.score > 0:
            near.append((v.score, job))
    near.sort(key=lambda t: -t[0])
    print(f"\n{len(near)} near-misses (scored but below min_score):\n")
    for score, job in near[:60]:
        print(f"  [{score:>2}] {job.company:<22.22} {job.title:<70.70}")
    raise SystemExit

matched, stats = filter_jobs(raw, matcher)
groups: dict[str, list] = {}
for job in matched:
    groups.setdefault(job.category or "other", []).append(job)

print(f"\n{'=' * 100}\n{len(matched)} MATCHES\n{'=' * 100}")
for cat in ("silicon", "electrical", "aerospace", "other"):
    rows = sorted(groups.get(cat, []), key=lambda j: -j.score)
    if not rows:
        continue
    print(f"\n### {cat.upper()}  ({len(rows)})\n")
    for job in rows:
        loc = job.location_str[:28]
        print(f"  [{job.score:>2}] {job.company:<22.22} {job.title:<62.62} {loc}")

print(f"\n{'=' * 100}")
for reason, count in sorted(stats.items(), key=lambda kv: -kv[1]):
    print(f"  {count:6d}  {reason}")
