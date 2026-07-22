"""Task title helpers (no Django model imports)."""

from __future__ import annotations

import re

_LEADING_BRACKET_RE = re.compile(r"^\[([^\]]+)\]\s*")


def strip_portal_title_prefix(title: str, portal=None) -> str:
    """Remove leading [portal] tags we used to inject into agency Bitrix titles."""
    text = (title or "").strip()
    if not text:
        return text

    labels: set[str] = set()
    if portal is not None:
        name = str(getattr(portal, "name", "") or "").strip()
        domain = str(getattr(portal, "domain", "") or "").strip()
        domain = domain.replace("https://", "").replace("http://", "").split("/")[0].strip()
        if name:
            labels.add(name)
        if domain:
            labels.add(domain)
            labels.add(domain.split(".")[0])

    while text.startswith("["):
        match = _LEADING_BRACKET_RE.match(text)
        if not match:
            break
        label = match.group(1).strip()
        label_l = label.lower()
        known = any(label_l == x.lower() for x in labels if x)
        # Legacy prefixes look like [b24-xxxxx] even without portal context
        if known or label_l.startswith("b24-"):
            text = text[match.end() :].lstrip()
            continue
        break
    return text.strip() or (title or "").strip()
