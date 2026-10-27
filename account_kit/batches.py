"""Build the research handoff: batch input files plus a rendered prompt.

The researcher (any capable agent with web access) reads
data/research/INSTRUCTIONS.md and the in-NN.jsonl file named in its task, and
writes out-NN.jsonl. The prompt is prompts/research.md with the signal tables
injected from config/signals.yaml, so the catalog and the prompt never drift.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path


def _cell(text: str) -> str:
    return str(text or "").replace("|", "/").replace("\n", " ").strip()


def window_phrase(window: tuple[int | None, int | None]) -> str:
    earliest, latest = window
    if earliest is None and latest is None:
        return "any date"
    parts = []
    if earliest is not None:
        parts.append(f"no older than {abs(earliest)} days" if earliest < 0
                     else f"no sooner than {earliest} days ahead")
    if latest is not None:
        parts.append(f"at least {abs(latest)} days ago" if latest < 0
                     else f"no more than {latest} days ahead")
    return ", ".join(parts)


def timing_table(catalog) -> str:
    rows = ["| Code | Signal | What counts | Window (relative to today) | Common false positives |",
            "|---|---|---|---|---|"]
    for code in sorted(catalog.timing):
        spec = catalog.timing[code]
        rows.append(f"| `{code}` | {_cell(spec.meaning)} | {_cell(spec.definition)} | "
                    f"{window_phrase(spec.window)} | {_cell(spec.guard)} |")
    return "\n".join(rows)


def study_table(catalog) -> str:
    rows = ["| Code | Signal | What counts | Common false positives |", "|---|---|---|---|"]
    for code in sorted(catalog.study):
        spec = catalog.study[code]
        rows.append(f"| `{code}` | {_cell(spec.meaning)} | {_cell(spec.definition)} | {_cell(spec.guard)} |")
    return "\n".join(rows)


def render_prompt(template: str, catalog, today: date, batches: list[str]) -> str:
    out = template
    for token, value in {
        "{{TODAY}}": today.isoformat(),
        "{{TIMING_TABLE}}": timing_table(catalog),
        "{{STUDY_TABLE}}": study_table(catalog),
        "{{PARTNERS}}": ", ".join(catalog.partner_sis) or "(none configured)",
        "{{CASE_STUDIES}}": "; ".join(catalog.case_studies) or "(none configured)",
        "{{OUT_OF_SCOPE_NOTE}}": catalog.out_of_scope_note,
        "{{LOOKUP_BUDGET}}": str(catalog.lookup_budget),
        "{{BATCHES}}": ", ".join(f"`{name}`" for name in batches) or "(none)",
        "{{STALE_DAYS}}": str(catalog.stale_days),
    }.items():
        out = out.replace(token, value)
    return out


def build(settings, catalog, data: Path, today: date, _template: str | None = None) -> dict:
    """Write data/research/in-NN.jsonl plus the rendered INSTRUCTIONS.md."""
    shortlist = data / "shortlist.csv"
    if not shortlist.exists():
        raise FileNotFoundError("data/shortlist.csv is missing; run the filter step first")
    with shortlist.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    jobs_dir = data / "jobs"
    research_dir = data / "research"
    research_dir.mkdir(parents=True, exist_ok=True)

    # Stale batch inputs from an earlier shortlist would silently mix pools.
    for old in sorted(research_dir.glob("in-*.jsonl")):
        old.unlink()

    names = []
    for i in range(0, len(rows), settings.batch_size):
        lines = []
        for record in rows[i:i + settings.batch_size]:
            job_data = {}
            job_file = jobs_dir / f"{record['Id']}.json"
            if job_file.exists():
                job_data = json.loads(job_file.read_text(encoding="utf-8"))
            board = f"{job_data.get('ats', '')} {job_data.get('board', '')}".strip()
            lines.append(json.dumps({
                "Id": record["Id"], "Name": record["Name"], "Website": record.get("Website", ""),
                "state": record.get("state", ""), "industry": record.get("industry", ""),
                "eng_headcount": record.get("eng_headcount", ""),
                "board": board,
                "job_signals_already_found": sorted({s["code"] for s in job_data.get("signals", [])}),
            }))
        index = i // settings.batch_size
        name = f"in-{index:02d}.jsonl"
        (research_dir / name).write_text("\n".join(lines) + "\n", encoding="utf-8")
        names.append(name)

    template = _template or (Path(__file__).resolve().parent.parent / "prompts" / "research.md") \
        .read_text(encoding="utf-8")
    instructions = render_prompt(template, catalog, today, names)
    (research_dir / "INSTRUCTIONS.md").write_text(instructions, encoding="utf-8")

    verify_path = Path(__file__).resolve().parent.parent / "prompts" / "verify.md"
    if verify_path.exists():
        verify = render_prompt(verify_path.read_text(encoding="utf-8"), catalog, today, names)
        (research_dir / "VERIFY.md").write_text(verify, encoding="utf-8")
    return {"batches": names, "accounts": len(rows),
            "instructions": str(research_dir / "INSTRUCTIONS.md"),
            "verify": str(research_dir / "VERIFY.md")}
