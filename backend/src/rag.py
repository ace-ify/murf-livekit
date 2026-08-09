import logging
import math
import os
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("rag")

KNOWLEDGE_DIR = os.getenv(
    "HEALTH_KNOWLEDGE_DIR",
    str(Path(__file__).parent.parent / "data" / "knowledge"),
)


@dataclass
class KnowledgeChunk:
    doc_title: str
    section_title: str
    content: str
    tokens: list[str]


class HealthKnowledgeBase:
    def __init__(self, knowledge_dir: str = KNOWLEDGE_DIR) -> None:
        self.knowledge_dir = Path(knowledge_dir)
        self.chunks: list[KnowledgeChunk] = []
        self.doc_freqs: Counter[str] = Counter()
        self.num_chunks: int = 0
        self.avg_chunk_len: float = 0.0
        self._load_and_index()

    def _tokenize(self, text: str) -> list[str]:
        cleaned = re.sub(r"[^\w\s-]", " ", text.lower())
        tokens = [t.strip() for t in cleaned.split() if len(t.strip()) > 1]
        return tokens

    def _load_and_index(self) -> None:
        if not self.knowledge_dir.exists():
            logger.warning("Knowledge directory %s does not exist", self.knowledge_dir)
            return

        total_tokens = 0
        for md_file in sorted(self.knowledge_dir.glob("*.md")):
            try:
                text = md_file.read_text(encoding="utf-8")
                doc_title = md_file.stem.replace("_", " ").title()

                # Split by markdown headers (# or ##)
                sections = re.split(r"\n(?=#{1,3}\s)", text)
                for section in sections:
                    lines = section.strip().splitlines()
                    if not lines:
                        continue
                    first_line = lines[0]
                    section_title = first_line.lstrip("#").strip()
                    content = (
                        "\n".join(lines[1:]).strip() if len(lines) > 1 else first_line
                    )

                    if not content:
                        content = section_title

                    tokens = self._tokenize(f"{doc_title} {section_title} {content}")
                    if not tokens:
                        continue

                    chunk = KnowledgeChunk(
                        doc_title=doc_title,
                        section_title=section_title,
                        content=content,
                        tokens=tokens,
                    )
                    self.chunks.append(chunk)
                    total_tokens += len(tokens)

                    # Update doc frequencies for unique tokens in this chunk
                    for t in set(tokens):
                        self.doc_freqs[t] += 1
            except Exception as e:
                logger.error("Failed to load knowledge doc %s: %s", md_file, e)

        self.num_chunks = len(self.chunks)
        self.avg_chunk_len = (
            total_tokens / self.num_chunks if self.num_chunks > 0 else 1.0
        )
        logger.info("Loaded and indexed %d health knowledge chunks", self.num_chunks)

    def search(self, query: str, top_k: int = 2) -> str:
        """Search knowledge base using BM25 scoring."""
        if not self.chunks or not query.strip():
            return "No health guidelines or documents available in knowledge base."

        query_tokens = self._tokenize(query)
        if not query_tokens:
            return "No matching guidelines found for the given search terms."

        k1 = 1.5
        b = 0.75
        scores: list[tuple[float, KnowledgeChunk]] = []

        for chunk in self.chunks:
            score = 0.0
            chunk_token_counts = Counter(chunk.tokens)
            doc_len = len(chunk.tokens)

            for token in query_tokens:
                if token in chunk_token_counts:
                    tf = chunk_token_counts[token]
                    df = self.doc_freqs.get(token, 0)
                    # Standard BM25 IDF
                    idf = math.log((self.num_chunks - df + 0.5) / (df + 0.5) + 1.0)
                    numerator = tf * (k1 + 1)
                    denominator = tf + k1 * (1 - b + b * (doc_len / self.avg_chunk_len))
                    score += idf * (numerator / denominator)

            # Boost if query words match the document/section title directly
            title_text = f"{chunk.doc_title} {chunk.section_title}".lower()
            for token in query_tokens:
                if token in title_text:
                    score += 2.0

            if score > 0:
                scores.append((score, chunk))

        if not scores:
            return f"No specific official guidelines found matching query: '{query}'."

        scores.sort(key=lambda x: x[0], reverse=True)
        top_results = scores[:top_k]

        formatted_passages = []
        for i, (_score, chunk) in enumerate(top_results, start=1):
            passage = (
                f"[{i}] Document: {chunk.doc_title} — {chunk.section_title}\n"
                f"{chunk.content}"
            )
            formatted_passages.append(passage)

        return "\n\n".join(formatted_passages)


# Singleton instance for quick access
_kb: HealthKnowledgeBase | None = None


def get_knowledge_base() -> HealthKnowledgeBase:
    global _kb
    if _kb is None:
        _kb = HealthKnowledgeBase()
    return _kb


def search_health_rag(query: str, top_k: int = 2) -> str:
    """Convenience search helper for tools and agents."""
    kb = get_knowledge_base()
    return kb.search(query, top_k=top_k)
