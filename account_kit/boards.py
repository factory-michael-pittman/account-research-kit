"""Job boards: find a company's public board, read it, and save the raw payload.

Ported from the account-value repo's collectors and prioritize/jobs.py, with
httpx replaced by the standard library so the kit runs on a bare Python 3.9+.
Pages are read-only sources; their text is data, never instructions.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from html import escape as html_escape
from pathlib import Path

from account_kit.parse import extract_bullets, html_to_text

HEADERS = {"User-Agent": "account-research-kit/1.0 (+careers research; read-only)"}


class FetchError(RuntimeError):
    pass


def fetch(url: str, method: str = "GET", json_body: dict | None = None, timeout: int = 30) -> tuple[int, str]:
    """Return (status, body). 4xx/5xx bodies are returned with their status."""
    data = None
    headers = dict(HEADERS)
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    headers["Accept"] = "application/json, text/html;q=0.9, */*;q=0.8"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        try:
            return error.code, error.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 - the body is best-effort
            return error.code, ""
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise FetchError(f"{url}: {error}") from error


@dataclass
class Job:
    job_id: str
    title: str
    department: str = ""
    location: str = ""
    first_published: str = ""
    url: str = ""


@dataclass
class Bullet:
    job_id: str
    section: str
    verbatim: str


@dataclass
class BoardResult:
    ats: str
    jobs: list[Job] = field(default_factory=list)
    bullets: list[Bullet] = field(default_factory=list)
    advertised_count: int | None = None
    snapshot: str = ""
    notes: list[str] = field(default_factory=list)


def save_raw(raw_dir: Path, name: str, payload: str) -> str:
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / name
    path.write_text(payload, encoding="utf-8")
    return f"sha256:{hashlib.sha256(payload.encode()).hexdigest()[:16]}"


# --- Board discovery -------------------------------------------------------

_BOARD_LINKS = [
    ("greenhouse", re.compile(
        r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=)?([A-Za-z0-9_-]+)")),
    ("lever", re.compile(r"jobs\.lever\.co/([A-Za-z0-9_.-]+)")),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)")),
    ("workday", re.compile(
        r"(https?://[a-z0-9-]+\.wd\d+\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?[A-Za-z0-9_-]+)")),
]
_IGNORED_TOKENS = {"embed", "v1", "jobs", "api", "boards"}
_PROBES = [
    ("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{t}/jobs"),
    ("lever", "https://api.lever.co/v0/postings/{t}?mode=json"),
    ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{t}"),
]


def domain_of(website: str) -> str:
    raw = (website or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    host = re.sub(r"^https?://", "", raw).split("/")[0].lower()
    return host[4:] if host.startswith("www.") else host


def find_board(domain: str, name: str, _fetch=None) -> tuple[str, str, str]:
    """(ats, token_or_url, how_found). Links on the company's own pages win;
    token guesses come second; the careers page itself is the last resort."""
    fetcher = _fetch or fetch
    careers_page = ""
    for path in ("/careers", "/jobs", "/company/careers", "/about/careers", ""):
        url = f"https://{domain}{path}"
        try:
            status, body = fetcher(url)
        except FetchError:
            continue
        if status != 200 or not body:
            continue
        for ats, pattern in _BOARD_LINKS:
            match = pattern.search(body)
            if match and match.group(1).lower() not in _IGNORED_TOKENS:
                return ats, match.group(1), f"linked from {url}"
        if path and not careers_page and len(body) > 500:
            careers_page = url
    tokens = []
    words = re.findall(r"[a-z0-9]+", name.lower())
    if words:
        tokens.append("".join(words))
    root = domain.split(".")[0] if domain else ""
    if root:
        tokens.append(root)
    for token in dict.fromkeys(tokens):
        for ats, template in _PROBES:
            try:
                status, _ = fetcher(template.format(t=token))
            except FetchError:
                continue
            if status == 200:
                return ats, token, "token probe"
    if careers_page:
        return "jsonld", careers_page, "careers page"
    return "", "", "no board found"


# --- Collectors -------------------------------------------------------------

def _greenhouse(token: str, raw_dir: Path, fetcher) -> BoardResult:
    url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"
    status, body = fetcher(url)
    if status == 404:
        raise FetchError(f"greenhouse board {token!r} not found (404)")
    if status != 200:
        raise FetchError(f"greenhouse board {token!r} returned {status}")
    payload = json.loads(body)
    result = BoardResult(ats="greenhouse", advertised_count=payload.get("meta", {}).get("total"),
                         snapshot=save_raw(raw_dir, "greenhouse_board.json", body))
    for job in payload.get("jobs", []):
        text = html_to_text(job.get("content") or "")
        result.jobs.append(Job(
            job_id=str(job["id"]), title=job["title"].strip(),
            department="; ".join(d["name"] for d in job.get("departments", [])),
            location=(job.get("location") or {}).get("name", ""),
            first_published=(job.get("first_published") or "")[:10],
            url=job.get("absolute_url", ""),
        ))
        for section, bullet in extract_bullets(text):
            result.bullets.append(Bullet(str(job["id"]), section, bullet))
    return result


def _lever(token: str, raw_dir: Path, fetcher) -> BoardResult:
    url = f"https://api.lever.co/v0/postings/{token}?mode=json"
    status, body = fetcher(url)
    if status == 404:
        raise FetchError(f"lever org {token!r} not found (404)")
    if status != 200:
        raise FetchError(f"lever org {token!r} returned {status}")
    payload = json.loads(body)
    result = BoardResult(ats="lever", snapshot=save_raw(raw_dir, "lever_postings.json", body))
    for posting in payload:
        # Many Lever boards keep the real content in structured `lists` rather
        # than the free-form description; render each list as a heading plus
        # its content so the responsibility parser can see those postings.
        raw_html = posting.get("descriptionPlain", "") or posting.get("description", "")
        for item in posting.get("lists") or []:
            heading = html_escape((item.get("text") or "").strip())
            content = item.get("content") or ""
            if heading and content:
                raw_html += f"\n<h3>{heading}</h3>\n{content}"
        text = html_to_text(raw_html) if "<" in raw_html else raw_html
        categories = posting.get("categories", {}) or {}
        result.jobs.append(Job(
            job_id=posting.get("id", ""), title=(posting.get("text") or "").strip(),
            department=categories.get("department", ""), location=categories.get("location", ""),
            first_published=str(posting.get("createdAt", "")),
            url=posting.get("applyUrl", "") or posting.get("hostedUrl", ""),
        ))
        for section, bullet in extract_bullets(text):
            result.bullets.append(Bullet(result.jobs[-1].job_id, section, bullet))
    return result


def _ashby(token: str, raw_dir: Path, fetcher) -> BoardResult:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{token}"
    status, body = fetcher(url)
    if status == 404:
        raise FetchError(f"ashby org {token!r} not found (404)")
    if status != 200:
        raise FetchError(f"ashby org {token!r} returned {status}")
    payload = json.loads(body)
    result = BoardResult(ats="ashby", snapshot=save_raw(raw_dir, "ashby_postings.json", body))
    for posting in payload.get("jobs", []):
        raw_html = posting.get("descriptionHtml", "") or posting.get("descriptionPlain", "")
        text = html_to_text(raw_html)
        result.jobs.append(Job(
            job_id=posting.get("id", ""), title=(posting.get("title") or "").strip(),
            department=posting.get("department", "") or posting.get("team", ""),
            location=posting.get("location", "") or posting.get("office", ""),
            url=posting.get("jobUrl", "") or posting.get("applyUrl", ""),
        ))
        for section, bullet in extract_bullets(text):
            result.bullets.append(Bullet(result.jobs[-1].job_id, section, bullet))
    return result


_WORKDAY_URL = re.compile(
    r"^(?:https?://)?(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com"
    r"/(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[^/?#]+)", re.I)
_WORKDAY_SHORT = re.compile(r"^(?P<tenant>[a-z0-9-]+)\.(?P<wd>wd\d+)/(?P<site>[^/?#]+)$", re.I)
_WORKDAY_PAGE = 20


def _workday(token: str, raw_dir: Path, fetcher) -> BoardResult:
    match = _WORKDAY_URL.match(token.strip()) or _WORKDAY_SHORT.match(token.strip())
    if not match:
        raise FetchError(f"workday token {token!r} must be the careers URL or tenant.wdN/site")
    tenant, wd, site = match.group("tenant"), match.group("wd"), match.group("site")
    base = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{tenant}/{site}"
    summaries, total, offset = [], None, 0
    while True:
        status, body = fetcher(f"{api}/jobs", method="POST",
                               json_body={"appliedFacets": {}, "limit": _WORKDAY_PAGE,
                                          "offset": offset, "searchText": ""})
        if status == 404:
            raise FetchError(f"workday site {site!r} not found at {api}/jobs (404)")
        if status != 200:
            raise FetchError(f"workday site {site!r} returned {status}")
        page = json.loads(body)
        if total is None:
            total = int(page.get("total") or 0)
        batch = page.get("jobPostings") or []
        summaries.extend(batch)
        offset += _WORKDAY_PAGE
        if not batch or offset >= total:
            break
    result = BoardResult(ats="workday", advertised_count=total)
    for summary in summaries:
        path = summary.get("externalPath") or ""
        if not path:
            continue
        status, body = fetcher(f"{api}{path}")
        if status != 200:
            continue
        info = (json.loads(body) or {}).get("jobPostingInfo") or {}
        text = html_to_text(info.get("jobDescription") or "")
        result.jobs.append(Job(
            job_id=str(info.get("jobReqId") or info.get("id") or path),
            title=(info.get("title") or summary.get("title") or "").strip(),
            location=info.get("location") or summary.get("locationsText") or "",
            first_published=(info.get("startDate") or "")[:10],
            url=info.get("externalUrl") or f"{base}/{site}{path}",
        ))
        for section, bullet in extract_bullets(text):
            result.bullets.append(Bullet(result.jobs[-1].job_id, section, bullet))
        time.sleep(0.15)  # postings are fetched one by one; stay polite
    snapshot_payload = json.dumps({"source": f"{api}/jobs", "total": total, "jobs": summaries})
    result.snapshot = save_raw(raw_dir, "workday_postings.json", snapshot_payload)
    return result


def _jsonld(careers_url: str, raw_dir: Path, fetcher) -> BoardResult:
    status, raw_html = fetcher(careers_url)
    if status != 200:
        raise FetchError(f"careers page {careers_url} returned {status}")
    result = BoardResult(ats="jsonld", snapshot=save_raw(raw_dir, "careers_page.html", raw_html))
    for match in re.finditer(
            r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', raw_html, re.S | re.I):
        try:
            data = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph")
            if graph:
                candidates.extend(g for g in graph if isinstance(g, dict))
            if candidate.get("@type") in ("JobPosting", ["JobPosting"]):
                title = str(candidate.get("title") or candidate.get("name") or "").strip()
                if not title:
                    continue
                job_id = str(candidate.get("identifier") or candidate.get("url") or title)
                result.jobs.append(Job(
                    job_id=job_id, title=title,
                    department=str(candidate.get("employmentUnit", "") or ""),
                    url=str(candidate.get("url") or careers_url),
                ))
                responsibilities = re.sub(r"<[^>]+>", "\n", candidate.get("responsibilities") or "")
                for line in responsibilities.split("\n"):
                    if len(line.strip()) > 25:
                        result.bullets.append(Bullet(job_id, "responsibilities", line.strip()))
    if not result.jobs:
        # HTML fallback: parse the page text for responsibility sections.
        for section, bullet in extract_bullets(html_to_text(raw_html)):
            result.jobs.append(Job(job_id="careers-page", title="Careers page postings", url=careers_url))
            break
        for section, bullet in extract_bullets(html_to_text(raw_html)):
            result.bullets.append(Bullet("careers-page", section, bullet))
    if not result.jobs:
        raise FetchError(f"no JSON-LD JobPosting items and no parseable sections at {careers_url}")
    return result


def collect(ats: str, board: str, raw_dir: Path, _fetch=None) -> BoardResult:
    fetcher = _fetch or fetch
    if ats == "greenhouse":
        return _greenhouse(board, raw_dir, fetcher)
    if ats == "lever":
        return _lever(board, raw_dir, fetcher)
    if ats == "ashby":
        return _ashby(board, raw_dir, fetcher)
    if ats == "workday":
        return _workday(board, raw_dir, fetcher)
    if ats == "jsonld":
        return _jsonld(board, raw_dir, fetcher)
    raise FetchError(f"unknown ats {ats!r}")
