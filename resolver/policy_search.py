"""Search over the AP policy document.

This is the retrieval half of agentic RAG. The agent decides WHEN to call it (it is just a tool).
Retrieval uses BM25 keyword ranking, which needs no embedding model and runs offline.
For a large policy library you would swap this for Azure AI Search (hybrid search) behind the same tool.
"""

import re
from functools import lru_cache

from rank_bm25 import BM25Okapi

from . import config

STOPWORDS = set("a an the is are was be of to in on for and or not no by with as at it this that from when".split())


def tokenize(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS]


@lru_cache(maxsize=1)
def load_sections() -> list[dict]:
    """Split every policy file into sections that start with '### <ID> <Title>'."""
    sections = []
    for path in sorted(config.POLICY_DIR.glob("*.txt")):
        text = path.read_text(encoding="utf-8")
        for match in re.finditer(r"^### (\S+) (.+?)\n(.*?)(?=^### |\Z)", text, flags=re.M | re.S):
            section_id, title, body = match.group(1), match.group(2).strip(), match.group(3).strip()
            sections.append({"section": section_id, "title": title, "text": body, "source": path.name})
    return sections


@lru_cache(maxsize=1)
def _index() -> BM25Okapi:
    return BM25Okapi([tokenize(s["title"] + " " + s["text"]) for s in load_sections()])


def search(query: str, top_k: int = 3) -> list[dict]:
    sections = load_sections()
    if not sections:
        return []
    scores = _index().get_scores(tokenize(query))
    ranked = sorted(zip(scores, sections), key=lambda pair: pair[0], reverse=True)
    return [{**section, "score": round(float(score), 3)} for score, section in ranked[:top_k] if score > 0]
