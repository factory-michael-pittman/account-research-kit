"""Location normalization: country names and US/CA state codes.

The allowlist (which countries and regions may pass) lives in
config/settings.yaml; this module only normalizes what Salesforce or a CSV
gives us into two-letter codes.
"""

from __future__ import annotations

US_STATES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "district of columbia": "DC",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
    "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA",
    "maine": "ME", "maryland": "MD", "massachusetts": "MA", "michigan": "MI", "minnesota": "MN",
    "mississippi": "MS", "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR",
    "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC", "south dakota": "SD",
    "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT", "virginia": "VA",
    "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
}
CA_PROVINCES = {
    "alberta": "AB", "british columbia": "BC", "manitoba": "MB", "new brunswick": "NB",
    "newfoundland and labrador": "NL", "nova scotia": "NS", "ontario": "ON",
    "prince edward island": "PE", "quebec": "QC", "québec": "QC", "saskatchewan": "SK",
    "northwest territories": "NT", "nunavut": "NU", "yukon": "YT",
}
_COUNTRY_ALIASES = {
    "united states": "US", "us": "US", "usa": "US", "u.s.": "US", "u.s.a.": "US",
    "united states of america": "US", "canada": "CA", "ca": "CA",
}


def normalize_country(value: str | None) -> str:
    """'US', 'CA', '' (unknown), or the raw value for any other country."""
    raw = (value or "").strip()
    if not raw:
        return ""
    return _COUNTRY_ALIASES.get(raw.lower(), raw)


def normalize_region(country: str, value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    table = US_STATES if country == "US" else CA_PROVINCES if country == "CA" else {}
    codes = set(table.values())
    if raw.upper() in codes:
        return raw.upper()
    return table.get(raw.lower(), raw)


def resolve_location(record: dict, enrichment: dict | None = None) -> tuple[str, str, str]:
    """Return (country, region, source). Billing wins, then shipping, then the
    web enrichment file; the first complete pair is kept."""
    candidates = [
        ("billing", record.get("BillingCountry"), record.get("BillingState")),
        ("shipping", record.get("ShippingCountry"), record.get("ShippingState")),
    ]
    if enrichment:
        candidates.append((
            f"web:{enrichment.get('source_url', '')}",
            enrichment.get("country"), enrichment.get("state"),
        ))
    partial_country = ""
    for source, country_raw, region_raw in candidates:
        country = normalize_country(country_raw)
        region = normalize_region(country, region_raw)
        if country and region:
            return country, region, source
        partial_country = partial_country or country
    return partial_country, "", "unresolved"


def country_outside(countries: frozenset[str], country: str) -> str:
    """'' when the country passes or is unknown; else the exclusion reason.
    An unknown country is never an exclusion: it is an enrichment lookup."""
    if country and countries and country not in countries:
        return f"country {country} not in the allowed list"
    return ""


def region_outside(regions: frozenset[str], country: str, region: str) -> str:
    """'' when the region passes or is unknown; else the exclusion reason."""
    if region and regions and region not in regions:
        return f"region {country}-{region} not in the allowed list"
    return ""
