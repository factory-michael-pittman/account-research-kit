"""Loads and validates config/signals.yaml and config/settings.yaml.

Every tuning knob the kit has lives in those two files. This module is the
only reader; `python3 kit.py check` prints what it loaded so a misread config
is visible before any research depends on it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from account_kit import miniyaml

SCHEMA_VERSION = 1


class ConfigError(ValueError):
    pass


def _fail(where: str, message: str) -> None:
    raise ConfigError(f"config/{where}: {message}")


@dataclass(frozen=True)
class TimingCode:
    code: str
    meaning: str
    window: tuple[int | None, int | None]
    definition: str
    guard: str = ""


@dataclass(frozen=True)
class StudyCode:
    code: str
    meaning: str
    definition: str
    precedent: str = ""  # "A" | "B" | ""
    guard: str = ""
    patterns: tuple = ()  # compiled with re.I; empty for research-only codes

    @property
    def scanned(self) -> bool:
        return bool(self.patterns)


@dataclass(frozen=True)
class Tiers:
    tier1_max_rank: int
    tier1_min_score: float
    tier2_min_score: float


@dataclass(frozen=True)
class Catalog:
    bucket_max: float
    stack_step: float
    tiers: Tiers
    labels: tuple[str, ...]
    ai_code: str
    ai_source: str
    timing: dict[str, TimingCode]
    study: dict[str, StudyCode]
    stale_days: int
    eng_title_pattern: re.Pattern
    partner_sis: tuple[str, ...]
    case_studies: tuple[str, ...]
    out_of_scope_note: str
    lookup_budget: int

    @property
    def timing_points(self) -> float:
        return self.bucket_max / len(self.timing)

    @property
    def study_points(self) -> float:
        return self.bucket_max / len(self.study)


@dataclass(frozen=True)
class Settings:
    org: str
    owners: tuple[str, ...]
    ownership_in: tuple[str | None, ...]
    shortlist: int
    final: int
    batch_size: int
    job_workers: int
    countries: frozenset[str]
    regions: frozenset[str]
    min_eng: int
    software_heavy_industries: frozenset[str]
    software_heavy_subs: frozenset[str]
    it_services_subs: frozenset[str]
    non_software_industries: frozenset[str]
    non_software_subs: frozenset[str]
    stack_pattern: re.Pattern
    growth_threshold: float
    own_domains: frozenset[str]
    competitor_domains: frozenset[str]
    competitor_names: re.Pattern
    junk_names: re.Pattern
    public_sector_industries: frozenset[str]
    public_sector_subs: frozenset[str]


def _window(value, where: str) -> tuple[int | None, int | None]:
    if not isinstance(value, list) or len(value) != 2:
        _fail(where, "window must be a two-item list like [-365, 0]; use null for unbounded")
    out = []
    for item in value:
        if item is None:
            out.append(None)
        elif isinstance(item, int) and not isinstance(item, bool):
            out.append(item)
        else:
            _fail(where, f"window bounds must be whole numbers or null; found {item!r}")
    return (out[0], out[1])


def _window_text(code: TimingCode) -> str:
    earliest, latest = code.window
    def side(bound, suffix_past: str, suffix_future: str, unbounded: str) -> str:
        if bound is None:
            return unbounded
        if bound < 0:
            return f"past {abs(bound)} days ({suffix_past})"
        return f"within {bound} days ahead ({suffix_future})"
    return f"{side(earliest, 'no older', 'no sooner', 'any date')} to {side(latest, 'no newer', 'no later', 'any date')}"


def window_text(catalog: Catalog, code: str) -> str:
    return _window_text(catalog.timing[code])


def _load_catalog(data: dict, lookup_budget: int = 6) -> Catalog:
    if data.get("schema_version") != SCHEMA_VERSION:
        _fail("signals.yaml", f"schema_version must be {SCHEMA_VERSION}")
    scoring = data.get("scoring")
    if not isinstance(scoring, dict):
        _fail("signals.yaml", "missing 'scoring:' section")
    bucket_max, stack_step = scoring.get("bucket_max"), scoring.get("stack_step")
    if not isinstance(bucket_max, (int, float)) or bucket_max <= 0:
        _fail("signals.yaml", "scoring.bucket_max must be a positive number")
    if not isinstance(stack_step, (int, float)) or stack_step < 0:
        _fail("signals.yaml", "scoring.stack_step must be zero or more")

    tiers = data.get("tiers")
    if not isinstance(tiers, dict) or "tier1" not in tiers or "tier2" not in tiers:
        _fail("signals.yaml", "missing tiers: tier1 and tier2")
    t1, t2 = tiers["tier1"], tiers["tier2"]
    for where, part, key in (("tier1", t1, "max_rank"), ("tier1", t1, "min_score"), ("tier2", t2, "min_score")):
        if not isinstance(part.get(key), (int, float)):
            _fail("signals.yaml", f"tiers.{where}.{key} must be a number")

    labels = data.get("labels")
    if not isinstance(labels, list) or "unknown" not in labels:
        _fail("signals.yaml", "labels must be a list that includes 'unknown'")

    ai = data.get("posting_level_ai")
    if not isinstance(ai, dict) or not ai.get("code") or not ai.get("source"):
        _fail("signals.yaml", "posting_level_ai needs a code and a source")

    timing_raw = data.get("timing")
    study_raw = data.get("study")
    if not isinstance(timing_raw, dict) or not timing_raw:
        _fail("signals.yaml", "missing non-empty 'timing:' section")
    if not isinstance(study_raw, dict) or not study_raw:
        _fail("signals.yaml", "missing non-empty 'study:' section")

    timing: dict[str, TimingCode] = {}
    for code, spec in timing_raw.items():
        where = f"signals.yaml timing.{code}"
        if not code.startswith("T_"):
            _fail(where, "timing codes are named T_*")
        if not isinstance(spec, dict) or not spec.get("meaning"):
            _fail(where, "needs a meaning")
        timing[code] = TimingCode(
            code=code, meaning=spec["meaning"], window=_window(spec.get("window"), where),
            definition=spec.get("definition", ""), guard=spec.get("guard", ""),
        )

    study: dict[str, StudyCode] = {}
    for code, spec in study_raw.items():
        where = f"signals.yaml study.{code}"
        if not code.startswith("S"):
            _fail(where, "study codes are named S*")
        if not isinstance(spec, dict) or not spec.get("meaning"):
            _fail(where, "needs a meaning")
        precedent = spec.get("precedent", "")
        if precedent not in ("", "A", "B"):
            _fail(where, f"precedent must be A, B, or omitted; found {precedent!r}")
        patterns = spec.get("patterns", [])
        if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
            _fail(where, "patterns must be a list of regex strings")
        for pattern in patterns:
            try:
                re.compile(pattern, re.I)
            except re.error as error:
                _fail(where, f"pattern does not compile: {pattern!r} ({error})")
        study[code] = StudyCode(
            code=code, meaning=spec["meaning"], definition=spec.get("definition", ""),
            precedent=precedent, guard=spec.get("guard", ""),
            # Posting text is mixed-case; patterns always scan case-insensitively.
            patterns=tuple(re.compile(p, re.I) for p in patterns),
        )

    scan = data.get("job_scan")
    if not isinstance(scan, dict):
        _fail("signals.yaml", "missing 'job_scan:' section")
    stale_days = scan.get("stale_days")
    if not isinstance(stale_days, int) or isinstance(stale_days, bool) or stale_days <= 0:
        _fail("signals.yaml", "job_scan.stale_days must be a positive whole number of days")
    eng_title = scan.get("engineering_title_pattern")
    if not isinstance(eng_title, str):
        _fail("signals.yaml", "job_scan.engineering_title_pattern must be a regex string")

    research = data.get("research")
    if not isinstance(research, dict):
        _fail("signals.yaml", "missing 'research:' section")
    partners = research.get("partner_sis", [])
    cases = research.get("case_studies", [])
    for where, value in (("research.partner_sis", partners), ("research.case_studies", cases)):
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            _fail("signals.yaml", f"{where} must be a list of strings")

    ai_code = ai["code"]
    if ai_code not in study:
        _fail("signals.yaml", f"posting_level_ai.code {ai_code!r} is not a study code")

    return Catalog(
        bucket_max=float(bucket_max), stack_step=float(stack_step),
        tiers=Tiers(int(t1["max_rank"]), float(t1["min_score"]), float(t2["min_score"])),
        labels=tuple(labels), ai_code=ai_code, ai_source=ai["source"],
        timing=timing, study=study, stale_days=int(stale_days),
        eng_title_pattern=re.compile(eng_title, re.I),
        partner_sis=tuple(partners), case_studies=tuple(cases),
        out_of_scope_note=research.get("out_of_scope_note", ""),
        lookup_budget=lookup_budget,
    )


def _strings(value, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
        _fail(where, "must be a list of strings")
    return tuple(value)


def _pattern(value, where: str, flags: int = 0) -> re.Pattern:
    if not isinstance(value, str):
        _fail(where, "must be a regex string")
    try:
        return re.compile(value, flags)
    except re.error as error:
        _fail(where, f"pattern does not compile: {value!r} ({error})")
    raise AssertionError  # unreachable; keeps type checkers content


def _load_settings(data: dict, catalog: Catalog) -> Settings:
    sfdc = data.get("sfdc")
    if not isinstance(sfdc, dict) or not sfdc.get("org"):
        _fail("settings.yaml", "sfdc.org must be your sf CLI org alias")
    owners = _strings(sfdc.get("owners", []), "settings.yaml sfdc.owners")
    ownership = sfdc.get("ownership_in", [])
    if not isinstance(ownership, list):
        _fail("settings.yaml", "sfdc.ownership_in must be a list (use null for 'no value set')")

    limits = data.get("limits")
    if not isinstance(limits, dict):
        _fail("settings.yaml", "missing 'limits:' section")
    for key in ("shortlist", "final", "batch_size", "job_workers"):
        if not isinstance(limits.get(key), int) or isinstance(limits.get(key), bool) or limits[key] < 1:
            _fail("settings.yaml", f"limits.{key} must be a positive whole number")
    if not isinstance(limits.get("lookup_budget", 6), int) or limits.get("lookup_budget", 6) < 1:
        _fail("settings.yaml", "limits.lookup_budget must be a positive whole number")

    filters = data.get("filters")
    if not isinstance(filters, dict):
        _fail("settings.yaml", "missing 'filters:' section")
    countries = _strings(filters.get("countries", []), "settings.yaml filters.countries")
    regions = _strings(filters.get("regions", []), "settings.yaml filters.regions")
    min_eng = filters.get("min_eng", 0)
    if not isinstance(min_eng, int) or isinstance(min_eng, bool) or min_eng < 0:
        _fail("settings.yaml", "filters.min_eng must be zero or a positive whole number")

    fit = data.get("fit")
    if not isinstance(fit, dict):
        _fail("settings.yaml", "missing 'fit:' section")
    growth = fit.get("growth_threshold", 10)
    if not isinstance(growth, (int, float)):
        _fail("settings.yaml", "fit.growth_threshold must be a number")

    exclusions = data.get("exclusions")
    if not isinstance(exclusions, dict):
        _fail("settings.yaml", "missing 'exclusions:' section")

    return Settings(
        org=sfdc["org"], owners=owners, ownership_in=tuple(ownership),
        shortlist=int(limits["shortlist"]), final=int(limits["final"]),
        batch_size=int(limits["batch_size"]), job_workers=int(limits["job_workers"]),
        countries=frozenset(countries), regions=frozenset(regions), min_eng=int(min_eng),
        software_heavy_industries=frozenset(_strings(fit.get("software_heavy_industries", []),
                                                     "settings.yaml fit.software_heavy_industries")),
        software_heavy_subs=frozenset(_strings(fit.get("software_heavy_subs", []),
                                               "settings.yaml fit.software_heavy_subs")),
        it_services_subs=frozenset(_strings(fit.get("it_services_subs", []),
                                            "settings.yaml fit.it_services_subs")),
        non_software_industries=frozenset(_strings(fit.get("non_software_industries", []),
                                                   "settings.yaml fit.non_software_industries")),
        non_software_subs=frozenset(_strings(fit.get("non_software_subs", []),
                                             "settings.yaml fit.non_software_subs")),
        stack_pattern=_pattern(fit.get("stack_pattern", ""), "settings.yaml fit.stack_pattern", re.I),
        growth_threshold=float(growth),
        own_domains=frozenset(x.lower() for x in _strings(exclusions.get("own_domains", []),
                                                         "settings.yaml exclusions.own_domains")),
        competitor_domains=frozenset(x.lower() for x in _strings(
            exclusions.get("competitor_domains", []), "settings.yaml exclusions.competitor_domains")),
        competitor_names=_pattern(exclusions.get("competitor_names_pattern", ""),
                                  "settings.yaml exclusions.competitor_names_pattern", re.I),
        junk_names=_pattern(exclusions.get("junk_names_pattern", ""),
                            "settings.yaml exclusions.junk_names_pattern", re.I),
        public_sector_industries=frozenset(_strings(
            exclusions.get("public_sector_industries", []),
            "settings.yaml exclusions.public_sector_industries")),
        public_sector_subs=frozenset(_strings(
            exclusions.get("public_sector_subs", []), "settings.yaml exclusions.public_sector_subs")),
    )


def load(root: Path | None = None) -> tuple[Catalog, Settings]:
    """Read both config files under <root>/config (default: the kit folder)."""
    base = (root or Path(__file__).resolve().parent.parent) / "config"
    try:
        catalog_data = miniyaml.load(base / "signals.yaml")
        settings_data = miniyaml.load(base / "settings.yaml")
    except miniyaml.YamlError as error:
        raise ConfigError(str(error)) from error
    limits = settings_data.get("limits") or {}
    budget = limits.get("lookup_budget", 6)
    if not isinstance(budget, int) or isinstance(budget, bool) or budget < 1:
        budget = 6
    catalog = _load_catalog(catalog_data, lookup_budget=budget)
    settings = _load_settings(settings_data, catalog)
    return catalog, settings
