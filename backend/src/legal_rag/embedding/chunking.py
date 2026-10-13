import re

from legal_rag.domain.text import normalize


def chunk_unit(raw, title, locator, count, target=384, limit=512):
    """Count the entire E5 input. Preserve every character without silent truncation."""
    header = f"passage: {title}\n{locator}\n"
    if count(header) >= target:
        raise ValueError("metadata exceeds chunk budget")
    chunks = []
    offset = 0
    while offset < len(raw):
        remaining = raw[offset:]
        if count(header + normalize(remaining)) <= target:
            end = len(raw)
        else:
            lo, hi = 1, len(remaining)
            while lo < hi:
                mid = (lo + hi + 1) // 2
                if count(header + normalize(remaining[:mid])) <= target:
                    lo = mid
                else:
                    hi = mid - 1
            cut = lo
            # Prefer sentence or whitespace boundaries, while preserving all text.
            candidates = list(re.finditer(r"(?:[.!?;]\s+|\n|\s+)", remaining[:cut]))
            if candidates and candidates[-1].end() > cut // 2:
                cut = candidates[-1].end()
            end = offset + max(1, cut)
        piece = raw[offset:end]
        input_text = header + normalize(piece)
        tokens = count(input_text)
        if tokens > limit:
            raise ValueError("chunk exceeds E5 token budget")
        chunks.append(
            {
                "raw_text": piece,
                "normalized_text": normalize(piece),
                "embedding_text": input_text,
                "token_count": tokens,
            }
        )
        offset = end
    return chunks
