"""Minimal YAML-frontmatter parser for SKILL.md (the Agent Skills subset).

No PyYAML: the workbench takes no external dependencies. Handles what the
Agent Skills format uses: plain and quoted scalars, ``>``/``|`` block
scalars, one nested map (``metadata:``) and lists (``allowed-tools``).
Anything else is skipped; malformed input never raises.
"""

import re

_KEY_RE = re.compile(r"^([A-Za-z0-9_-]+):(?:\s+(.*))?$")


def _split(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return "", text
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return "".join(lines[1:i]), "".join(lines[i + 1:])
    return "", text


def _scalar(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        inner = raw[1:-1]
        return inner.replace("''", "'") if raw[0] == "'" else inner.replace('\\"', '"')
    if " #" in raw:
        raw = raw.split(" #", 1)[0].rstrip()
    return raw


def _block_scalar(style: str, block: list[str]) -> str:
    texts = [b.strip() for b in block]
    while texts and not texts[-1]:
        texts.pop()
    if style == "|":
        return "\n".join(texts)
    paras, current = [], []
    for t in texts:
        if t:
            current.append(t)
        elif current:
            paras.append(" ".join(current))
            current = []
    if current:
        paras.append(" ".join(current))
    return "\n".join(paras)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Return ``(fields, body)``; ``({}, text)`` when there is no frontmatter."""
    fm, body = _split(text)
    fields: dict = {}
    lines = fm.splitlines()
    i = 0
    while i < len(lines):
        m = _KEY_RE.match(lines[i])
        i += 1
        if not m:
            continue
        key, raw = m.group(1), (m.group(2) or "").strip()
        block = []
        while i < len(lines) and (not lines[i].strip() or lines[i][:1] in " \t"):
            block.append(lines[i])
            i += 1
        if raw[:1] in (">", "|"):
            fields[key] = _block_scalar(raw[0], block)
        elif raw.startswith("[") and raw.endswith("]"):
            fields[key] = [_scalar(p) for p in raw[1:-1].split(",") if p.strip()]
        elif raw:
            extra = " ".join(b.strip() for b in block if b.strip())
            fields[key] = _scalar(f"{raw} {extra}" if extra else raw)
        else:
            items = [b.strip() for b in block if b.strip()]
            if items and all(it.startswith("- ") for it in items):
                fields[key] = [_scalar(it[2:]) for it in items]
            elif items:
                sub = {}
                for it in items:
                    mm = _KEY_RE.match(it)
                    if mm:
                        sub[mm.group(1)] = _scalar(mm.group(2) or "")
                fields[key] = sub
    return fields, body
