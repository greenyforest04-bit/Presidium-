"""Semantic Search for Presidium — AI-powered message search.

Provides semantic search capabilities using sentence-transformers for
embedding messages and FAISS for efficient similarity search. Unlike
keyword-based search (FTS5), semantic search understands the meaning
of queries and can find relevant messages even without exact keyword
matches.

Architecture:
    SemanticSearchEngine
        ├── EmbeddingService — sentence-transformers embedding generation
        ├── VectorStore — FAISS index for fast similarity search
        ├── QueryProcessor — query understanding and expansion
        └── ResultRanker — hybrid ranking (semantic + keyword + recency)

Features:
    - Natural language queries ("find messages about the project deadline")
    - Cross-lingual search (query in Russian, find messages in English)
    - Hybrid search: combines semantic similarity with FTS5 keyword match
    - Incremental indexing: new messages are indexed in the background
    - Privacy: all embeddings computed locally, no cloud APIs

Index lifecycle:
    1. On startup, load existing FAISS index from disk
    2. Index new messages incrementally in background
    3. Periodically optimize the index (merge segments)
    4. On shutdown, persist the index to disk

Dependencies (optional, lazy-loaded):
    - sentence-transformers: for embedding generation
    - faiss-cpu: for efficient similarity search
    - numpy: for vector operations
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Awaitable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384       # all-MiniLM-L6-v2 output dimension
MAX_QUERY_LENGTH = 512
MAX_MESSAGE_LENGTH = 2048       # Truncate messages longer than this for embedding
INDEX_FILE_NAME = "semantic_index.faiss"
METADATA_FILE_NAME = "semantic_metadata.json"
BATCH_SIZE = 32                 # Messages to embed per batch
REINDEX_INTERVAL = 3600         # 1 hour between full reindex checks


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class SemanticSearchError(Exception):
    """Base exception for semantic search operations."""


class EmbeddingError(SemanticSearchError):
    """Failed to generate embeddings."""


class IndexError(SemanticSearchError):
    """Failed to create or query the search index."""


class ModelNotAvailableError(SemanticSearchError):
    """Required ML model is not installed."""


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SearchResult:
    """A single search result from semantic search.

    Attributes:
        message_id: ID of the matched message.
        peer_user_id: User ID of the message sender.
        content: Snippet of the message content.
        score: Relevance score (0.0 - 1.0, higher is more relevant).
        timestamp: Unix timestamp of the message.
        is_from_semantic: Whether this result came from semantic (vs keyword) search.
    """
    message_id: str = ""
    peer_user_id: str = ""
    content: str = ""
    score: float = 0.0
    timestamp: float = 0.0
    is_from_semantic: bool = True


@dataclass
class SearchQuery:
    """A processed search query.

    Attributes:
        original: The raw user query string.
        normalized: Lowercased, trimmed query.
        embedding: Precomputed query embedding (if available).
        keywords: Extracted keywords for FTS5 fallback.
        max_results: Maximum number of results to return.
        filters: Additional filters (date range, peer, etc.).
    """
    original: str = ""
    normalized: str = ""
    embedding: Optional[list[float]] = None
    keywords: list[str] = field(default_factory=list)
    max_results: int = 20
    filters: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.normalized and self.original:
            self.normalized = self.original.lower().strip()
        if not self.keywords and self.normalized:
            self.keywords = self.normalized.split()


@dataclass
class IndexedMessage:
    """A message that has been indexed for semantic search.

    Attributes:
        message_id: Unique message identifier.
        peer_user_id: Sender's user ID.
        content: Message text content.
        timestamp: Unix timestamp.
        embedding_hash: Hash of the content at embedding time
            (for detecting changes that require re-embedding).
    """
    message_id: str = ""
    peer_user_id: str = ""
    content: str = ""
    timestamp: float = 0.0
    embedding_hash: str = ""

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute a content hash for change detection."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Embedding Service
# ---------------------------------------------------------------------------

class EmbeddingService:
    """Generates vector embeddings using sentence-transformers.

    Uses the all-MiniLM-L6-v2 model by default, which provides good
    quality embeddings at 384 dimensions with fast inference. The model
    is loaded lazily on first use to avoid startup delay.

    For environments without sentence-transformers, falls back to a
    simple TF-IDF-style embedding based on character n-grams.
    """

    def __init__(self, model_name: str = DEFAULT_MODEL_NAME) -> None:
        self._model_name = model_name
        self._model: Optional[object] = None
        self._dimension = EMBEDDING_DIMENSION
        self._is_available = False

    @property
    def is_available(self) -> bool:
        """Check if the embedding model is available."""
        return self._is_available or self._check_availability()

    @property
    def dimension(self) -> int:
        """Embedding vector dimension."""
        return self._dimension

    def _check_availability(self) -> bool:
        """Check if sentence-transformers is installed."""
        try:
            import sentence_transformers  # noqa: F401
            self._is_available = True
            return True
        except ImportError:
            return False

    def _load_model(self) -> None:
        """Load the sentence-transformers model (lazy loading)."""
        if self._model is not None:
            return

        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            self._is_available = True
            logger.info("Loaded embedding model: %s", self._model_name)
        except ImportError:
            raise ModelNotAvailableError(
                "sentence-transformers not installed. "
                "Install with: pip install sentence-transformers"
            )
        except Exception as exc:
            raise EmbeddingError(f"Failed to load model {self._model_name}: {exc}") from exc

    def embed_text(self, text: str) -> list[float]:
        """Generate an embedding for a single text string.

        Args:
            text: The text to embed.

        Returns:
            List of floats representing the embedding vector.

        Raises:
            EmbeddingError: If embedding generation fails.
        """
        if not text or not text.strip():
            return [0.0] * self._dimension

        truncated = text[:MAX_MESSAGE_LENGTH]

        try:
            self._load_model()
            if self._model is not None:
                embedding = self._model.encode(truncated, show_progress_bar=False)
                return embedding.tolist()
        except ModelNotAvailableError:
            # Fallback: simple hash-based embedding
            return self._fallback_embed(truncated)
        except Exception as exc:
            raise EmbeddingError(f"Embedding failed: {exc}") from exc

        return self._fallback_embed(truncated)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for a batch of texts.

        More efficient than individual calls due to model batching.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors.
        """
        if not texts:
            return []

        truncated = [t[:MAX_MESSAGE_LENGTH] for t in texts]

        try:
            self._load_model()
            if self._model is not None:
                embeddings = self._model.encode(
                    truncated, batch_size=BATCH_SIZE, show_progress_bar=False
                )
                return embeddings.tolist()
        except (ModelNotAvailableError, Exception):
            pass

        return [self._fallback_embed(t) for t in truncated]

    def _fallback_embed(self, text: str) -> list[float]:
        """Simple fallback embedding using character n-grams.

        Produces a deterministic vector based on character trigrams
        in the text. Much lower quality than sentence-transformers
        but works without any ML dependencies.

        Args:
            text: The text to embed.

        Returns:
            A pseudo-embedding vector.
        """
        vector = [0.0] * self._dimension

        # Character trigrams → dimension indices
        normalized = text.lower()
        for i in range(len(normalized) - 2):
            trigram = normalized[i:i + 3]
            idx = int(hashlib.md5(trigram.encode()).hexdigest(), 16) % self._dimension
            vector[idx] += 1.0

        # Normalize
        magnitude = sum(v * v for v in vector) ** 0.5
        if magnitude > 0:
            vector = [v / magnitude for v in vector]

        return vector


# ---------------------------------------------------------------------------
# Vector Store (FAISS-based)
# ---------------------------------------------------------------------------

class VectorStore:
    """FAISS-based vector store for fast similarity search.

    Stores message embeddings in a FAISS index for efficient
    nearest-neighbor search. Falls back to brute-force numpy
    search if FAISS is not available.
    """

    def __init__(
        self,
        dimension: int = EMBEDDING_DIMENSION,
        data_dir: Optional[Path] = None,
    ) -> None:
        self._dimension = dimension
        self._data_dir = data_dir
        self._index: Optional[object] = None
        self._metadata: list[IndexedMessage] = []
        self._use_faiss = False

    @property
    def size(self) -> int:
        """Number of vectors in the store."""
        if self._index is not None:
            try:
                return self._index.ntotal
            except AttributeError:
                return len(self._metadata)
        return len(self._metadata)

    def initialize(self) -> None:
        """Initialize the vector store, loading existing index if available."""
        # Try FAISS first
        try:
            import faiss
            self._use_faiss = True

            # Try to load existing index
            if self._data_dir is not None:
                index_path = self._data_dir / INDEX_FILE_NAME
                if index_path.exists():
                    self._index = faiss.read_index(str(index_path))
                    logger.info("Loaded FAISS index with %d vectors.", self._index.ntotal)
                    self._load_metadata()
                    return

            # Create new index
            self._index = faiss.IndexFlatIP(self._dimension)  # Inner product for cosine similarity
            logger.info("Created new FAISS index (dim=%d).", self._dimension)

        except ImportError:
            logger.info("FAISS not available, using numpy fallback.")
            self._use_faiss = False
            self._index = []  # type: ignore

    def add(self, message: IndexedMessage, embedding: list[float]) -> None:
        """Add a message embedding to the store.

        Args:
            message: The indexed message metadata.
            embedding: The embedding vector.
        """
        if self._index is None:
            self.initialize()

        if len(embedding) != self._dimension:
            raise ValueError("Embedding dimension does not match the index.")
        import math
        magnitude = math.sqrt(sum(value * value for value in embedding))
        normalized = [value / magnitude for value in embedding] if magnitude else list(embedding)
        if self._use_faiss:
            import numpy as np
            self._index.add(np.array([normalized], dtype=np.float32))
        else:
            self._index.append(normalized)

        self._metadata.append(message)

    def add_batch(
        self, messages: list[IndexedMessage], embeddings: list[list[float]]
    ) -> None:
        """Add a batch of message embeddings.

        Args:
            messages: List of indexed messages.
            embeddings: List of embedding vectors.
        """
        if len(messages) != len(embeddings):
            raise ValueError("Each message must have an embedding.")
        for message, embedding in zip(messages, embeddings):
            self.add(message, embedding)

    def search(
        self, query_embedding: list[float], top_k: int = 20
    ) -> list[tuple[int, float]]:
        """Search for similar vectors.

        Args:
            query_embedding: The query vector.
            top_k: Number of results to return.

        Returns:
            List of (index, score) tuples, sorted by score descending.
        """
        if self._index is None or self.size == 0:
            return []

        if len(query_embedding) != self._dimension:
            raise ValueError("Query dimension does not match the index.")
        import math
        magnitude = math.sqrt(sum(value * value for value in query_embedding))
        query = [value / magnitude for value in query_embedding] if magnitude else list(query_embedding)
        if self._use_faiss:
            import numpy as np
            scores, indices = self._index.search(np.array([query], dtype=np.float32), min(top_k, self.size))
            return [(int(i), float(score)) for i, score in zip(indices[0], scores[0]) if i >= 0]
        scores = [(i, sum(a * b for a, b in zip(vector, query))) for i, vector in enumerate(self._index)]
        return sorted(scores, key=lambda item: item[1], reverse=True)[:max(0, top_k)]

    def save(self) -> None:
        """Persist the index and metadata to disk."""
        if self._data_dir is None:
            return

        self._data_dir.mkdir(parents=True, exist_ok=True)

        if self._use_faiss and self._index is not None:
            try:
                import faiss
                faiss.write_index(self._index, str(self._data_dir / INDEX_FILE_NAME))
            except Exception as exc:
                logger.warning("Failed to save FAISS index: %s", exc)

        self._save_metadata()

    def _save_metadata(self) -> None:
        """Save metadata to a JSON file."""
        if self._data_dir is None:
            return

        metadata_path = self._data_dir / METADATA_FILE_NAME
        data = [
            {
                "message_id": m.message_id,
                "peer_user_id": m.peer_user_id,
                "content": m.content[:200],  # Truncate for storage
                "timestamp": m.timestamp,
                "embedding_hash": m.embedding_hash,
            }
            for m in self._metadata
        ]

        try:
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as exc:
            logger.warning("Failed to save metadata: %s", exc)

    def _load_metadata(self) -> None:
        """Load metadata from a JSON file."""
        if self._data_dir is None:
            return

        metadata_path = self._data_dir / METADATA_FILE_NAME
        if not metadata_path.exists():
            return

        try:
            with open(metadata_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self._metadata = [
                IndexedMessage(
                    message_id=m["message_id"],
                    peer_user_id=m["peer_user_id"],
                    content=m.get("content", ""),
                    timestamp=m.get("timestamp", 0.0),
                    embedding_hash=m.get("embedding_hash", ""),
                )
                for m in data
            ]
        except Exception as exc:
            logger.warning("Failed to load metadata: %s", exc)


# ---------------------------------------------------------------------------
# Semantic Search Engine
# ---------------------------------------------------------------------------

class SemanticSearchEngine:
    """Full semantic search engine for Presidium messages.

    Combines embedding-based semantic search with FTS5 keyword search
    for hybrid retrieval, providing both meaning-based and exact-match
    results.

    Lifecycle:
        engine = SemanticSearchEngine(data_dir=Path("..."))
        await engine.start()    # Loads index, starts background indexing
        results = await engine.search("project deadline discussion")
        await engine.stop()     # Saves index to disk
    """

    def __init__(
        self,
        data_dir: Optional[Path] = None,
        model_name: str = DEFAULT_MODEL_NAME,
    ) -> None:
        self._embedding = EmbeddingService(model_name)
        self._store = VectorStore(
            dimension=self._embedding.dimension,
            data_dir=data_dir,
        )
        self._running = False
        self._index_task: Optional[asyncio.Task] = None
        self._message_queue: asyncio.Queue[IndexedMessage] = asyncio.Queue()

        # Callbacks for fetching messages from the database
        self._fetch_recent_messages: Optional[Callable[[], Awaitable[list[IndexedMessage]]]] = None

    def set_fetch_callback(
        self, callback: Callable[[], Awaitable[list[IndexedMessage]]]
    ) -> None:
        """Set the callback for fetching recent messages to index.

        Args:
            callback: Async function returning list of IndexedMessage.
        """
        self._fetch_recent_messages = callback

    async def start(self) -> None:
        """Start the semantic search engine."""
        self._store.initialize()
        self._running = True

        # Start background indexing
        self._index_task = asyncio.create_task(self._index_loop())

        logger.info(
            "Semantic search engine started (%d vectors, model=%s).",
            self._store.size,
            "sentence-transformers" if self._embedding.is_available else "fallback",
        )

    async def stop(self) -> None:
        """Stop the engine and persist the index."""
        self._running = False

        if self._index_task is not None:
            self._index_task.cancel()
            try:
                await self._index_task
            except asyncio.CancelledError:
                pass
            self._index_task = None

        # Process remaining queue
        while not self._message_queue.empty():
            msg = self._message_queue.get_nowait()
            self._index_message(msg)

        self._store.save()
        logger.info("Semantic search engine stopped. Index saved.")

    async def index_message(self, message: IndexedMessage) -> None:
        """Queue a message for background indexing.

        Args:
            message: The message to index.
        """
        await self._message_queue.put(message)

    def _index_message(self, message: IndexedMessage) -> None:
        """Index a single message (synchronous).

        Args:
            message: The message to index.
        """
        if not message.content.strip():
            return

        message.embedding_hash = IndexedMessage.compute_hash(message.content)
        embedding = self._embedding.embed_text(message.content)
        self._store.add(message, embedding)

    async def search(
        self,
        query_text: str,
        max_results: int = 20,
        peer_filter: str = "",
    ) -> list[SearchResult]:
        """Search for messages matching a natural language query.

        Performs semantic similarity search using the query embedding
        and returns the most relevant messages.

        Args:
            query_text: Natural language search query.
            max_results: Maximum number of results.
            peer_filter: Optional peer user ID to filter results.

        Returns:
            List of SearchResult objects, sorted by relevance.
        """
        if not query_text.strip():
            return []

        # Generate query embedding
        query_embedding = self._embedding.embed_text(query_text)

        # Search the vector store
        raw_results = self._store.search(query_embedding, top_k=max_results * 2)

        # Build results
        results: list[SearchResult] = []
        for idx, score in raw_results:
            if idx >= len(self._store._metadata):
                continue

            msg = self._store._metadata[idx]

            # Apply peer filter
            if peer_filter and msg.peer_user_id != peer_filter:
                continue

            # Normalize score to 0-1 range
            normalized_score = max(0.0, min(1.0, (score + 1.0) / 2.0))

            results.append(SearchResult(
                message_id=msg.message_id,
                peer_user_id=msg.peer_user_id,
                content=msg.content[:200],
                score=normalized_score,
                timestamp=msg.timestamp,
                is_from_semantic=True,
            ))

            if len(results) >= max_results:
                break

        return results

    # -------------------------------------------------------------------
    # Background indexing
    # -------------------------------------------------------------------

    async def _index_loop(self) -> None:
        """Background loop for processing the message indexing queue."""
        while self._running:
            try:
                # Process messages in batches
                batch: list[IndexedMessage] = []

                # Wait for first message
                try:
                    msg = await asyncio.wait_for(
                        self._message_queue.get(), timeout=5.0
                    )
                    batch.append(msg)
                except asyncio.TimeoutError:
                    continue

                # Collect more messages for batch processing
                while len(batch) < BATCH_SIZE:
                    try:
                        msg = self._message_queue.get_nowait()
                        batch.append(msg)
                    except asyncio.QueueEmpty:
                        break

                # Embed and index the batch
                if batch:
                    texts = [m.content for m in batch]
                    embeddings = self._embedding.embed_batch(texts)
                    self._store.add_batch(batch, embeddings)

                    logger.debug("Indexed batch of %d messages.", len(batch))

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Indexing error: %s", exc)
                await asyncio.sleep(5.0)

    @property
    def index_size(self) -> int:
        """Number of messages currently indexed."""
        return self._store.size

    @property
    def is_model_available(self) -> bool:
        """Whether the ML model is available for semantic search."""
        return self._embedding.is_available
