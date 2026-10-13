import re
from datetime import date
from difflib import unified_diff

PIPELINE_VERSION = "legal-rag-v1"


OFFICIAL_HOSTS = {"vanban.chinhphu.vn", "congbao.chinhphu.vn", "vbpl.vn"}


NUMBER = re.compile(r"\b\d{1,4}/\d{4}/[\wĐđ-]+", re.UNICODE)


DATE = re.compile(r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4})\b")


VI_DATE = re.compile(r"ngày\s+(\d{1,2})\s+tháng\s+(\d{1,2})\s+năm\s+(\d{4})", re.I)


def date_strings(text):
    return DATE.findall(text) + [f"{day}/{month}/{year}" for day, month, year in VI_DATE.findall(text)]


def parse_date(value):
    if not value:
        return None
    if isinstance(value, date):
        return value
    value = str(value).strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return date.fromisoformat(value)
    match = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})[/-](\d{4})", value)
    if match:
        day, month, year = map(int, match.groups())
        return date(year, month, day)
    raise ValueError("unrecognized date")


def temporal_state(version, as_of):
    if version.get("temporal_status") == "conflict":
        return "conflict"
    start, end = version.get("effective_from"), version.get("effective_to")
    if start and as_of < start:
        return "future"
    if end and as_of >= end:
        return "expired"
    if version.get("temporal_status") == "partially_expired":
        return "partial"
    if start and end:
        return "in_interval"
    # An open-ended interval does not prove present-day legal validity.
    return "unknown"


def compare_units(left, right):
    left_map = {u["locator"]: u["raw_text"] for u in left}
    right_map = {u["locator"]: u["raw_text"] for u in right}
    rows = []
    for locator in dict.fromkeys([*left_map, *right_map]):
        before, after = left_map.get(locator, ""), right_map.get(locator, "")
        if before != after:
            rows.append(
                {
                    "locator": locator,
                    "before": before,
                    "after": after,
                    "diff": "\n".join(unified_diff(before.splitlines(), after.splitlines(), lineterm="")),
                }
            )
    return rows
