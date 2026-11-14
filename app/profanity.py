"""Profanity word lists (v2.2) — kept in a dedicated file so the vocabulary is
easy to audit and extend without touching the filter logic.

Three supported languages (fa, en, ru) plus the Finglish transliteration set.
Only clearly vulgar vocabulary lives here; ordinary/mild words must NOT be
listed so normal display names are never rejected.
"""
from __future__ import annotations

# ------------------------------------------------- Persian (fa) ----
FA_WORDS = [
    "کس", "کص", "کون", "کیر", "جنده", "حرومزاده", "حرامزاده",
    "ناموس", "عوضی", "هرزه", "فاحشه", "شلوق", "لختی", "سکسی", "سیکسی",
    "پستون", "خایه", "کونده", "کیرم", "کصکش", "کسکش", "ممه", "فک",
]
# Finglish (romanized Persian) transliterations
FA_FINGLISH = [
    "kos", "koon", "kir", "jendeh", "jende", "kooni", "koskesh", "kirie",
    "haramzade", "namoos", "avazi", "faheshe", "sholough", "khalaf",
]

# -------------------------------------------------- English (en) ----
EN_WORDS = [
    "fuck", "fucking", "bitch", "asshole", "cunt", "motherfucker", "whore",
    "slut", "dickhead", "faggot", "nigger", "retard", "bastard", "pussy",
]

# ------------------------------------------------- Russian (ru) ----
RU_WORDS = [
    "бля", "блядь", "хуй", "хуе", "пизда", "пизде", "ебать", "еба", "сука",
    "гандон", "мудак", "шлюха", "пидор", "заебал", "херня",
]

# Phrases that must never be flagged (false-positive protection for names)
DEFAULT_ALLOWLIST = ["kun", "kirsi", "suka"]

# Dictionary tuples consumed by the moderation engine: (word, severity, action)
DEFAULT_DICTIONARY = [
    (w, "high", "block_and_warn") for w in FA_WORDS + EN_WORDS + RU_WORDS
] + [(w, "medium", "block_and_warn") for w in FA_FINGLISH]
