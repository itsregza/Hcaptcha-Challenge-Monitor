from __future__ import annotations

import re
from typing import Any

SKIP = (
    "afrikaans", "albanian", "amharic", "arabic", "armenian", "azerbaijani",
    "basque", "belarusian", "bengali", "bulgarian", "bosnian", "burmese",
    "catalan", "cebuano", "chinese", "corsican", "croatian", "czech", "danish",
    "dutch", "english", "esperanto", "estonian", "finnish", "portuguese",
    "french", "frisian", "galician", "georgian", "german", "greek", "gujarati",
    "haitian", "hausa", "hawaiian", "hebrew", "hindi", "hmong", "hungarian",
    "icelandic", "igbo", "indonesian", "irish", "italian", "japanese",
    "javanese", "kannada", "kazakh", "khmer", "korean", "kurdish", "kyrgyz",
    "lao", "latin", "latvian", "lithuanian", "luxembourgish", "macedonian",
    "malagasy", "malay", "malayalam", "maltese", "maori", "marathi",
    "mongolian", "myanmar", "nepali", "norwegian", "nyanja", "odia", "pashto",
    "persian", "polish", "punjabi", "romanian", "russian", "samoan",
    "scots gaelic", "serbian", "sesotho", "shona", "sindhi", "sinhala",
    "slovak", "slovenian", "somali", "spanish", "sundanese", "swahili",
    "swedish", "tajik", "tamil", "tatar", "telugu", "thai", "turkish",
    "turkmen", "ukrainian", "urdu", "uyghur", "uzbek", "vietnamese",
    "welsh", "xhosa", "yiddish", "yoruba", "zulu", "sotho", "southern sotho",
    "please select an image to report", "privacy", "terms", "try again",
    "skip", "select a language", "accessibility", "hcaptcha", "report",
)

HINTS = (
    "click", "select", "find", "pick", "choose", "identify",
    "containing", "habitat", "reference", "wear", "please click",
    "foldable", "grow", "cost", "money", "animal", "example",
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
        if not any(h in tl for h in HINTS):
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
        aria = b.get("aria") or ""
        m = re.match(r"Challenge Image (\d+)", aria, re.I)
        if m:
            out.append({"idx": int(m.group(1)) - 1})
            continue
        if "task" in cls:
            out.append({"idx": len(out)})
    return out


def snap_ready(snap: dict[str, Any]) -> bool:
    href = ((snap.get("loc") or {}).get("href") or "").lower()
    if "frame=challenge" not in href:
        return False
    if len(grid_tasks(snap)) >= 9:
        return True
    if len(snap.get("bg_els") or []) >= 9:
        return True
    return bool(prompt_from_texts(snap.get("texts") or []))
