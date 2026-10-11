"""Measure recall quality (nDCG@10, hit@5, MRR) per query split.

--queries points to JSON shaped as {"<split>": [{"q": str, "gold": [key, ...]}]}.
A key is a page stem (decisions/foo.md -> "foo") or the project name for log.md entries.
The queries file is supplied by the user and is not versioned in this repo.
Run with MEMORY_BOOST_HOME pointing at the wiki to evaluate.
"""
import argparse
import json
import math
from pathlib import Path

from memory_boost import core


def recall_keys(query: str, historic: bool) -> list[str]:
    keys: list[str] = []
    for r in core.recall(query, limit=10, include_historic=historic):
        key = r["project"] if r["kind"] == "log" else Path(r["path"]).stem
        if key not in keys:
            keys.append(key)
    return keys[:10]


def score(keys: list[str], gold: list[str]) -> dict[str, float]:
    gold_set = set(gold)
    rank = next((i + 1 for i, k in enumerate(keys) if k in gold_set), None)
    dcg = sum(1 / math.log2(i + 2) for i, k in enumerate(keys) if k in gold_set)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(gold_set), 10)))
    return {
        "ndcg": dcg / idcg if idcg else 0.0,
        "hit5": float(rank is not None and rank <= 5),
        "mrr": 1 / rank if rank else 0.0,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--queries", type=Path, required=True, help="JSON file with query splits")
    ap.add_argument("--historic", action="store_true", help="include historic pages")
    args = ap.parse_args()
    splits = json.loads(args.queries.read_text(encoding="utf-8"))
    for split, items in splits.items():
        scores = [score(recall_keys(it["q"], args.historic), it["gold"]) for it in items]
        n = len(scores) or 1
        mean = {m: sum(s[m] for s in scores) / n for m in ("ndcg", "hit5", "mrr")}
        print(f"{split}: nDCG@10 {mean['ndcg']:.3f}  hit@5 {mean['hit5']:.3f}  MRR {mean['mrr']:.3f}")


if __name__ == "__main__":
    main()
