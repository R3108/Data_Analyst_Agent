"""Analysis memory: what has this question already been answered with, and where?

Two problems grow with every week a team uses an analyst tool. The same question gets
asked four times and answered four slightly different ways. And by the third dataset,
nobody is sure which file a question should even be pointed at.

Both are retrieval problems, and both are solved here without an embedding model:
TF-IDF over the questions, headlines and column vocabulary already stored in SQLite,
scored by cosine similarity. That keeps recall free, instant, offline and — importantly
for a feature that decides what the agent is told — inspectable: every match reports the
terms it actually matched on.

* **Recall** — before a question is planned, the closest prior answers on the same
  dataset are found and handed to the agent as continuity context, and to the reader as
  "you asked something like this on 3 March". The agent is told explicitly that those
  figures are historical, because the verifier will flag any number it repeats that the
  current run did not compute.
* **Routing** — given a question and several datasets, rank which table can actually
  answer it, by matching the question's terms against each dataset's name, column names
  and category values.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from typing import Any, Iterable

logger = logging.getLogger(__name__)

WORD = re.compile(r"[A-Za-z][A-Za-z0-9_]*")
# Splits snake_case and camelCase column names into the words a question would use.
CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")

STOPWORDS = frozenset("""
a about all also an and any are as at be been being between both but by can could did do does
for from get give had has have how i if in into is it its just like make me more most much my no
not of on one only or other our out over please same see should show so some such than that the
their them then there these they this those to too us use used very was we were what when where
which while who why will with would you your which whats
""".split())

# A question with fewer real terms than this cannot be matched responsibly.
MIN_TERMS = 2
# Below this, two questions merely share common words.
MIN_SIMILARITY = 0.18
# Above this they are the same question in different words.
DUPLICATE_SIMILARITY = 0.62
MAX_CANDIDATES = 400


def tokenize(text: str | None) -> list[str]:
    """Words a business question and a column name have in common.

    `net_revenue` and "net revenue" must produce the same terms, or matching a question
    against a schema never works.
    """
    if not text:
        return []
    expanded = CAMEL.sub(" ", str(text).replace("_", " ").replace("-", " "))
    terms = [w.lower() for w in WORD.findall(expanded)]
    return [t for t in terms if len(t) > 2 and t not in STOPWORDS]


def _vector(terms: Iterable[str], idf: dict[str, float]) -> dict[str, float]:
    counts = Counter(terms)
    if not counts:
        return {}
    most_common = counts.most_common(1)[0][1]
    # Augmented term frequency: a word repeated ten times is not ten times the evidence.
    return {
        term: (0.5 + 0.5 * count / most_common) * idf.get(term, 0.0)
        for term, count in counts.items()
    }


def _cosine(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    shared = set(a) & set(b)
    if not shared:
        return 0.0
    dot = sum(a[t] * b[t] for t in shared)
    norm = math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values()))
    return dot / norm if norm else 0.0


def _idf(documents: list[list[str]]) -> dict[str, float]:
    """Smoothed inverse document frequency, so "revenue" in a revenue dataset is cheap."""
    n = len(documents)
    seen: Counter[str] = Counter()
    for terms in documents:
        seen.update(set(terms))
    return {term: math.log((n + 1) / (count + 1)) + 1.0 for term, count in seen.items()}


# ----------------------------------------------------------------------------- recall


def recall(
    entries: list[dict[str, Any]],
    question: str,
    *,
    limit: int = 3,
    min_similarity: float = MIN_SIMILARITY,
    exclude_message_id: str | None = None,
) -> list[dict[str, Any]]:
    """The prior analyses closest to `question`, best first.

    `entries` are the stored question/answer pairs — see `Database.recent_analyses`.
    """
    terms = tokenize(question)
    if len(terms) < MIN_TERMS or not entries:
        return []

    candidates = entries[:MAX_CANDIDATES]
    documents = [tokenize(f"{e.get('question') or ''} {e.get('headline') or ''}") for e in candidates]
    idf = _idf(documents + [terms])
    query = _vector(terms, idf)

    scored: list[dict[str, Any]] = []
    for entry, document in zip(candidates, documents):
        if exclude_message_id and entry.get("message_id") == exclude_message_id:
            continue
        similarity = _cosine(query, _vector(document, idf))
        if similarity < min_similarity:
            continue
        overlap = sorted(set(terms) & set(document), key=lambda t: -idf.get(t, 0.0))
        scored.append({
            **entry,
            "similarity": round(similarity, 4),
            "matched_terms": overlap[:6],
            "duplicate": similarity >= DUPLICATE_SIMILARITY,
        })
    scored.sort(key=lambda e: (-e["similarity"], e.get("created_at") or ""))
    return scored[:limit]


def recall_block(matches: list[dict[str, Any]]) -> str:
    """The prompt block handed to the planner.

    It says plainly that these numbers are historical. The agent is allowed to use them
    for continuity — consistent metric choices, "as previously reported" framing — and
    not as a source of current figures, which the verifier would flag anyway.
    """
    if not matches:
        return ""
    lines = [
        "PREVIOUSLY ANSWERED ON THIS DATASET (historical context, NOT current figures):",
        "Use these only to stay consistent with how the question was framed and which metrics "
        "were used before. Every number in your answer must come from this run's computed output.",
        "",
    ]
    for match in matches:
        when = (match.get("created_at") or "")[:10]
        lines.append(f'- [{when}] Asked: "{match["question"]}"')
        if match.get("headline"):
            lines.append(f"  Answered: {match['headline']}")
        if match.get("kpis"):
            summary = "; ".join(f"{k['label']}={k['value']}" for k in match["kpis"][:4])
            lines.append(f"  KPIs then: {summary}")
    return "\n".join(lines)


def summarize(matches: list[dict[str, Any]]) -> dict[str, Any] | None:
    """What the UI shows above the answer. None when nothing is close enough to mention."""
    if not matches:
        return None
    best = matches[0]
    return {
        "matches": matches,
        "duplicate": bool(best["duplicate"]),
        "headline": (
            f"You asked this before, on {(best.get('created_at') or '')[:10]}."
            if best["duplicate"]
            else f"{len(matches)} related analysis{'es' if len(matches) > 1 else ''} already exist."
        ),
    }


# ----------------------------------------------------------------------------- routing


def route(datasets: list[dict[str, Any]], question: str, *, limit: int = 3) -> list[dict[str, Any]]:
    """Rank datasets by how well their vocabulary answers `question`.

    Each dataset is represented by its name, its column names and the category values it
    actually holds — so "how many orders shipped late in EMEA" finds the table with a
    `region` column containing `EMEA`, not merely the one called "Orders".
    """
    terms = tokenize(question)
    if len(terms) < MIN_TERMS or not datasets:
        return []

    documents = [vocabulary(d) for d in datasets]
    idf = _idf(documents + [terms])
    query = _vector(terms, idf)

    ranked: list[dict[str, Any]] = []
    for dataset, document in zip(datasets, documents):
        similarity = _cosine(query, _vector(document, idf))
        matched = sorted(set(terms) & set(document), key=lambda t: -idf.get(t, 0.0))
        ranked.append({
            "dataset_id": dataset.get("id"),
            "name": dataset.get("name"),
            "n_rows": dataset.get("n_rows"),
            "score": round(similarity, 4),
            "matched_terms": matched[:8],
            "unmatched_terms": sorted(set(terms) - set(document))[:8],
        })
    ranked.sort(key=lambda d: -d["score"])
    top = ranked[:limit]
    if top:
        runner_up = top[1]["score"] if len(top) > 1 else 0.0
        # "Confident" means the winner is clearly ahead, not merely first.
        top[0]["confident"] = bool(top[0]["score"] >= 0.25 and top[0]["score"] >= runner_up * 1.5)
    return top


def vocabulary(dataset: dict[str, Any]) -> list[str]:
    """Every word a question could legitimately use to refer to this dataset."""
    profile = dataset.get("profile") or {}
    terms = tokenize(dataset.get("name"))
    for column in profile.get("columns") or []:
        terms += tokenize(column.get("name"))
        for value in column.get("top_values") or []:
            terms += tokenize(str(value.get("value")))
    semantics = dataset.get("semantics") or {}
    for metric in semantics.get("metrics") or []:
        terms += tokenize(metric.get("name")) + tokenize(metric.get("definition"))
    for term in semantics.get("glossary") or []:
        terms += tokenize(term.get("term"))
    return terms


__all__ = [
    "recall",
    "recall_block",
    "route",
    "summarize",
    "tokenize",
    "vocabulary",
    "DUPLICATE_SIMILARITY",
    "MIN_SIMILARITY",
]
