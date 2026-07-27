from __future__ import annotations

import re
from typing import Any

SKIP = (
    "afrikaans", "albanian", "amharic", "arabic", "armenian", "azerbaijani",
    "basque", "belarusian", "bengali", "bulgarian", "bosnian", "burmese",
    "catalan", "cebuano", "chinese", "corsican", "croatian", "czech", "danish",
    "dutch", "english", "esperanto", "estonian", "finnish", "portuguese",
    "please select an image to report", "privacy", "terms", "try again",
    "skip", "select a language", "accessibility", "hcaptcha",
)

HINTS = (
    "click", "select", "find", "pick", "choose", "identify",
    "containing", "habitat", "reference", "wear", "please click",
    "foldable", "grow", "cost", "money",
)


def prompt_is_challenge(prompt: str) -> bool:
    tl = (prompt or "").lower().strip()
    if len(tl) < 14:
        return False
    if any(s in tl for s in SKIP):
        return False
    return any(h in tl for h in HINTS)


def prompt_from_texts(texts: list[str]) -> str:
    best = ""
    best_score = -1
    for raw in texts or []:
        t = (raw or "").strip()
        if len(t) < 14:
            continue
        tl = t.lower()
        if any(s in tl for s in SKIP):
            continue
        if tl.startswith("please select an image"):
            continue
        score = len(t)
        if any(h in tl for h in HINTS):
            score += 1000
        if score > best_score:
            best_score = score
            best = t
    return best.strip()


def grid_tasks(snap: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for b in snap.get("buttons") or []:
        cls = (b.get("cls") or "").lower()
        if "task" not in cls:
            continue
        aria = b.get("aria") or ""
        m = re.match(r"Challenge Image (\d+)", aria)
        if not m:
            continue
        out.append({"idx": int(m.group(1)) - 1})
    return out


def snap_ready(snap: dict[str, Any]) -> bool:
    href = ((snap.get("loc") or {}).get("href") or "").lower()
    if "frame=challenge" not in href:
        return False
    return len(grid_tasks(snap)) >= 9
