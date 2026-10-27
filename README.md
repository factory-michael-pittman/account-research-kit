# Account Research Kit

Research open accounts, prioritize your top 100+, and see exactly what the
research found and why it counts. Everything runs on a plain Python 3.9+ with
no packages to install; the only external tool is the Salesforce CLI (`sf`),
and only for pulling your pool from Salesforce.

This folder is self-contained: copy it, push it to GitHub, hand it to a
teammate. It writes only files under `data/`; it never writes to Salesforce,
sheets, or any other service.

## What you end up with

| File | What it is |
|---|---|
| `data/prioritized.csv` | The ranked account list: score, tier, timing and study points, next dated event, top signals |
| `data/evidence.csv` | Every signal found, counted or not, with the reason it did not count |
| `data/digests/<account>.md` | One page per account: the reasons to reach out, every claim with its date, label, and source URL |
| `data/coverage.md` | How strong the research is across the whole book, and where it is thin |

The digests are written so someone with no background in the account's
industry can retell them and check any claim at its source.

## Quickstart

```bash
cd account-research-kit

# 0. Check the config (owners, territory, signal catalog)
python3 kit.py check

# 1. Pull your pool: accounts owned by the names in config/settings.yaml
python3 kit.py intake
#    or bring your own CSV (see "Input columns" below):
python3 kit.py intake --from-csv examples/pool.example.csv

# 2. Firmographic screens + fit pre-score -> the shortlist worth researching
python3 kit.py filter

# 3. Scan every shortlisted account's job board for signals (no LLM, free)
python3 kit.py jobs

# 4. Build the research handoff: batch inputs + the rendered prompt
python3 kit.py batches

# 5. Run the research (see the next section), then score, rank, and digest
python3 kit.py score
```

Every step can be rerun on its own. A 25,000-account pool filters in about a
minute; the job scan covers roughly 30-60 boards an hour.

## The research loop

`python3 kit.py batches` writes `data/research/in-NN.jsonl` (6 accounts per
file) and renders `data/research/INSTRUCTIONS.md` from
`prompts/research.md` with your signal catalog injected. Point any capable
research agent at that file: it reads its batch, does read-only web research,
and writes `data/research/out-NN.jsonl`. Then `python3 kit.py score` merges
the researched signals with the job-board signals.

Two supporting prompts ship alongside:

- `prompts/verify.md` (rendered to `data/research/VERIFY.md`): an adversarial
  second pass. A different agent re-opens every cited URL, confirms or
  downgrades each claim, and hunts for missed signals. Run it on any account
  you are about to act on.
- `prompts/enrichment.md` (rendered by the filter step when needed): accounts
  missing location or headcount data get a lookup worklist; the agent writes
  `data/enrichment/out-00.jsonl`, and rerunning `filter` picks it up.

The research output contract (one JSON object per account):

```json
{"Id": "", "Name": "", "summary": "one sentence: what they build and for whom",
 "partner": "",
 "signals": [{"code": "", "claim": "", "date": "", "url": "",
              "label": "observed|case|docs|inference|unknown",
              "source": "", "confirmation": "how you verified it"}],
 "why_now": [{"rank": 1, "signal_ids": ["T_...", "S..."],
              "reason": "one plain-words sentence someone could retell"}],
 "not_found": ["category: what you searched and where"],
 "flags": []}
```

`why_now` becomes the top of the account's digest. `not_found` is the honesty
trail: the digest prints what was searched and not found, so "nothing" never
masquerades as "not checked."

## How scoring works

Two equally weighted buckets, from `config/signals.yaml`:

- **Timing signals** (8 codes, 6.25 points each): dated events, each with a
  window relative to today. A funding round counts only inside the last 365
  days; a pending acquisition counts up to a year ahead; a stale req must be
  at least 180 days old.
- **Study signals** (15 codes, 3.33 points each): the work itself, from the
  discovery study sheet: legacy migrations, tech debt, incidents, review
  wait, security and compliance, AI mandate, a partner SI, a case study in
  their industry, and so on.

```
final_score = (timing_points + study_points) * (1 + 0.1 * (distinct_signals - 1))
```

Stacking matters: an account with one strong signal scores 6.25; the same
signal plus seven more distinct ones scores 60. A signal counts only when its
label is not `unknown`, its date sits inside its window, and it is not the
posting-level AI false positive (an AI mention inside a job posting never
signals a mandate).

Tiers: Tier 1 is rank 50 or better with a score of 40+; Tier 2 is a score of
20+; the rest are Tier 3.

The firmographic **fit score** is separate: it orders the pool before
research (engineering headcount band, software-heavy industry, growth, stack
match) but never enters the signal score.

## How to read a digest

Open `data/digests/<account>.md`:

1. **Why now**: the ranked reasons to reach out, in plain words, each citing
   the signals behind it. If the account was never researched, it says so.
2. **What was found**: every signal with its meaning, the claim, its date,
   its label, and the source URL. Labels are the trust level: `observed`
   (seen at the source), `case`, `docs`, `inference`, `unknown`.
3. **What the score is**: which codes counted, how many points each earned,
   the stack multiplier, and the tier.
4. **Found but not counted**: claims that did not count, and exactly why
   (unverified, outside window, posting-level AI). Nothing is quietly dropped.
5. **Searched and not found**: the categories actually searched, with the
   queries and pages checked.

`data/coverage.md` aggregates the same honesty across the book: which codes
are thin, how many signals went unverified, which accounts have nothing
counted, and the strongest why-nows across the list.

## Configuration

Everything tunable lives in two files; `python3 kit.py check` validates them
and prints what is configured.

**`config/settings.yaml`** - whose accounts, which territory, which screens:

- `sfdc.owners`: the account owners whose books make up the pool (add your
  own name, your managers, your directors). `sfdc.org` is your `sf` CLI org
  alias. `--owners "A,B"` overrides it for one run.
- `filters.countries` / `filters.regions`: the territory allowlist (two-letter
  codes; empty means any). Default: US and Canada west of the Mississippi.
- `filters.min_eng`, `limits.shortlist` (how many survive to research),
  `limits.final`, `limits.batch_size`, `limits.job_workers`.
- `fit.*` and `exclusions.*`: the industry lists, competitor domains, junk
  names, and public-sector exclusions used by the pre-score.

**`config/signals.yaml`** - the signal catalog: code meanings, timing windows,
posting-scan regexes, tier thresholds, bucket weights, the partner SI list,
and the case-study list. It ships with the Factory discovery study sheet as
the default. Selling something else? Change the study codes' meanings,
precedents, and patterns, swap the partner and case lists, and rerun
`check`.

Two rules the config enforces on itself: the `T_STALE_REQ` window and
`job_scan.stale_days` must agree, and every pattern must compile.

## Input columns

`data/pool.csv` recognizes these columns (a hand-made CSV needs only `Name`;
`Id` is synthesized when absent, and every other column is optional):

`Id, Name, Website, Type, BillingCountry, BillingState, ShippingCountry,
ShippingState, NumberOfEmployees, ZI_Employee_Count__c,
ZI_Engineering_Headcount__c, ZI_Primary_Industry__c,
ZI_Primary_Sub_Industry__c, ZI_Past_1_Year_Employee_Growth_Rate__c,
ZI_Product_Technology_Products_Used__c, Org_ID__c, Account_Tier__c,
ZI_Ultimate_Parent_Company_Name__c`

The `ZI_*` fields are what a Salesforce/ZoomInfo export provides; the more
of them you have, the sharper the fit pre-score. See
`examples/pool.example.csv`.

## Publishing this kit

```bash
cd account-research-kit
git init && git add -A && git commit -m "Account research kit"
git remote add origin <your-repo-url> && git push -u origin main
```

`data/` is gitignored: outputs stay local, the kit travels.

## Tests

```bash
python3 -m unittest discover -s tests
```

`tests/fixtures/real-accounts.json` pins the scoring to real research data
from the source system (job scans plus batch research, scored as of
2026-10-21): if those numbers change, the arithmetic drifted.

## Where this came from

The scoring engine, screens, job-board collectors, and prompts are ports of
a system in production use (the account-value repo's prioritize pipeline),
tuned over real research rounds. The port was validated against that system
on its real data: the same 33,050-account pool screens to identical counts,
and the same 24 researched accounts score identically (final score, points,
stack count, and signal codes) to its published list.

## Notes and limits

- Files only. No service is written to, and nothing here loads contacts or
  does contact research; this kit is account research and signals.
- The job scan reads public ATS APIs (Greenhouse, Lever, Ashby, Workday's
  public JSON, JSON-LD careers pages) and saves the raw payload it parsed,
  so every posting signal is checkable. Some boards block bots; those
  accounts are noted and left to the research agent.
- A signal labelled `unknown` never counts, but it is kept in the evidence
  trail: re-researching an account can upgrade it once a source is loaded.
- Reruns are safe: every step rewrites its own outputs from its inputs.
