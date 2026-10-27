"""Research digests: one file per account, defensible line by line.

The point of a digest is that someone with no background in the account's
industry can read it, retell the reasons to reach out, and check any claim at
its source. Every line traces to a signal that carries a label, a date, and a
URL; anything that was found but did not count says why.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import date
from pathlib import Path

from account_kit import score as scoring


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "account"


def _cell(text) -> str:
    return str(text or "").replace("|", "/").replace("\n", " ").strip()


def _why_now_lines(row: dict, catalog) -> list[str]:
    lines = []
    for entry in row.get("why_now") or []:
        ids = ", ".join(f"`{i}`" for i in entry.get("signal_ids") or [])
        reason = _cell(entry.get("reason", ""))
        lines.append(f"{entry.get('rank', '?')}. {reason}" + (f" (evidence: {ids})" if ids else ""))
    if not lines and row["research_status"] == "researched":
        return ["No ranked reason was strong enough; read the evidence below and decide yourself."]
    if not lines:
        return ["Not researched yet; this account ranks on job-posting signals alone."]
    return lines


def _signal_tables(row: dict, catalog) -> tuple[list[str], list[str]]:
    timing, study = [], []
    for s in row["all_signals"]:
        code = s.get("code", "")
        spec = catalog.timing.get(code) or catalog.study.get(code)
        if not spec:
            continue
        entry = (f"| `{code}` | {_cell(spec.meaning)} | {_cell(s.get('claim', ''))} | "
                 f"{_cell(s.get('date', ''))} | {_cell(s.get('label', ''))} | "
                 f"{_cell(s.get('url', ''))} |")
        (timing if code in catalog.timing else study).append(entry)
    return timing, study


def account_digest(catalog, row: dict, total: int, today: date) -> str:
    name = row["Name"]
    timing, study = _signal_tables(row, catalog)
    out = [
        f"# {name} - account research digest",
        "",
        f"{today.isoformat()} · rank {row['rank']} of {total} · {row['tier']} · "
        f"score {row['final_score']} · {row['research_status']}",
        "",
        _cell(row.get("company_summary")) or "(no summary yet; run the research step)",
        "",
        "## Why now (the reasons to reach out, ranked)",
        "",
    ]
    out += _why_now_lines(row, catalog)
    out += ["", "## What was found", ""]
    if timing:
        out += ["Timing signals (dated events):", "",
                "| Code | Signal | What was found | Date | Label | Source |",
                "|---|---|---|---|---|---|"] + timing + [""]
    else:
        out += ["Timing signals: none counted.", ""]
    if study:
        out += ["Study signals (the work itself):", "",
                "| Code | Signal | What was found | Date | Label | Source |",
                "|---|---|---|---|---|---|"] + study + [""]
    else:
        out += ["Study signals: none counted.", ""]

    out += [
        "## What the score is",
        "",
        f"- Timing: {len(row['timing_codes_list'])} "
        f"of {len(catalog.timing)} codes, {row['timing_points']} of {catalog.bucket_max:g} points",
        f"- Study: {len(row['study_codes_list'])} "
        f"of {len(catalog.study)} codes, {row['study_points']} of {catalog.bucket_max:g} points",
        f"- {row['stack_count']} distinct signals stacked, multiplier x{row['multiplier']}, "
        f"final score {row['final_score']} -> {row['tier']}",
    ]
    if row.get("next_event_date"):
        out.append(f"- Next dated event: {_cell(row['next_event'])} ({row['next_event_date']}, "
                   f"{row['days_until_next']} days)")
    if row.get("last_event_date"):
        out.append(f"- Last dated event: {_cell(row['last_event'])} ({row['last_event_date']}, "
                   f"{row['days_since_last']} days ago)")
    out.append(f"- Sales precedent from study signals: {row['precedent']}")

    rejected = []
    for s in row["all_signals"]:
        code = s.get("code", "")
        if code not in catalog.timing and code not in catalog.study:
            continue
        why = scoring.why_not_counted(catalog, s, today)
        if not why.startswith("yes"):
            rejected.append((s, why))
    if rejected:
        out += ["", "## Found but not counted (and why)", "",
                "| What was found | Why it did not count |", "|---|---|"]
        for s, why in rejected:
            out.append(f"| {_cell(s.get('claim', ''))} | {why[4:]} |")
    else:
        out += ["", "Found but not counted: everything found counted."]

    if row.get("not_found"):
        out += ["", "## Searched and not found", ""]
        out += [f"- {_cell(line)}" for line in row["not_found"]]
    out.append("")
    return "\n".join(out)


def write_all(catalog, data: Path, accounts: list[dict], evidence: list[dict], today: date) -> None:
    digests = data / "digests"
    digests.mkdir(parents=True, exist_ok=True)
    for old in digests.glob("*.md"):
        old.unlink()
    for row in accounts:
        path = digests / f"{slugify(row['Name'])}.md"
        path.write_text(account_digest(catalog, row, len(accounts), today), encoding="utf-8")
    (data / "coverage.md").write_text(coverage_report(catalog, accounts, evidence, today),
                                      encoding="utf-8")


def coverage_report(catalog, accounts: list[dict], evidence: list[dict], today: date) -> str:
    total = len(accounts)
    researched = sum(1 for a in accounts if a["research_status"] == "researched")
    tiers = Counter(a["tier"] for a in accounts)
    code_stats: dict[str, dict] = {}
    for e in evidence:
        code = e["code"]
        stat = code_stats.setdefault(code, {"meaning": e["meaning"], "counted": 0, "total": 0})
        stat["total"] += 1
        if e["counted"]:
            stat["counted"] += 1
    why_counts = Counter(e["why"] for e in evidence if not e["counted"])

    lines = [
        f"# Research coverage - {today.isoformat()}",
        "",
        f"- {total} accounts ranked; {researched} researched; "
        f"{tiers.get('Tier 1', 0)} Tier 1 / {tiers.get('Tier 2', 0)} Tier 2 / {tiers.get('Tier 3', 0)} Tier 3",
        f"- {len(evidence)} signals recorded, {sum(1 for e in evidence if e['counted'])} counted",
        "",
        "## Signal strength across the book",
        "",
        "| Code | Signal | Counted | Found | Share counted |",
        "|---|---|---|---|---|",
    ]
    all_codes = sorted(set(catalog.timing) | set(catalog.study))
    for code in all_codes:
        spec = catalog.timing.get(code) or catalog.study.get(code)
        stat = code_stats.get(code)
        if not stat:
            lines.append(f"| `{code}` | {_cell(spec.meaning)} | 0 | 0 | - |")
            continue
        share = f"{stat['counted'] / stat['total']:.0%}" if stat["total"] else "-"
        lines.append(f"| `{code}` | {_cell(stat['meaning'])} | {stat['counted']} | {stat['total']} | {share} |")

    lines += ["", "## What did not count (book level)", ""]
    if why_counts:
        lines += ["| Reason | Signals |", "|---|---|"]
        for why, count in why_counts.most_common():
            lines.append(f"| {why[4:]} | {count} |")
    else:
        lines.append("Everything found counted.")

    zero = [a for a in accounts if a["stack_count"] == 0]
    lines += ["", "## Accounts to improve", ""]
    if zero:
        lines.append("No counted signals (weakest accounts; research these or drop them):")
        lines += [f"- {a['Name']} (rank {a['rank']}, {a['research_status']})" for a in zero]
    else:
        lines.append("Every account has at least one counted signal.")
    unverified = sorted(accounts, key=lambda a: -sum(1 for s in a["all_signals"] if s.get("label") == "unknown"))
    heavy = [(a, sum(1 for s in a["all_signals"] if s.get("label") == "unknown"))
             for a in unverified[:5]]
    if any(count for _, count in heavy):
        lines += ["", "Most unverified signals (re-check these first):"]
        lines += [f"- {a['Name']}: {count} unverified" for a, count in heavy if count]

    lines += ["", "## Strongest reasons to reach out, book-wide", ""]
    shown = 0
    for a in accounts:
        if shown >= 10:
            break
        for entry in a.get("why_now") or []:
            reason = _cell(entry.get("reason", ""))
            if reason:
                lines.append(f"- {a['Name']} (rank {a['rank']}): {reason}")
                shown += 1
                break
    lines.append("")
    return "\n".join(lines)
