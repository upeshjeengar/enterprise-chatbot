"""Policy RAG: chunk markdown policies, embed with NVIDIA nv-embedqa, store in a
numpy vector file, retrieve by cosine similarity. Lightweight stand-in for Qdrant
that needs zero external services."""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

from . import config
from .llm_gateway import embed


@dataclass
class Chunk:
    policy_name: str
    section: str
    text: str


def _split_sections(md: str) -> list[tuple[str, str]]:
    """Split a policy markdown into (section_label, text) pairs on `## Section` headers."""
    lines = md.splitlines()
    out: list[tuple[str, str]] = []
    cur_label = "Preamble"
    cur_buf: list[str] = []
    for ln in lines:
        m = re.match(r"^##\s+(.*)", ln)
        if m:
            if cur_buf:
                out.append((cur_label, "\n".join(cur_buf).strip()))
            cur_label = m.group(1).strip()
            cur_buf = []
        else:
            cur_buf.append(ln)
    if cur_buf:
        out.append((cur_label, "\n".join(cur_buf).strip()))
    # title from first # heading
    title_m = re.search(r"^#\s+(.*)", md, re.MULTILINE)
    title = title_m.group(1).strip() if title_m else "Policy"
    return [(f"{title} — {label}", text) for label, text in out if text]


def build_chunks() -> list[Chunk]:
    chunks: list[Chunk] = []
    for path in sorted(config.POLICY_DIR.glob("*.md")):
        md = path.read_text(encoding="utf-8")
        for section, text in _split_sections(md):
            # keep chunks reasonably sized
            chunks.append(Chunk(policy_name=path.stem, section=section, text=text))
    return chunks


def ingest() -> int:
    """Embed all policy chunks and persist vectors. Returns chunk count."""
    chunks = build_chunks()
    texts = [f"{c.section}\n{c.text}" for c in chunks]
    vectors: list[list[float]] = []
    # batch to be gentle on the API
    B = 32
    for i in range(0, len(texts), B):
        vectors.extend(embed(texts[i : i + B], input_type="passage"))
    mat = np.array(vectors, dtype=np.float32)
    # normalize for cosine via dot product
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms
    np.savez(
        config.VECTOR_PATH,
        vectors=mat,
        meta=np.array([list(asdict(c).values()) for c in chunks], dtype=object),
    )
    return len(chunks)


class PolicyStore:
    """Loaded vector store for retrieval."""

    def __init__(self) -> None:
        self.vectors: np.ndarray | None = None
        self.chunks: list[Chunk] = []
        self._load()

    def _load(self) -> None:
        if not Path(config.VECTOR_PATH).exists():
            return
        data = np.load(config.VECTOR_PATH, allow_pickle=True)
        self.vectors = data["vectors"]
        self.chunks = [
            Chunk(policy_name=row[0], section=row[1], text=row[2])
            for row in data["meta"]
        ]

    @property
    def ready(self) -> bool:
        return self.vectors is not None and len(self.chunks) > 0

    def retrieve(self, query: str, k: int = 5) -> list[dict]:
        if not self.ready:
            return []
        qv = np.array(embed([query], input_type="query")[0], dtype=np.float32)
        qn = np.linalg.norm(qv) or 1.0
        qv = qv / qn
        sims = self.vectors @ qv  # cosine (both normalized)
        idx = np.argsort(-sims)[:k]
        results = []
        for i in idx:
            c = self.chunks[int(i)]
            results.append(
                {
                    "policy_name": c.policy_name,
                    "section": c.section,
                    "text": c.text,
                    "relevance_score": round(float(sims[int(i)]), 4),
                }
            )
        return results


_store: PolicyStore | None = None


def get_store() -> PolicyStore:
    global _store
    if _store is None:
        _store = PolicyStore()
    return _store


def reload_store() -> None:
    global _store
    _store = PolicyStore()
