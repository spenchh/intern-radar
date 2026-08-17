"""Offline checks: Discord payload legality, dashboard integrity, matcher behaviour.

    python selftest.py

Runs without network access except for the dashboard check, which reads the
already-generated docs/ output.
"""

import json
import re
import sys
from pathlib import Path

import yaml

from intern_radar.matcher import Matcher
from intern_radar.models import Job
from intern_radar.notify import _embed

failures = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    if not ok:
        failures.append(label)


cfg = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
matcher = Matcher(cfg["matching"])

# ---------------------------------------------------------------- matcher
print("\nMatcher")

CASES = [
    # (title, should_match)
    ("RTL Design Engineer Intern", True),
    ("ASIC Design Verification Co-op - Summer 2027", True),
    ("FPGA Engineer Intern", True),
    ("Analog Mixed-Signal Layout Intern", True),
    ("Computer Engineering Intern", True),
    ("Avionics Hardware Intern", True),
    ("Electrical Engineering Internship - Summer 2027", True),
    ("Physical Design Engineer Intern", True),
    # Must NOT match - software and non-engineering.
    ("Software Engineer Intern", False),
    ("Software Engineering Intern - Backend", False),
    ("Embedded Software Engineer Intern", False),
    ("Flight Software Engineering Intern - Fall 2026", False),
    ("Machine Learning Engineer Intern", False),
    ("Data Scientist Intern", False),
    ("Frontend Developer Intern", False),
    ("Marketing Intern", False),
    ("Internal Audit Specialist", False),          # "Internal" is not "intern"
    ("International Business Development Manager", False),
    ("Senior Manager, Internal Communications", False),
    ("Electronics Technician Intern", False),
    ("Hardware Engineer", False),                  # not an internship
]

for title, expected in CASES:
    got = matcher.evaluate(Job(source="t", company="Test", title=title, url="u")).matched
    check(f"{'match' if expected else 'reject'}: {title!r}", got == expected)

# Year filtering.
old = matcher.evaluate(Job(source="t", company="T", title="RTL Intern Summer 2019", url="u"))
check("rejects out-of-window year", not old.matched, old.rejected_by)

# ---------------------------------------------------------------- identity
print("\nDeduplication")

def uid(title, loc):
    return Job(source="s", company="Etched", title=title, url="u", locations=[loc]).uid

check("city vs city+state collapse", uid("RTL Intern", "San Jose") == uid("RTL Intern", "San Jose, CA"))
check("verbose location collapses",
      uid("HW Intern", "San Mateo, CA") == uid("HW Intern", "San Mateo, California, United States"))
check("word order collapses",
      uid("Intern, Optical Engineer", "SF") == uid("Optical Engineer Intern", "SF"))
check("different cities stay distinct", uid("FPGA Intern", "Chicago") != uid("FPGA Intern", "London"))
check("different roles stay distinct", uid("RTL Intern", "SF") != uid("DFT Intern", "SF"))

# ---------------------------------------------------------------- discord
print("\nDiscord payload limits")

sample = Job(
    source="Greenhouse",
    company="A" * 300,
    title="B" * 400,
    url="https://example.com/apply",
    locations=["C" * 200] * 12,
    posted_at=1766370010,
    terms=["Summer 2027", "Fall 2026", "Spring 2027", "Summer 2026"],
    sponsorship="D" * 400,
)
sample.score = 9
sample.reasons = [f"term{i} (+3)" for i in range(40)]
sample.category = "silicon"

embed = _embed(sample, {})
check("title <= 256", len(embed["title"]) <= 256, f"{len(embed['title'])}")
check("fields <= 25", len(embed["fields"]) <= 25, f"{len(embed['fields'])}")
check("every field name <= 256", all(len(f["name"]) <= 256 for f in embed["fields"]))
check("every field value <= 1024",
      all(len(f["value"]) <= 1024 for f in embed["fields"]),
      f"max={max(len(f['value']) for f in embed['fields'])}")
check("footer <= 2048", len(embed["footer"]["text"]) <= 2048)
total = (len(embed["title"]) + len(embed["footer"]["text"])
         + sum(len(f["name"]) + len(f["value"]) for f in embed["fields"]))
check("embed total <= 6000", total <= 6000, f"{total}")
check("url preserved", embed["url"] == sample.url)
check("serializes as JSON", isinstance(json.dumps({"embeds": [embed]}), str))

no_date = _embed(Job(source="s", company="X", title="RTL Intern", url="u"), {})
check("missing timestamp handled", any(f["value"] == "Unknown" for f in no_date["fields"]))

# ---------------------------------------------------------------- dashboard
print("\nDashboard")

index = Path("docs/index.html")
jobs_json = Path("docs/jobs.json")

if not index.exists():
    check("docs/index.html exists", False, "run the radar first")
else:
    html_text = index.read_text(encoding="utf-8")
    check("docs/index.html exists", True, f"{len(html_text):,} bytes")
    check("no unreplaced placeholders", not re.search(r"__[A-Z]+__", html_text))

    blob = re.search(r'<script id="data" type="application/json">(.*?)</script>',
                     html_text, re.S)
    check("embedded data block present", blob is not None)
    if blob:
        raw = blob.group(1).replace("<\\/", "</")
        try:
            rows = json.loads(raw)
            check("embedded JSON parses", True, f"{len(rows)} roles")
            check("every role has an apply url", all(r.get("url") for r in rows))
            check("every role has a uid", all(r.get("uid") for r in rows))
        except json.JSONDecodeError as e:
            check("embedded JSON parses", False, str(e))

    check("no raw </script> can break the tag",
          html_text.count("</script>") == 2, f"{html_text.count('</script>')}")

if jobs_json.exists():
    data = json.loads(jobs_json.read_text(encoding="utf-8"))
    check("jobs.json valid", data["count"] == len(data["jobs"]), f"{data['count']} roles")
else:
    check("jobs.json exists", False)

print()
if failures:
    print(f"{len(failures)} FAILED: {failures}")
    sys.exit(1)
print("All checks passed.")
