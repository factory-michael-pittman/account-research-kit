"""Firmographic filters and the fit pre-score that narrow a large pool to the
accounts worth researching.

Ported from the account-value repo's prioritize/fit.py, with the industry
lists, territory, and thresholds read from config/settings.yaml instead of
hardcoded.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable

from account_kit import geo
from account_kit.catalog import Settings


def _num(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def engineering_share_by_industry(records: Iterable[dict]) -> dict[str, float]:
    """Median engineering/employee ratio per industry, measured on accounts
    that carry both numbers."""
    ratios: dict[str, list[float]] = defaultdict(list)
    for r in records:
        eng, emp = _num(r.get("ZI_Engineering_Headcount__c")), _num(r.get("ZI_Employee_Count__c"))
        if eng is not None and emp and emp > 0:
            ratios[r.get("ZI_Primary_Industry__c") or ""].append(min(eng / emp, 1.0))
    every = [x for xs in ratios.values() for x in xs]
    shares = {k: statistics.median(v) for k, v in ratios.items() if len(v) >= 20}
    shares["__default__"] = statistics.median(every) if every else 0.05
    return shares


def engineering_headcount(record: dict, shares: dict[str, float],
                           enrichment: dict | None = None) -> tuple[float | None, bool]:
    """(headcount, estimated). Measured value first, then a web-found figure,
    then employees x the industry's median engineering share."""
    eng = _num(record.get("ZI_Engineering_Headcount__c"))
    if eng is not None:
        return eng, False
    if enrichment and _num(enrichment.get("eng_headcount")) is not None:
        return _num(enrichment["eng_headcount"]), True
    emp = _num(record.get("ZI_Employee_Count__c")) or _num(record.get("NumberOfEmployees"))
    if enrichment and emp is None:
        emp = _num(enrichment.get("employees"))
    if emp is None:
        return None, True
    share = shares.get(record.get("ZI_Primary_Industry__c") or "", shares["__default__"])
    return round(emp * share), True


@dataclass
class Assessment:
    record: dict
    status: str  # pass | expansion | excluded | unresolved
    reason: str = ""
    country: str = ""
    region: str = ""
    location_source: str = ""
    eng: float | None = None
    eng_estimated: bool = False
    fit_score: int = 0
    fit_parts: list[str] = field(default_factory=list)


def fit_score(settings: Settings, record: dict, eng: float, eng_estimated: bool) -> tuple[int, list[str]]:
    """The pre-score that orders the pool before research. It never enters the
    signal score; it only decides who is worth researching first."""
    score, parts = 0, []
    band = 1 if eng < 100 else 2 if eng < 200 else 3
    score += band
    parts.append(f"eng band +{band}")
    industry = record.get("ZI_Primary_Industry__c") or ""
    sub = record.get("ZI_Primary_Sub_Industry__c") or ""
    if industry in settings.software_heavy_industries or sub in settings.software_heavy_subs:
        score += 2
        parts.append("software-heavy +2")
    elif sub in settings.it_services_subs:
        score -= 2
        parts.append("IT services -2")
    elif industry in settings.non_software_industries or sub in settings.non_software_subs:
        score -= 2
        parts.append("non-software engineering -2")
    growth = _num(record.get("ZI_Past_1_Year_Employee_Growth_Rate__c"))
    if growth is not None and growth > settings.growth_threshold:
        score += 1
        parts.append(f"growth >{settings.growth_threshold:g}% +1")
    if settings.stack_pattern.search(record.get("ZI_Product_Technology_Products_Used__c") or ""):
        score += 1
        parts.append("stack match +1")
    if eng_estimated:
        score -= 1
        parts.append("estimated headcount -1")
    return score, parts


def parent_flag(record: dict) -> str:
    """Name the ultimate parent when it is a different company: a unit of a
    large enterprise buys through the parent, not as a mid-market account."""
    parent = (record.get("ZI_Ultimate_Parent_Company_Name__c") or "").strip()
    if not parent:
        return ""
    own = set(re.findall(r"[a-z0-9]+", record.get("Name", "").lower())) - {"inc", "llc", "corp", "the", "co"}
    theirs = set(re.findall(r"[a-z0-9]+", parent.lower())) - {"inc", "llc", "corp", "the", "co"}
    return "" if own & theirs else f"subsidiary of {parent}"


def assess(settings: Settings, record: dict, shares: dict[str, float],
           enrichment: dict | None = None) -> Assessment:
    country, region, source = geo.resolve_location(record, enrichment)
    eng, estimated = engineering_headcount(record, shares, enrichment)
    a = Assessment(record=record, status="pass", country=country, region=region,
                   location_source=source, eng=eng, eng_estimated=estimated)
    if record.get("Type") == "Customer" or record.get("Org_ID__c"):
        a.status, a.reason = "expansion", "existing customer or known org"
        return a
    domain = (record.get("Website") or "").lower().replace("https://", "").replace("http://", "")
    domain = domain.removeprefix("www.").split("/")[0]
    name = (record.get("Name") or "").strip()
    if domain in settings.own_domains:
        a.status, a.reason = "excluded", "own company's account"
        return a
    if domain in settings.competitor_domains or settings.competitor_names.match(name):
        a.status, a.reason = "excluded", "competitor"
        return a
    if settings.junk_names.fullmatch(name):
        a.status, a.reason = "excluded", "junk record"
        return a
    if (record.get("ZI_Primary_Industry__c") in settings.public_sector_industries
            or record.get("ZI_Primary_Sub_Industry__c") in settings.public_sector_subs):
        a.status, a.reason = "excluded", "government, university, or school"
        return a
    if parent_flag(record):
        a.status, a.reason = "excluded", parent_flag(record)
        return a
    if record.get("Account_Tier__c") == "Disqualified":
        a.status, a.reason = "excluded", "tier disqualified"
        return a
    reason = geo.country_outside(settings.countries, country)
    if reason:
        a.status, a.reason = "excluded", reason
        return a
    if eng is not None and eng < settings.min_eng:
        a.status, a.reason = "excluded", f"engineering headcount {eng:.0f} < {settings.min_eng}"
        return a
    if not region:
        a.status, a.reason = "unresolved", "no state/province after enrichment"
        return a
    reason = geo.region_outside(settings.regions, country, region)
    if reason:
        a.status, a.reason = "excluded", reason
        return a
    if eng is None:
        a.status, a.reason = "unresolved", "no engineering or employee headcount"
        return a
    a.fit_score, a.fit_parts = fit_score(settings, record, eng, estimated)
    return a


def enrichment_candidates(settings: Settings, records: list[dict],
                          shares: dict[str, float]) -> list[dict]:
    """Accounts that could pass every filter but are missing the location or
    size data to decide; these get a web lookup (prompts/enrichment.md)."""
    out = []
    for r in records:
        a = assess(settings, r, shares)
        if a.status == "unresolved":
            out.append({"Id": r["Id"], "Name": r["Name"], "Website": r.get("Website", ""),
                        "missing": "location" if not a.region else "headcount",
                        "country_hint": a.country})
    return out


def rank(assessments: list[Assessment]) -> list[Assessment]:
    passing = [a for a in assessments if a.status == "pass"]
    return sorted(passing, key=lambda a: (-a.fit_score, -(a.eng or 0), a.record["Name"].lower()))
