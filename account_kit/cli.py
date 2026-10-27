"""kit.py entry points. Every step reads and writes files under data/ so each
can be rerun on its own, in any order that makes sense:

  check    - validate config, print what is configured
  intake   - pool.csv from Salesforce (by owner) or from a CSV
  filter   - shortlist.csv / cut.csv / unresolved.csv / enrichment worklist
  jobs     - posting-text signals for every shortlisted account
  batches  - research handoff: in-NN.jsonl plus the rendered prompt
  score    - prioritized.csv / evidence.csv / digests / coverage.md
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from account_kit import assemble, batches, catalog as catalog_mod, intake, jobs, screen
from account_kit.catalog import ConfigError

ROOT = Path(__file__).resolve().parent.parent


def _data(value: str | None) -> Path:
    return Path(value) if value else ROOT / "data"


def cmd_check(args) -> int:
    catalog, settings = catalog_mod.load(ROOT)
    print(f"config OK")
    print(f"  signals: {len(catalog.timing)} timing codes, {len(catalog.study)} study codes; "
          f"buckets {catalog.bucket_max:g}+{catalog.bucket_max:g}, stack step {catalog.stack_step}")
    print(f"  timing code = {catalog.timing_points:.2f} points; "
          f"study code = {catalog.study_points:.2f} points")
    print(f"  tiers: Tier 1 rank<={catalog.tiers.tier1_max_rank} and score>={catalog.tiers.tier1_min_score:g}; "
          f"Tier 2 score>={catalog.tiers.tier2_min_score:g}")
    print(f"  job scan: stale req after {catalog.stale_days} days; "
          f"{sum(1 for s in catalog.study.values() if s.scanned)} study codes scanned from postings")
    print(f"  research: lookup budget {catalog.lookup_budget}/account; partners "
          f"{', '.join(catalog.partner_sis) or '-'}; {len(catalog.case_studies)} case studies")
    print(f"  sfdc: org {settings.org}; owners {', '.join(settings.owners) or '(none set)'}")
    print(f"  limits: shortlist {settings.shortlist}, final {settings.final}, "
          f"batch {settings.batch_size}, workers {settings.job_workers}")
    region_note = f"{len(settings.regions)} allowed" if settings.regions else "any region"
    country_note = f"{len(settings.countries)} allowed" if settings.countries else "any country"
    print(f"  filters: {country_note} countries, {region_note} regions, min eng {settings.min_eng}")
    return 0


def cmd_intake(args) -> int:
    catalog, settings = catalog_mod.load(ROOT)
    out = _data(args.data) / "pool.csv"
    if args.from_csv:
        missing = intake.normalize_csv(Path(args.from_csv), out)
        print(f"pool.csv written from {args.from_csv}")
        if missing:
            print(f"  note: these optional fields are absent, so the fit step is weaker: {', '.join(missing)}")
        return 0
    owners = [o.strip() for o in args.owners.split(",") if o.strip()] if args.owners else None
    intake.export(settings, out, owners)
    print(f"pool.csv written from Salesforce org {settings.org} "
          f"(owners: {', '.join(owners or settings.owners)})")
    return 0


def cmd_filter(args) -> int:
    catalog, settings = catalog_mod.load(ROOT)
    summary = screen.run(settings, _data(args.data))
    print(json.dumps({k: v for k, v in summary.items() if k != "industry_eng_share"}, indent=2))
    print(f"shortlist.csv: {summary['shortlist']} accounts; "
          f"{summary['unresolved']} unresolved (see data/enrichment-candidates.csv)")
    return 0


def cmd_jobs(args) -> int:
    catalog, settings = catalog_mod.load(ROOT)
    summary = jobs.scan_all(catalog, settings, _data(args.data), limit=args.limit)
    print(f"scanned {summary['accounts']} accounts; boards found for {summary['boards_found']}; "
          f"{summary['signals']} posting signals")
    if summary["no_board"]:
        print(f"  no public board: {', '.join(summary['no_board'][:10])}"
              + (" ..." if len(summary["no_board"]) > 10 else ""))
    for failure in summary["failed"][:10]:
        print(f"  {failure}")
    return 0


def cmd_batches(args) -> int:
    catalog, settings = catalog_mod.load(ROOT)
    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    result = batches.build(settings, catalog, _data(args.data), today)
    print(f"{result['accounts']} accounts in {len(result['batches'])} batch files")
    print(f"research handoff written: {result['instructions']}")
    print("next: point your research agent at data/research/INSTRUCTIONS.md "
          "and have it write out-NN.jsonl files there")
    return 0


def cmd_score(args) -> int:
    catalog, settings = catalog_mod.load(ROOT)
    today = date.fromisoformat(args.as_of) if args.as_of else date.today()
    summary = assemble.run(catalog, settings, _data(args.data), today)
    print(f"prioritized.csv: {summary['accounts']} accounts "
          f"({summary['researched']} researched, {summary['tier1']} Tier 1)")
    print(f"evidence.csv, digests/, and coverage.md written under {_data(args.data)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kit", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="validate config and print what is configured")

    p_intake = sub.add_parser("intake", help="build data/pool.csv")
    p_intake.add_argument("--owners", help="comma-separated owner names (overrides config)")
    p_intake.add_argument("--from-csv", help="read a CSV pool instead of Salesforce")
    p_intake.add_argument("--data", help="data directory (default: <kit>/data)")

    p_filter = sub.add_parser("filter", help="firmographic screens + fit pre-score -> shortlist.csv")
    p_filter.add_argument("--data", help="data directory (default: <kit>/data)")

    p_jobs = sub.add_parser("jobs", help="posting-text signals for the shortlist")
    p_jobs.add_argument("--limit", type=int, help="scan only the first N accounts")
    p_jobs.add_argument("--data", help="data directory (default: <kit>/data)")

    p_batches = sub.add_parser("batches", help="research handoff: in-NN.jsonl + prompt")
    p_batches.add_argument("--as-of", help="today's date as YYYY-MM-DD (default: today)")
    p_batches.add_argument("--data", help="data directory (default: <kit>/data)")

    p_score = sub.add_parser("score", help="score, rank, and write every output")
    p_score.add_argument("--as-of", help="today's date as YYYY-MM-DD (default: today)")
    p_score.add_argument("--data", help="data directory (default: <kit>/data)")

    args = parser.parse_args(argv)
    handlers = {"check": cmd_check, "intake": cmd_intake, "filter": cmd_filter,
                "jobs": cmd_jobs, "batches": cmd_batches, "score": cmd_score}
    try:
        return handlers[args.command](args)
    except (ConfigError, FileNotFoundError, ValueError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
