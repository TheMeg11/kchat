"""Literal, multilingual admin search (not the aggressive profanity normalizer)."""
import re
import unicodedata
from sqlalchemy import func

TRANSLATION = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩يك", "01234567890123456789یک")


def normalize_query(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).translate(TRANSLATION)
    value = re.sub(r"[\u064b-\u065f\u0670]", "", value)
    return " ".join(value.replace("\u200c", " ").split()).strip().lstrip("@").lower()


def normalized_column(column):
    expr = func.lower(func.coalesce(column, ""))
    for a, b in (("ي", "ی"), ("ك", "ک"), ("\u200c", " ")):
        expr = func.replace(expr, a, b)
    return expr


def contains_literal(column, query):
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return normalized_column(column).like(f"%{escaped}%", escape="\\")
