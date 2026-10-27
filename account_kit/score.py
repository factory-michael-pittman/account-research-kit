"""Buying-signal scoring: timing points plus study-sheet points, equally
weighted, multiplied by how many distinct signals stack on one account.

Ported line-for-line from the account-value repo's prioritize/scoring.py so
scores match the source system; the code catalog and weights come from
config/signals.yaml instead of constants in this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from account_kit.catalog import Catalog


@dataclass
class Score:
    timing_points: float
    study_points: float
    distinct_signals: int
    multiplier: float
    final: float
    timing_codes: list[str]
    study_codes: list[str]
    next_event: dict | None
    last_event: dict | None


def _parse_date(value) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    for fmt_len in (10, 7, 4):
        piece = text[:fmt_len]
        try:
            parts = [int(p) for p in piece.split("-")]
        except ValueError:
            continue
        if len(parts) == 3:
            return date(*parts)
        if len(parts) == 2:
            return date(parts[0], parts[1], 1)
        if len(parts) == 1 and fmt_len == 4:
            return date(parts[0], 1, 1)
    return None


def in_window(catalog: Catalog, signal: dict, today: date) -> bool:
    code = signal.get("code")
    if code not in catalog.timing:
        return True
    earliest, latest = catalog.timing[code].window
    when = _parse_date(signal.get("date"))
    if when is None:
        return False
    days = (when - today).days
    return (earliest is None or days >= earliest) and (latest is None or days <= latest)


def posting_level_ai(catalog: Catalog, signal: dict) -> bool:
    """An AI mention inside a job posting is too common to signal a mandate;
    the study code needs company-level evidence (an executive statement, a
    rollout)."""
    return signal.get("code") == catalog.ai_code and signal.get("source") == catalog.ai_source


def counts(catalog: Catalog, signal: dict, today: date) -> bool:
    if signal.get("label") == "unknown":
        return False
    if signal.get("code") not in catalog.timing and signal.get("code") not in catalog.study:
        return False
    return in_window(catalog, signal, today) and not posting_level_ai(catalog, signal)


def why_not_counted(catalog: Catalog, signal: dict, today: date) -> str:
    if signal.get("code") not in catalog.timing and signal.get("code") not in catalog.study:
        return "no: code not in the catalog"
    if signal.get("label") == "unknown":
        return "no: unverified"
    if posting_level_ai(catalog, signal):
        return "no: AI mention in a job posting"
    if not in_window(catalog, signal, today):
        return "no: outside window"
    return "yes"


def score_signals(catalog: Catalog, signals: list[dict], today: date | None = None) -> Score:
    """signals: dicts with at least `code`; optional `date`, `claim`. Unknown
    codes, signals labelled `unknown`, and timing signals outside their
    window earn nothing."""
    today = today or date.today()
    usable = [s for s in signals if counts(catalog, s, today)]
    timing = sorted({s["code"] for s in usable if s.get("code") in catalog.timing})
    study = sorted({s["code"] for s in usable if s.get("code") in catalog.study})
    timing_points = min(catalog.bucket_max, len(timing) * catalog.timing_points)
    study_points = min(catalog.bucket_max, len(study) * catalog.study_points)
    distinct = len(timing) + len(study)
    multiplier = 1 + catalog.stack_step * max(distinct - 1, 0)
    final = round((timing_points + study_points) * multiplier, 1)

    upcoming, past = [], []
    for s in usable:
        if s.get("code") not in catalog.timing:
            continue
        when = _parse_date(s.get("date"))
        if when is None:
            continue
        entry = {"code": s["code"], "date": when.isoformat(), "claim": s.get("claim", ""),
                 "url": s.get("url", ""), "days": (when - today).days}
        (upcoming if when >= today else past).append(entry)
    next_event = min(upcoming, key=lambda e: e["date"]) if upcoming else None
    last_event = max(past, key=lambda e: e["date"]) if past else None
    return Score(round(timing_points, 2), round(study_points, 2), distinct, round(multiplier, 2), final,
                 timing, study, next_event, last_event)


def precedent_for(catalog: Catalog, study_codes: list[str]) -> str:
    letters = {catalog.study[c].precedent for c in study_codes if c in catalog.study}
    if "A" in letters:
        return "A"
    if "B" in letters:
        return "B"
    return "none"


def suggested_tier(catalog: Catalog, final: float, rank: int) -> str:
    tiers = catalog.tiers
    if rank <= tiers.tier1_max_rank and final >= tiers.tier1_min_score:
        return "Tier 1"
    if final >= tiers.tier2_min_score:
        return "Tier 2"
    return "Tier 3"
