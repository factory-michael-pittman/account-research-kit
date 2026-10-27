"""Pool intake: pull accounts from Salesforce by owner, or read a CSV.

The Salesforce step needs the `sf` CLI authenticated to your org
(`sf org login web`). Everything after intake is plain files, so a hand-built
CSV works too: the only required columns are Id and Name (Id can be blank; one
is synthesized), and the fit step is stronger with the ZoomInfo-style fields
it recognizes.
"""

from __future__ import annotations

import csv
import shutil
import subprocess
from pathlib import Path

from account_kit.catalog import Settings

POOL_FIELDS = [
    "Id", "Name", "Website", "Type", "BillingCountry", "BillingState", "BillingCity",
    "ShippingCountry", "ShippingState", "NumberOfEmployees", "ZI_Employee_Count__c",
    "ZI_Engineering_Headcount__c", "ZI_Primary_Industry__c", "ZI_Primary_Sub_Industry__c",
    "ZI_Past_1_Year_Employee_Growth_Rate__c", "ZI_Product_Technology_Products_Used__c",
    "Org_ID__c", "Account_Tier__c", "LastActivityDate", "ZI_Ultimate_Parent_Company_Name__c",
]


def _soql_text(value: str) -> str:
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def pool_query(owners: list[str], ownership_in: list[str | None]) -> str:
    if not owners:
        raise ValueError("no owners configured: set sfdc.owners in config/settings.yaml "
                         "or pass --owners")
    names = ", ".join(_soql_text(o) for o in owners)
    where = f"Owner.Name IN ({names})"
    values = [v for v in ownership_in if v is not None]
    if values or len(ownership_in) != len(values):
        # A null in ownership_in means "no Ownership value set".
        clause = " OR ".join(["Ownership__c = null"] + [f"Ownership__c = {_soql_text(v)}" for v in values])
        where += f" AND ({clause})"
    return f"SELECT {', '.join(POOL_FIELDS)} FROM Account WHERE {where}"


def export(settings: Settings, out: Path, owners: list[str] | None = None) -> Path:
    """Pull the pool from Salesforce into `out` (a CSV)."""
    if shutil.which("sf") is None:
        raise RuntimeError("the sf CLI was not found; install Salesforce CLI or use --from-csv")
    query = pool_query(list(owners or settings.owners), list(settings.ownership_in))
    out.parent.mkdir(parents=True, exist_ok=True)
    done = subprocess.run(
        ["sf", "data", "export", "bulk", "--query", query, "--output-file", str(out),
         "--result-format", "csv", "--wait", "10", "--target-org", settings.org],
        capture_output=True, text=True, check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"sf export failed: {done.stderr or done.stdout}\nquery: {query}")
    return out


OPTIONAL_FIELDS = ("Website", "ZI_Engineering_Headcount__c", "ZI_Employee_Count__c",
                  "ZI_Primary_Industry__c", "ZI_Primary_Sub_Industry__c", "BillingCountry",
                  "BillingState", "ShippingCountry", "ShippingState")


def normalize_csv(source: Path, out: Path) -> list[str]:
    """Copy a hand-made pool CSV into place, synthesizing Ids and reporting
    which optional fields are missing (they weaken the fit step)."""
    with source.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"{source} has no data rows")
    if "Name" not in (rows[0] or {}):
        raise ValueError(f"{source} needs at least a Name column")
    missing = [f for f in OPTIONAL_FIELDS if f not in rows[0]]
    fixed = []
    for i, row in enumerate(rows, 1):
        if None in row:  # DictReader parks surplus cells under the None key
            raise ValueError(f"{source} row {i} has more fields than the header; "
                             "quote the cells that contain commas and match the header count")
        row = {k: (v or "").strip() for k, v in row.items()}
        if not row.get("Id"):
            row["Id"] = f"csv-{i:04d}"
        fixed.append(row)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(fixed[0].keys()), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(fixed)
    return missing
