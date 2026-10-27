"""Turn job postings into scored signals without an LLM.

Ported from the account-value repo's prioritize/jobs.py; the signal patterns
now come from config/signals.yaml instead of this file. The job scan is the
cheap first pass across a whole shortlist; the research prompt (batches step)
deepens and corrects it.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from account_kit.boards import BoardResult, Job, collect, domain_of, find_board


def _published(value) -> str:
    """ISO date for a posting's publish time. Lever reports epoch
    milliseconds, which `date.fromisoformat` would misread as a year."""
    text = str(value or "").strip()
    if re.fullmatch(r"\d{10,13}", text):
        seconds = int(text) / (1000 if len(text) > 10 else 1)
        return datetime.fromtimestamp(seconds, tz=timezone.utc).date().isoformat()
    return text


def _iso(value) -> date | None:
    text = _published(value)
    if not re.match(r"\d{4}-\d{2}-\d{2}", text):
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def signals_from_postings(catalog, jobs: list[Job], bullets, today: date | None = None) -> list[dict]:
    today = today or date.today()
    text_by_job: dict[str, list[str]] = {}
    for bullet in bullets:
        text_by_job.setdefault(bullet.job_id, []).append(bullet.verbatim)
    signals: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for job in jobs:
        if not catalog.eng_title_pattern.search(job.title or ""):
            continue
        body = " ".join([job.title or ""] + text_by_job.get(job.job_id, []))
        for code, spec in catalog.study.items():
            if not spec.scanned:
                continue
            for pattern in spec.patterns:
                match = re.search(pattern, body)
                if not match or (code, spec.meaning) in seen:
                    continue
                seen.add((code, spec.meaning))
                start = max(match.start() - 60, 0)
                signals.append({
                    "code": code, "source": "job posting", "label": "observed",
                    "claim": f"{spec.meaning}: \"...{body[start:match.end() + 60].strip()}...\" ({job.title})",
                    "date": _published(job.first_published) or today.isoformat(),
                    "url": job.url,
                })
        published = _iso(job.first_published)
        if published and (today - published) > timedelta(days=catalog.stale_days) \
                and ("T_STALE_REQ", "") not in seen:
            seen.add(("T_STALE_REQ", ""))
            signals.append({
                "code": "T_STALE_REQ", "source": "job posting", "label": "observed",
                "claim": f"{job.title} open since {published.isoformat()} "
                         f"({(today - published).days} days)",
                "date": published.isoformat(), "url": job.url,
            })
    return signals


def scan_account(catalog, record: dict, raw_dir: Path, _fetch=None) -> dict:
    """Find the board, read it, and extract signals for one account."""
    domain = domain_of(record.get("Website", ""))
    result = {"Id": record["Id"], "Name": record["Name"], "domain": domain, "ats": "", "board": "",
              "how_found": "", "n_jobs": 0, "n_eng_jobs": 0, "signals": [], "notes": []}
    if not domain:
        result["notes"].append("no website")
        return result
    ats, board, how = find_board(domain, record["Name"], _fetch)
    result.update(ats=ats, board=board, how_found=how)
    if not ats:
        return result
    try:
        collected: BoardResult = collect(ats, board, raw_dir / record["Id"], _fetch)
    except Exception as error:  # noqa: BLE001 - one account's board failing must not stop the batch
        result["notes"].append(f"collector failed: {error}")
        return result
    result["n_jobs"] = len(collected.jobs)
    result["n_eng_jobs"] = sum(1 for j in collected.jobs if catalog.eng_title_pattern.search(j.title or ""))
    result["signals"] = signals_from_postings(catalog, collected.jobs, collected.bullets)
    result["snapshot"] = collected.snapshot
    return result


def scan_all(catalog, settings, data: Path, limit: int | None = None) -> dict:
    """Scan every shortlisted account's board, one JSON per account under data/jobs."""
    import csv
    import json
    from concurrent.futures import ThreadPoolExecutor

    shortlist = data / "shortlist.csv"
    if not shortlist.exists():
        raise FileNotFoundError("data/shortlist.csv is missing; run the filter step first")
    with shortlist.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))[:limit or None]
    raw_dir = data / "raw"
    jobs_dir = data / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    def work(record: dict) -> dict:
        try:
            return scan_account(catalog, record, raw_dir)
        except Exception as error:  # noqa: BLE001 - one account must not stop the batch
            return {"Id": record["Id"], "Name": record["Name"], "domain": domain_of(record.get("Website", "")),
                    "ats": "", "board": "", "how_found": "", "n_jobs": 0, "n_eng_jobs": 0,
                    "signals": [], "notes": [f"scan failed: {error}"]}

    with ThreadPoolExecutor(max_workers=settings.job_workers) as pool:
        results = list(pool.map(work, rows))
    for result in results:
        (jobs_dir / f"{result['Id']}.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return {
        "accounts": len(results),
        "boards_found": sum(1 for r in results if r["ats"]),
        "signals": sum(len(r["signals"]) for r in results),
        "no_board": [r["Name"] for r in results if not r["ats"]],
        "failed": [f"{r['Name']}: {'; '.join(r['notes'])}" for r in results if r["notes"]],
    }
