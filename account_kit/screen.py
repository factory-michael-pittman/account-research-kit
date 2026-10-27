"""The filter step: firmographic screens and the fit pre-score turn a large
pool into the shortlist worth researching.

Writes:
  data/shortlist.csv            - the accounts to scan and research
  data/cut.csv                  - every excluded account, with the reason
  data/unresolved.csv           - accounts a web lookup could rescue
  data/enrichment-candidates.csv - the lookup worklist for prompts/enrichment.md
  data/screen-summary.json      - counts for the run
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

from account_kit import fit
from account_kit.catalog import Settings


def _read_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def load_enrichment(data: Path) -> dict[str, dict]:
    """Optional out-*.jsonl lookups under data/enrichment (prompts/enrichment.md)."""
    out: dict[str, dict] = {}
    for path in sorted((data / "enrichment").glob("out-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("confidence") in (None, "", "none"):
                continue
            # A registered-agent address (e.g. Sheridan WY, Delaware) is a
            # legal mailbox, not a headquarters; keep size data, drop location.
            if "registered" in (row.get("note") or "").lower() and "agent" in (row.get("note") or "").lower():
                row = {**row, "country": row.get("country", ""), "state": ""}
            out[row["Id"]] = row
    return out


def _row(a: fit.Assessment, rank: int | None = None) -> dict:
    r = a.record
    return {
        "rank": rank or "", "Id": r["Id"], "Name": r["Name"], "Website": r.get("Website", ""),
        "Type": r.get("Type", ""), "country": a.country, "state": a.region,
        "location_source": a.location_source,
        "eng_headcount": "" if a.eng is None else int(a.eng),
        "eng_estimated": a.eng_estimated, "employees": r.get("ZI_Employee_Count__c", ""),
        "industry": r.get("ZI_Primary_Industry__c", ""), "sub_industry": r.get("ZI_Primary_Sub_Industry__c", ""),
        "growth_1y": r.get("ZI_Past_1_Year_Employee_Growth_Rate__c", ""),
        "fit_score": a.fit_score, "fit_parts": "; ".join(a.fit_parts),
        "status": a.status, "reason": a.reason, "last_activity": r.get("LastActivityDate", ""),
        "tier": r.get("Account_Tier__c", ""), "parent_flag": fit.parent_flag(r),
    }


def run(settings: Settings, data: Path, today=None) -> dict:
    from datetime import date as _date
    today = today or _date.today()
    pool = data / "pool.csv"
    if not pool.exists():
        raise FileNotFoundError("data/pool.csv is missing; run intake first "
                                "(kit.py intake, or kit.py intake --from-csv FILE)")
    records = _read_csv(pool)
    if not records:
        raise ValueError("data/pool.csv has no rows")
    shares = fit.engineering_share_by_industry(records)
    enrichment = load_enrichment(data)
    assessments = [fit.assess(settings, r, shares, enrichment.get(r["Id"])) for r in records]
    ranked = fit.rank(assessments)
    short = [_row(a, i) for i, a in enumerate(ranked[:settings.shortlist], 1)]
    _write_csv(data / "shortlist.csv", short)
    _write_csv(data / "cut.csv", [_row(a) for a in assessments if a.status == "excluded"])
    _write_csv(data / "unresolved.csv", [_row(a) for a in assessments if a.status == "unresolved"])
    _write_csv(data / "expansion.csv", [_row(a) for a in assessments if a.status == "expansion"])
    candidates = fit.enrichment_candidates(settings, records, shares)
    _write_csv(data / "enrichment-candidates.csv", candidates)
    if candidates:
        # Render the enrichment prompt so the worklist is self-contained.
        template = Path(__file__).resolve().parent.parent / "prompts" / "enrichment.md"
        if template.exists():
            prompt = template.read_text(encoding="utf-8").replace("{{TODAY}}", today.isoformat())
            (data / "enrichment").mkdir(parents=True, exist_ok=True)
            (data / "enrichment" / "INSTRUCTIONS.md").write_text(prompt, encoding="utf-8")
    summary = {
        "pool": len(records), "passing": len(ranked), "shortlist": len(short),
        "excluded": sum(a.status == "excluded" for a in assessments),
        "unresolved": sum(a.status == "unresolved" for a in assessments),
        "expansion": sum(a.status == "expansion" for a in assessments),
        "enriched": len(enrichment),
        "cutoff_fit_score": short[-1]["fit_score"] if short else None,
        "industry_eng_share": {k: round(v, 3) for k, v in shares.items()},
    }
    (data / "screen-summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return summary
