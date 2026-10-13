import re
import unicodedata


def normalize(text):
    text = unicodedata.normalize("NFC", text).replace("\u00a0", " ")
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()).strip()


def legal_units(text):
    """Preserve original offsets; split at article, clause and point boundaries."""
    pattern = re.compile(r"(?m)^[ \t]*(?:Điều\s+\d+[a-zđ]?\b[^\n]*|\d+\.\s|[a-zđ]\)\s)", re.I)
    matches = list(pattern.finditer(text))
    starts = sorted({0, *(m.start() for m in matches)})
    article, clause = "", ""
    result = []
    for i, start in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(text)
        raw = text[start:end]
        if not raw.strip():
            continue
        first = raw.splitlines()[0].strip()
        if re.match(r"Điều\s+\d", first, re.I):
            article, clause = first[:180], ""
            locator = article
        elif re.match(r"\d+\.", first):
            clause = first.split(".", 1)[0]
            locator = f"{article} / khoản {clause}"
        elif re.match(r"[a-zđ]\)", first, re.I):
            locator = f"{article} / khoản {clause} / điểm {first[0]}"
        else:
            locator = "Phần mở đầu"
        result.append({"locator": locator, "raw_text": raw, "start_offset": start, "end_offset": end})
    return result
