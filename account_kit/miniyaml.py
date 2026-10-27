"""A strict, tiny YAML-subset reader for the kit's config files.

Supports exactly what config/*.yaml uses: nested mappings, block lists of
scalars, inline lists of scalars, quoted strings, ints, floats, booleans, and
null. Anything else fails with a line number, so a broken config is never
silently misread. The shipped configs are the reference: edit inside those
shapes only.
"""

from __future__ import annotations

import re
from pathlib import Path

_INT = re.compile(r"-?\d+\Z")
_FLOAT = re.compile(r"-?\d+\.\d*\Z")


class YamlError(ValueError):
    def __init__(self, name: str, lineno: int, message: str) -> None:
        super().__init__(f"{name}:{lineno}: {message}")
        self.name = name
        self.lineno = lineno


def _strip_comment(text: str) -> str:
    """Cut a trailing ' # comment' that sits outside quotes and brackets."""
    quote = None
    depth = 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth = max(depth - 1, 0)
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _unquote_single(body: str) -> str:
    return body.replace("''", "'")


def _unquote_double(body: str) -> str:
    out, i = [], 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append({"\\": "\\", '"': '"', "n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def _scalar(raw: str, name: str, lineno: int):
    raw = raw.strip()
    if raw == "":
        raise YamlError(name, lineno, "empty value where a scalar was expected")
    if raw[0] == "'" or raw[0] == '"':
        if len(raw) < 2 or raw[-1] != raw[0]:
            raise YamlError(name, lineno, f"unterminated quoted string {raw!r}")
        body = raw[1:-1]
        if "'" in body and raw[0] == "'" and "''" not in raw:
            raise YamlError(name, lineno, f"stray quote inside {raw!r} (escape it as '')")
        if '"' in body and raw[0] == "'":
            pass  # a double quote inside single quotes is literal text
        return _unquote_single(body) if raw[0] == "'" else _unquote_double(body)
    if raw in ("null", "~"):
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    if _INT.fullmatch(raw):
        return int(raw)
    if _FLOAT.fullmatch(raw):
        return float(raw)
    if ": " in raw or raw.endswith(":"):
        raise YamlError(name, lineno, f"{raw!r} looks like a key or mapping; only scalars are allowed here")
    return raw


def _split_inline(body: str, name: str, lineno: int) -> list[str]:
    parts, buf, quote, depth = [], [], None, 0
    for ch in body:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "'\"":
            quote = ch
        elif ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return [p.strip() for p in parts]


def _value(raw: str, name: str, lineno: int):
    raw = raw.strip()
    if raw.startswith("["):
        if not raw.endswith("]"):
            raise YamlError(name, lineno, f"inline list {raw!r} does not close with ]")
        items = _split_inline(raw[1:-1], name, lineno)
        if not items or items == [""]:
            return []
        return [_scalar(part, name, lineno) for part in items]
    return _scalar(raw, name, lineno)


def _key(text: str, name: str, lineno: int) -> str:
    text = text.strip()
    if not text:
        raise YamlError(name, lineno, "empty key")
    if text[0] in "'\"":
        key = _scalar(text, name, lineno)
    else:
        key = text
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", key or ""):
        raise YamlError(name, lineno, f"key {key!r} is not a plain identifier; quote it")
    return key


def _parse_map(items, pos: int, indent: int, name: str):
    out: dict = {}
    while pos < len(items) and items[pos][0] == indent and not items[pos][1].startswith("- "):
        _, content, lineno = items[pos]
        if content.startswith("- "):
            raise YamlError(name, lineno, "list item where a 'key:' line is expected")
        key_part, sep, rest = content.partition(":")
        if not sep:
            raise YamlError(name, lineno, f"expected 'key: value'; found {content!r}")
        key = _key(key_part, name, lineno)
        if key in out:
            raise YamlError(name, lineno, f"duplicate key {key!r}")
        rest = rest.strip()
        if rest:
            out[key] = _value(rest, name, lineno)
            pos += 1
            continue
        nxt = pos + 1
        if nxt < len(items) and items[nxt][0] > indent:
            out[key], pos = _parse_block(items, nxt, items[nxt][0], name)
        elif nxt < len(items) and items[nxt][0] == indent and items[nxt][1].startswith("- "):
            out[key], pos = _parse_list(items, nxt, indent, name)
        else:
            out[key] = None
            pos += 1
    return out, pos


def _parse_list(items, pos: int, indent: int, name: str):
    out: list = []
    while pos < len(items) and items[pos][0] == indent and items[pos][1].startswith("- "):
        _, content, lineno = items[pos]
        rest = content[2:].strip()
        if not rest:
            raise YamlError(name, lineno, "empty list item")
        out.append(_value(rest, name, lineno))
        pos += 1
    if pos < len(items) and items[pos][0] == indent:
        raise YamlError(name, items[pos][2], f"expected another '- item'; found {items[pos][1]!r}")
    return out, pos


def _parse_block(items, pos: int, indent: int, name: str):
    if items[pos][1].startswith("- "):
        return _parse_list(items, pos, indent, name)
    return _parse_map(items, pos, indent, name)


def _items(text: str, name: str) -> list[tuple[int, str, int]]:
    out: list[tuple[int, str, int]] = []
    for lineno, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            raise YamlError(name, lineno, "tab indentation is not supported; use spaces")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        content = _strip_comment(stripped)
        if not content:
            continue
        out.append((len(raw) - len(raw.lstrip(" ")), content, lineno))
    return out


def loads(text: str, name: str = "<string>") -> dict:
    items = _items(text, name)
    if not items:
        return {}
    if items[0][1].startswith("- "):
        raise YamlError(name, items[0][2], "the top level must be 'key:' lines, not a list")
    value, pos = _parse_block(items, 0, items[0][0], name)
    if pos != len(items):
        _, content, lineno = items[pos]
        raise YamlError(name, lineno, f"unexpected content at this indentation: {content!r}")
    if not isinstance(value, dict):
        raise YamlError(name, items[0][2], "the top level must be 'key:' lines")
    return value


def load(path: str | Path) -> dict:
    path = Path(path)
    return loads(path.read_text(encoding="utf-8"), name=str(path))
