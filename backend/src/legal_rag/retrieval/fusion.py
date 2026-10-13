def rrf(*rankings, k=60):
    scores, candidates = {}, {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            key = str(item["id"])
            scores[key] = scores.get(key, 0) + 1 / (k + rank)
            candidates[key] = item
    return [
        dict(candidates[key], rrf_score=scores[key]) for key in sorted(scores, key=lambda x: (-scores[x], x))
    ]
