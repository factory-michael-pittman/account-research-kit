"""Posting text parsing: HTML -> sections -> verbatim responsibility bullets.

Ported unchanged from the account-value repo's parse.py (originally from the
golden Crunchyroll build), minus the pay-extraction machinery this kit does
not need.
"""

from __future__ import annotations

import html
import re

RESP_HEADING_PAT = re.compile(
    r"(responsibilit|responsabilit|repsonsibilit|resposibilit|responsiblit|duties|"
    r"what you( will|.?ll) (do|be doing|accomplish)|"
    r"what you( will|.?ll) work on|"
    r"your (role|responsibilities|mission)|about (the|your|this) (role|job|position)|the role|"
    r"key responsibilities|day.?to.?day|in this role|areas of responsibility|"
    r"role overview|^the opportunity$|^what your contributions|"
    r"key areas of focus|^how you.?ll|^you will\b|you will$|"
    r"^main activities|^the work!? ?$|^success in your first|"
    r"^what your team does|^a day in the life|"
    r"purpose of the (job|role|position)|o seu desafio|sobre a vaga|担当業務|職務内容)",
    re.I,
)
STOP_HEADING_PAT = re.compile(
    r"^(what (we.?re looking for|you( will|.?ll) (need|bring)|we look for|makes|success)|"
    r"qualification|requirements?|"
    r"(basic|minimum) (qualification|requirement)s?|"
    r"who you are|you (are|have)|about you|nice to have|bonus points|skills|"
    r"experience|education|skills & competencies|working conditions|"
    r"about (us|the team|the opportunity|the company)|about the team|"
    r"our (mission|values|benefits)|compensation|pay|benefits|perks|culture|hybrid model|"
    r"why (join|you( will|.?ll) love)|what you.?ll love|let.?s talk about you|"
    r".*perks|knowledge\b|exigences|salary range|technical expertise|"
    r"additional capabilities|o que buscamos|we.?re excited about you|"
    r"eoe|equal|total rewards|how to apply|"
    r"future growth|応募資格|deployment.*|our commitment)",
    re.I,
)


def html_to_text(raw: str) -> str:
    text = html.unescape(raw)
    text = re.sub(r"<h[1-6][^>]*>", "\n@@H@@ ", text, flags=re.I)
    text = re.sub(r"</h[1-6]>", "\n", text, flags=re.I)
    text = re.sub(r"<(li|p|div|br|tr)[^>]*>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)  # second pass for double-escaped entities
    text = text.replace("\xa0", " ")
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.split("\n")]
    return "\n".join(ln for ln in lines if ln)


def extract_bullets(text: str) -> list[tuple[str, str]]:
    """Return (section, bullet_text) under responsibility-like headings."""
    out, section, in_resp = [], "", False
    lines = text.split("\n")
    for line in lines:
        m = re.match(r"^@@H@@\s*(.*)$", line)
        virtual = False
        if m:
            heading = m.group(1).strip().rstrip(":").strip()
        else:
            virtual = (len(line) < 70 and not line.endswith((".", "!", "?", ";"))
                       and (RESP_HEADING_PAT.search(line) or STOP_HEADING_PAT.match(line)))
            heading = line.strip().rstrip(":").strip()
            if not virtual:
                heading = None
        if heading is not None:
            if STOP_HEADING_PAT.match(heading):
                in_resp = False
                continue
            if RESP_HEADING_PAT.search(heading):
                section, in_resp = heading, True
            elif in_resp and not virtual:
                section = heading
            continue
        if not in_resp or not line:
            continue
        clean = re.sub(r"^[\-\u2022\*\u25cf\u25aa\u00b7\d{1,2}[\].)]\s*", "", line).strip()
        if 25 < len(clean) < 600 and not clean.endswith("?"):
            out.append((section, clean))
    if out:
        return out
    # Fallback: sentences from prose paragraphs under responsibility sections.
    out2, section, in_resp = [], "", False
    for line in lines:
        m = re.match(r"^@@H@@\s*(.*)$", line)
        virtual = False
        if m:
            heading = m.group(1).strip().rstrip(":").strip()
        else:
            virtual = (len(line) < 70 and not line.endswith((".", "!", "?", ";"))
                       and (RESP_HEADING_PAT.search(line) or STOP_HEADING_PAT.match(line)))
            heading = line.strip().rstrip(":").strip() if virtual else None
        if heading is not None:
            if STOP_HEADING_PAT.match(heading):
                in_resp = False
                continue
            if RESP_HEADING_PAT.search(heading):
                section, in_resp = heading, True
            continue
        if not in_resp or not line:
            continue
        for sent in re.split(r"(?<=[.;!])\s+", line):
            sent = sent.strip()
            if 40 < len(sent) < 600:
                out2.append((section, sent))
    return out2
