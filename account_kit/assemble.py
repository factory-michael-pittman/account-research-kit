"""The score step: merge job-board signals with researched signals, score
every account, rank the list, and write the outputs.

Ported from the account-value repo's prioritize/commands.py (score and
select_list), minus the owner-territory and addition machinery that belongs
to one seller's book. Outputs:
  data/prioritized.csv  - the ranked account list
  data/evidence.csv     - every signal, counted or not, with the reason
  data/digests/*.md     - one defensible per-account research digest
  data/coverage.md      - how strong the research is across the whole list
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

from account_kit import score as scoring
from account_kit.catalog import Catalog, Settings


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_jsonl(paths) -> list[dict]:
    out = []
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_research(data: Path) -> tuple[dict[str, dict], dict[str, dict]]:
    """Research records by Id, with an exact-name recovery for mistyped Ids
    (a real failure mode the source system learned to handle)."""
    rows = _read_jsonl(sorted((data / "research").glob("out-*.jsonl")))
    by_id = {r["Id"]: r for r in rows if r.get("Id")}
    by_name = {(r.get("Name") or "").strip().lower(): r for r in rows if r.get("Name")}
    return by_id, by_name


def account_view(catalog: Catalog, settings: Settings, data: Path, today: date) -> list[dict]:
    """Everything known about each shortlisted account, scored."""
    rows = _read_csv(data / "shortlist.csv")
    research_by_id, research_by_name = load_research(data)
    out = []
    for record in rows:
        job_file = data / "jobs" / f"{record['Id']}.json"
        job_data = json.loads(job_file.read_text(encoding="utf-8")) if job_file.exists() else {}
        found = research_by_id.get(record["Id"]) \
            or research_by_name.get(record["Name"].strip().lower(), {})
        if found and found.get("Id") != record["Id"]:
            found = {**found, "Id": record["Id"]}
        signals = list(job_data.get("signals", [])) + list(found.get("signals", []))
        result = scoring.score_signals(catalog, signals, today=today)
        ranked_signals = sorted(
            [s for s in signals if scoring.counts(catalog, s, today)],
            key=lambda s: (0 if s.get("code") in catalog.timing else 1, str(s.get("date", ""))),
        )
        top3 = [f"{s.get('claim', '')[:160]} <{s.get('url', '')}>" for s in ranked_signals[:3]]
        why_now = sorted(found.get("why_now") or [], key=lambda w: w.get("rank", 99))
        out.append({
            **record,
            "final_score": result.final, "timing_points": result.timing_points,
            "study_points": result.study_points, "stack_count": result.distinct_signals,
            "multiplier": result.multiplier,
            "timing_signals": ", ".join(result.timing_codes),
            "timing_codes_list": result.timing_codes,
            "study_entries": ", ".join(catalog.study[c].meaning for c in result.study_codes),
            "study_codes_list": result.study_codes,
            "precedent": scoring.precedent_for(catalog, result.study_codes),
            "next_event": result.next_event["claim"] if result.next_event else "",
            "next_event_date": result.next_event["date"] if result.next_event else "",
            "days_until_next": result.next_event["days"] if result.next_event else "",
            "last_event": result.last_event["claim"] if result.last_event else "",
            "last_event_date": result.last_event["date"] if result.last_event else "",
            "days_since_last": -result.last_event["days"] if result.last_event else "",
            "signal_1": top3[0] if top3 else ("No counted signals found (researched)" if found
                                              else "Not researched"),
            "signal_2": top3[1] if len(top3) > 1 else "",
            "signal_3": top3[2] if len(top3) > 2 else "",
            "partner": found.get("partner", ""),
            "research_flags": "; ".join(found.get("flags") or []),
            "research_status": "researched" if found else "not researched",
            "company_summary": found.get("summary", ""),
            "why_now": why_now,
            "not_found": found.get("not_found", []),
            "board": (f"{job_data.get('ats', '')} "
                      f"{job_data.get('n_eng_jobs', 0)}/{job_data.get('n_jobs', 0)} eng jobs"
                      if job_data.get("ats") else "no public board found"),
            "all_signals": signals,
        })
    return out


def run(catalog: Catalog, settings: Settings, data: Path, today: date) -> dict:
    accounts = account_view(catalog, settings, data, today)
    accounts.sort(key=lambda r: (-r["final_score"], -r["stack_count"],
                                 -int(r.get("fit_score") or 0), r["Name"].lower()))
    evidence = []
    for rank, row in enumerate(accounts, 1):
        row["rank"] = rank
        row["tier"] = scoring.suggested_tier(catalog, row["final_score"], rank)
        for s in row["all_signals"]:
            code = s.get("code", "")
            spec = catalog.timing.get(code) or catalog.study.get(code)
            evidence.append({
                "Id": row["Id"], "Name": row["Name"], "code": code,
                "meaning": spec.meaning if spec else "",
                "claim": s.get("claim", ""), "label": s.get("label", ""),
                "date": s.get("date", ""), "source": s.get("source", ""),
                "url": s.get("url", ""),
                "counted": scoring.why_not_counted(catalog, s, today).startswith("yes"),
                "why": scoring.why_not_counted(catalog, s, today),
                "confirmation": s.get("confirmation", ""),
            })

    columns = ["rank", "Id", "Name", "Website", "state", "industry", "eng_headcount", "fit_score",
               "tier", "final_score", "timing_points", "study_points", "stack_count", "multiplier",
               "timing_signals", "study_entries", "precedent", "next_event", "next_event_date",
               "days_until_next", "last_event", "last_event_date", "days_since_last",
               "signal_1", "signal_2", "signal_3", "company_summary", "partner", "board",
               "research_flags", "research_status"]
    list_rows = [{k: row.get(k, "") for k in columns} for row in accounts]
    _write_csv(data / "prioritized.csv", list_rows)
    _write_csv(data / "evidence.csv", evidence)

    from account_kit import digest
    digest.write_all(catalog, data, accounts, evidence, today)
    return {"accounts": len(accounts), "researched": sum(1 for a in accounts
                                                          if a["research_status"] == "researched"),
            "tier1": sum(1 for a in accounts if a["tier"] == "Tier 1"),
            "output": str(data / "prioritized.csv")}
