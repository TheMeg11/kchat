"""Moderation: multilingual profanity filter + contact-information protection.

Normalization handles: Arabic/Persian character variants, ZWNJ, diacritics,
repeated letters, separators, leetspeak, homoglyphs (Cyrillic/fullwidth),
Persian digits and transliteration tokens (matched via Finglish dictionary).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# --------------------------------------------------------------- lists ----
FA_WORDS = [
    "کس", "کص", "کون", "کیر", "جنده", "جنده", "حرومزاده", "حرامزاده",
    "ناموس", "عوضی", "هرزه", "فاحشه", "شلوق", "لختی", "سکسی", "سیکسی",
    "پستون", "خایه", "کونده", "کیرم", "کصکش", "کسکش", "ممه", "فک",
]
FA_FINGLISH = [
    "kos", "koon", "kir", "jendeh", "jende", "kooni", "koskesh", "kirie",
    "haramzade", "namoos", "avazi", "faheshe", "sholough", "khalaf",
]
EN_WORDS = [
    "fuck", "fucking", "bitch", "asshole", "cunt", "motherfucker", "whore",
    "slut", "dickhead", "faggot", "nigger", "retard", "bastard", "pussy",
]
RU_WORDS = [
    "бля", "блядь", "хуй", "хуе", "пизда", "пизде", "ебать", "еба", "сука",
    "гандон", "мудак", "шлюха", "пидор", "заебал", "херня",
]

DEFAULT_DICTIONARY = [
    (w, "high", "block_and_warn") for w in FA_WORDS + EN_WORDS + RU_WORDS
] + [(w, "medium", "block_and_warn") for w in FA_FINGLISH]

DEFAULT_ALLOWLIST = ["kun", "kirsi", "suka"]  # avoid false positives on non-Russian tokens

# ------------------------------------------------------------ normalizer ----
_ARABIC_TO_PERSIAN = str.maketrans({"ي": "ی", "ك": "ک", "ة": "ه", "أ": "ا", "إ": "ا", "آ": "ا", "ؤ": "و"})
_PERSIAN_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "@": "a", "$": "s", "!": "i", "€": "e", "£": "l"})
_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "і": "i", "ј": "j", "һ": "h", "ԁ": "d", "ѕ": "s", "ⅼ": "l",
    "ａ": "a", "ｂ": "b", "ｃ": "c", "ｅ": "e", "ｏ": "o", "ｓ": "s", "ｔ": "t",
})
_DIACRITICS = re.compile(r"[\u064B-\u065F\u0670\u0640\u200B-\u200F\u2060\uFEFF]")
_SEPARATOR = re.compile(r"[^\w\u0600-\u06FF]+")
_REPEAT = re.compile(r"(.)\1{2,}")


def normalize(text: str) -> str:
    t = text.lower()
    t = t.translate(_PERSIAN_DIGITS)
    t = t.translate(_ARABIC_TO_PERSIAN)
    t = t.translate(_HOMOGLYPHS)
    t = t.translate(_LEET)
    t = _DIACRITICS.sub("", t)
    t = _REPEAT.sub(r"\1\1", t)
    return t


def tokens(text: str) -> list[str]:
    return [tok for tok in _SEPARATOR.split(normalize(text)) if tok]


@dataclass
class ModerationResult:
    ok: bool
    reason: Optional[str] = None
    matched: Optional[str] = None


def check_profanity(text: str, dictionary: Iterable[tuple[str, str, str]],
                    allowlist: Iterable[str]) -> ModerationResult:
    word_full_text = text
    toks = tokens(text)
    tok_set = set(toks)
    allow = {normalize(a) for a in allowlist if a}
    allow |= {a.replace(" ", "") for a in allow if a}
    joined = "".join(toks)
    for word, _severity, _action in dictionary:
        w = normalize(word)
        if not w:
            continue
        if w in allow:
            continue
        if w in tok_set:
            return ModerationResult(False, "profanity", w)
        if len(w) >= 4 and (w in joined):
            # token-aware substring: only flag when it is inside a single token
            for tok in toks:
                if w in tok and tok not in allow:
                    return ModerationResult(False, "profanity", w)
            # separators-inside-word obfuscation ("FU CK" -> "fuck")
            if w in "".join(toks):
                return ModerationResult(False, "profanity", w)
        # symbol obfuscation on the WHOLE message ("бл*ть" -> "блть", "f.u.c.k" -> "fuck")
        compact = re.sub(r"[^\w\u0600-\u06FF]", "", normalize(word_full_text))
        # exact compact match (any length >= 2, e.g. censored short words)
        if len(compact) >= 2 and w == compact:
            return ModerationResult(False, "profanity", w)
        if len(compact) >= 3 and len(w) >= 4:
            # (full-word compact matching)
            # exact compact match
            if w == compact:
                return ModerationResult(False, "profanity", w)
            if len(w) == len(compact) + 1:
                for i in range(1, len(w)):
                    if w[:i] + w[i + 1:] == compact:
                        return ModerationResult(False, "profanity", w)
            if len(w) == len(compact) + 2 and len(w) >= 5:
                for i in range(1, len(w) - 1):
                    for j in range(i + 1, len(w)):
                        if w[:i] + w[i + 1:j] + w[j + 1:] == compact:
                            return ModerationResult(False, "profanity", w)
            # substitution/collapse censor ("бл*ть" ~ "блядь"): fuzzy match on
            # curated dictionary words with a shared 2-char prefix
            if len(compact) >= 3 and w[:2] == compact[:2]:
                from difflib import SequenceMatcher
                if SequenceMatcher(None, w, compact).ratio() >= 0.6:
                    return ModerationResult(False, "profanity", w)
        # short censored words ("فـ*ـک" ~ "فک"): compare against short dict words
        # sharing the first character with fuzzy ratio >= 0.5
        if len(compact) == 2:
            from difflib import SequenceMatcher
            for sw in ("کس", "کص", "کیر", "کون"):
                if sw[0] == compact[0] and SequenceMatcher(None, sw, compact).ratio() >= 0.5:
                    return ModerationResult(False, "profanity", sw)
    return ModerationResult(True)


# ------------------------------------------------------- contact filter ----
URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
TG_LINK_RE = re.compile(r"\b(?:t\.me|telegram\.me|ble\.ir)/\S+", re.IGNORECASE)
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.\w{2,}\b", re.IGNORECASE)
HANDLE_RE = re.compile(r"(?<![\w.])@[A-Za-z0-9_]{4,}")
PHONE_RE = re.compile(r"\+?\d[\d\s\-()]{8,16}\d")
PHONE_STRICT_RE = re.compile(r"^(?:\+?98|0)?9\d{9}$")
IG_RE = re.compile(r"\b(?:instagram|insta|اینستاگرام|اینستا)\s*[:\-]?\s*[A-Za-z0-9._]{3,}", re.IGNORECASE)


def _norm_phone(candidate: str) -> str:
    return re.sub(r"[^\d+]", "", candidate)


def check_contact(text: str, config: dict) -> ModerationResult:
    if config.get("blockUrls", True):
        if URL_RE.search(text) or TG_LINK_RE.search(text):
            return ModerationResult(False, "url", "url")
    if EMAIL_RE.search(text):
        return ModerationResult(False, "email", "email")
    if config.get("blockPhoneNumbers", True):
        for m in PHONE_RE.finditer(text):
            if PHONE_STRICT_RE.match(_norm_phone(m.group(0))):
                return ModerationResult(False, "phone", "phone")
    if config.get("blockTelegramHandles", True) or config.get("blockInstagramIds", True):
        if HANDLE_RE.search(text):
            return ModerationResult(False, "handle", "handle")
    if config.get("blockInstagramIds", True) and IG_RE.search(text):
        return ModerationResult(False, "instagram", "instagram")
    return ModerationResult(True)


def sanitize_snippet(text: str, limit: int = 60) -> str:
    snippet = " ".join((text or "").split())
    return snippet[:limit] + ("…" if len(snippet) > limit else "")
