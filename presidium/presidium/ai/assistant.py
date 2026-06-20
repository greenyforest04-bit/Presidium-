"""On-Device AI Assistant implementation for Presidium.

Core module that interfaces with local LLM (llama-cpp-python) and
translation models (transformers/opus-mt) to provide AI features
without any cloud dependency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LLM_MODEL = "llama-3-8b-instruct.Q4_K_M.gguf"
DEFAULT_LLM_URL = "https://huggingface.co/TheBloke/Llama-3-8B-GGUF/resolve/main/"
LLM_CONTEXT_SIZE = 4096
LLM_MAX_TOKENS_SMART_REPLY = 128
LLM_MAX_TOKENS_SUMMARY = 512
LLM_MAX_TOKENS_ENHANCE = 256
LLM_TEMPERATURE_SMART_REPLY = 0.7
LLM_TEMPERATURE_SUMMARY = 0.3
LLM_TEMPERATURE_ENHANCE = 0.5

# Translation models
TRANSLATION_MODEL_PREFIX = "Helsinki-NLP/opus-mt-"
SUPPORTED_LANGUAGE_PAIRS = {
    "en-ru", "ru-en", "en-de", "de-en", "en-fr", "fr-en",
    "en-es", "es-en", "en-zh", "zh-en", "en-ja", "ja-en",
    "en-ko", "ko-en", "en-ar", "ar-en",
}


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class AIError(Exception):
    """Base exception for AI assistant operations."""


class ModelNotLoadedError(AIError):
    """LLM model is not loaded."""


class ModelNotAvailableError(AIError):
    """LLM library or model file not available."""


class TranslationError(AIError):
    """Translation failed."""


# ---------------------------------------------------------------------------
# AI feature types
# ---------------------------------------------------------------------------

class AIFeature:
    """AI feature identifiers."""
    SMART_REPLIES = "smart_replies"
    SUMMARIZATION = "summarization"
    TRANSLATION = "translation"
    DRAFT_ENHANCEMENT = "draft_enhancement"


@dataclass
class SmartReply:
    """A single smart reply suggestion."""
    text: str = ""
    confidence: float = 0.0  # 0.0-1.0 (heuristic)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "confidence": self.confidence}


@dataclass
class MessageSummary:
    """Summary of a group of messages."""
    text: str = ""
    message_count: int = 0
    key_points: list[str] = field(default_factory=list)
    participants_mentioned: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "message_count": self.message_count,
            "key_points": self.key_points,
            "participants_mentioned": self.participants_mentioned,
        }


@dataclass
class TranslationResult:
    """Result of a translation."""
    original_text: str = ""
    translated_text: str = ""
    source_lang: str = ""
    target_lang: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "original_text": self.original_text,
            "translated_text": self.translated_text,
            "source_lang": self.source_lang,
            "target_lang": self.target_lang,
        }


@dataclass
class DraftEnhancement:
    """Result of draft enhancement."""
    original: str = ""
    improved: str = ""
    style: str = "polite"  # polite, concise, formal, casual

    def to_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "improved": self.improved,
            "style": self.style,
        }


# ---------------------------------------------------------------------------
# LLM Engine abstraction
# ---------------------------------------------------------------------------

class LLMEngine:
    """Abstraction over the local LLM inference engine.

    Supports llama-cpp-python as the primary backend. Provides a
    unified interface for text generation with configurable parameters.
    """

    def __init__(self) -> None:
        self._model: Optional[Any] = None
        self._model_path: Optional[Path] = None
        self._is_loaded = False
        self._library_available = False

        # Check if llama-cpp-python is available
        try:
            import llama_cpp
            self._library_available = True
        except ImportError:
            self._library_available = False

    @property
    def is_available(self) -> bool:
        """Check if the LLM library is available."""
        return self._library_available

    @property
    def is_loaded(self) -> bool:
        """Check if a model is loaded."""
        return self._is_loaded and self._model is not None

    async def load_model(self, model_path: Path) -> bool:
        """Load a GGUF model from disk.

        Args:
            model_path: Path to the .gguf model file.

        Returns:
            True if the model was loaded successfully.
        """
        if not self._library_available:
            logger.warning("llama-cpp-python not installed. AI features disabled.")
            return False

        if not model_path.exists():
            logger.error("Model file not found: %s", model_path)
            return False

        try:
            from llama_cpp import Llama

            # Load in executor to avoid blocking
            loop = asyncio.get_event_loop()
            self._model = await loop.run_in_executor(
                None,
                lambda: Llama(
                    model_path=str(model_path),
                    n_ctx=LLM_CONTEXT_SIZE,
                    n_gpu_layers=0,  # CPU only for broad compatibility
                    verbose=False,
                ),
            )
            self._model_path = model_path
            self._is_loaded = True

            logger.info("LLM model loaded: %s", model_path.name)
            return True

        except Exception as exc:
            logger.error("Failed to load LLM model: %s", exc)
            self._is_loaded = False
            return False

    async def generate(
        self,
        prompt: str,
        max_tokens: int = 256,
        temperature: float = 0.7,
        stop: Optional[list[str]] = None,
    ) -> str:
        """Generate text from a prompt.

        Args:
            prompt: Input prompt.
            max_tokens: Maximum tokens to generate.
            temperature: Sampling temperature.
            stop: Stop sequences.

        Returns:
            Generated text.

        Raises:
            ModelNotLoadedError: If no model is loaded.
        """
        if not self.is_loaded or self._model is None:
            raise ModelNotLoadedError("No LLM model loaded.")

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: self._model(
                prompt,
                max_tokens=max_tokens,
                temperature=temperature,
                stop=stop or ["</s>", "\n\n\n"],
            ),
        )

        # Extract generated text
        choices = result.get("choices", [])
        if choices:
            return choices[0].get("text", "").strip()
        return ""


# ---------------------------------------------------------------------------
# Translation engine
# ---------------------------------------------------------------------------

class TranslationEngine:
    """Local translation using opus-mt models via transformers."""

    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._models: dict[str, Any] = {}  # lang_pair → model
        self._tokenizers: dict[str, Any] = {}

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> TranslationResult:
        """Translate text between languages.

        Args:
            text: Text to translate.
            source_lang: Source language code (e.g. "en").
            target_lang: Target language code (e.g. "ru").

        Returns:
            TranslationResult with the translated text.

        Raises:
            TranslationError: If translation fails.
        """
        lang_pair = f"{source_lang}-{target_lang}"

        if lang_pair not in SUPPORTED_LANGUAGE_PAIRS:
            raise TranslationError(
                f"Unsupported language pair: {lang_pair}. "
                f"Supported: {', '.join(sorted(SUPPORTED_LANGUAGE_PAIRS))}"
            )

        # Load model if not cached
        if lang_pair not in self._models:
            loaded = await self._load_model(lang_pair)
            if not loaded:
                raise TranslationError(
                    f"Failed to load translation model for {lang_pair}. "
                    "Install transformers: pip install transformers sentencepiece"
                )

        model = self._models[lang_pair]
        tokenizer = self._tokenizers[lang_pair]

        try:
            loop = asyncio.get_event_loop()
            translated = await loop.run_in_executor(
                None,
                lambda: model.generate(**tokenizer(text, return_tensors="pt", padding=True))
            )

            # Decode output
            result_text = tokenizer.decode(translated[0], skip_special_tokens=True)

            return TranslationResult(
                original_text=text,
                translated_text=result_text,
                source_lang=source_lang,
                target_lang=target_lang,
            )

        except Exception as exc:
            raise TranslationError(f"Translation failed: {exc}") from exc

    async def _load_model(self, lang_pair: str) -> bool:
        """Load a translation model for a language pair."""
        try:
            from transformers import AutoTokenizer, AutoModelForSeq2SeqLM

            model_name = f"{TRANSLATION_MODEL_PREFIX}{lang_pair}"

            loop = asyncio.get_event_loop()
            tokenizer = await loop.run_in_executor(
                None,
                lambda: AutoTokenizer.from_pretrained(model_name, cache_dir=str(self._cache_dir)),
            )
            model = await loop.run_in_executor(
                None,
                lambda: AutoModelForSeq2SeqLM.from_pretrained(
                    model_name, cache_dir=str(self._cache_dir)
                ),
            )

            self._models[lang_pair] = model
            self._tokenizers[lang_pair] = tokenizer

            logger.info("Translation model loaded: %s", lang_pair)
            return True

        except ImportError:
            logger.warning("transformers not installed. Translation disabled.")
            return False
        except Exception as exc:
            logger.error("Failed to load translation model %s: %s", lang_pair, exc)
            return False


# ---------------------------------------------------------------------------
# AI Assistant (main interface)
# ---------------------------------------------------------------------------

class AIAssistant:
    """On-device AI assistant for Presidium.

    Provides local AI features without any cloud dependency.
    All inference runs on the user's device.

    Features:
        - Smart replies: 3 context-aware reply suggestions
        - Summarization: Summarize recent messages
        - Translation: On-device language translation
        - Draft enhancement: Improve message drafts

    Usage:
        ai = AIAssistant(data_dir=Path("~/.presidium/ai"))
        await ai.initialize()
        replies = await ai.get_smart_replies(
            recent_messages=[...],
            my_user_id="alice",
        )
    """

    def __init__(self, data_dir: Path) -> None:
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._llm = LLMEngine()
        self._translation = TranslationEngine(cache_dir=data_dir / "models")
        self._is_initialized = False
        self._features_enabled: dict[str, bool] = {
            AIFeature.SMART_REPLIES: True,
            AIFeature.SUMMARIZATION: True,
            AIFeature.TRANSLATION: True,
            AIFeature.DRAFT_ENHANCEMENT: True,
        }

    # -------------------------------------------------------------------
    # Initialization
    # -------------------------------------------------------------------

    async def initialize(self) -> bool:
        """Initialize the AI assistant.

        Attempts to load the LLM model. If the model is not found,
        the assistant starts in degraded mode with only translation
        (if transformers is available).

        Returns:
            True if the LLM was loaded successfully.
        """
        # Try to load LLM
        model_path = self._data_dir / "models" / DEFAULT_LLM_MODEL
        loaded = await self._llm.load_model(model_path)

        if loaded:
            self._is_initialized = True
            logger.info("AI assistant initialized with LLM")
        else:
            logger.info(
                "AI assistant initialized in degraded mode "
                "(LLM not available). Download model to enable smart features."
            )
            self._is_initialized = True  # Translation may still work
            self._features_enabled[AIFeature.SMART_REPLIES] = False
            self._features_enabled[AIFeature.SUMMARIZATION] = False
            self._features_enabled[AIFeature.DRAFT_ENHANCEMENT] = False

        return loaded

    @property
    def is_available(self) -> bool:
        """Check if any AI features are available."""
        return self._is_initialized and any(self._features_enabled.values())

    def is_feature_available(self, feature: str) -> bool:
        """Check if a specific feature is available."""
        return self._features_enabled.get(feature, False)

    # -------------------------------------------------------------------
    # Smart Replies
    # -------------------------------------------------------------------

    async def get_smart_replies(
        self,
        recent_messages: list[dict[str, str]],
        my_user_id: str = "",
    ) -> list[SmartReply]:
        """Generate 3 smart reply suggestions.

        Args:
            recent_messages: List of recent messages with 'sender' and 'text' keys.
            my_user_id: Our user ID (to skip our own messages).

        Returns:
            List of up to 3 SmartReply suggestions.
        """
        if not self.is_feature_available(AIFeature.SMART_REPLIES):
            return []

        # Build context from recent messages
        context = self._build_chat_context(recent_messages, my_user_id)

        # Craft prompt for smart replies
        prompt = (
            f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n"
            f"You are a helpful chat assistant. Based on the conversation below, "
            f"suggest 3 short, natural reply options. Each reply should be on a "
            f"new line starting with '-'. Keep replies concise (1-2 sentences).\n\n"
            f"<|start_header_id|>user<|end_header_id|>\n"
            f"Conversation:\n{context}\n\n"
            f"Suggested replies:\n-"
        )

        try:
            response = await self._llm.generate(
                prompt=prompt,
                max_tokens=LLM_MAX_TOKENS_SMART_REPLY,
                temperature=LLM_TEMPERATURE_SMART_REPLY,
                stop=["<|end_header_id|>", "\n\n"],
            )

            # Parse response into individual replies
            replies = []
            for line in response.split("\n"):
                line = line.strip().lstrip("- ").strip()
                if line and len(line) > 2:
                    replies.append(SmartReply(
                        text=line,
                        confidence=0.8 - len(replies) * 0.2,  # Decreasing confidence
                    ))

            return replies[:3]  # Max 3 suggestions

        except Exception as exc:
            logger.error("Smart reply generation failed: %s", exc)
            return []

    # -------------------------------------------------------------------
    # Summarization
    # -------------------------------------------------------------------

    async def summarize_messages(
        self,
        messages: list[dict[str, str]],
        my_user_id: str = "",
    ) -> Optional[MessageSummary]:
        """Summarize a list of messages.

        Args:
            messages: Messages to summarize with 'sender' and 'text' keys.
            my_user_id: Our user ID.

        Returns:
            MessageSummary or None if summarization fails.
        """
        if not self.is_feature_available(AIFeature.SUMMARIZATION):
            return None

        context = self._build_chat_context(messages, my_user_id)

        prompt = (
            f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n"
            f"Summarize the following chat conversation. Include:\n"
            f"1. A concise summary paragraph\n"
            f"2. 3-5 key points (each starting with '•')\n"
            f"3. List of active participants\n\n"
            f"<|start_header_id|>user<|end_header_id|>\n"
            f"Conversation:\n{context}\n\n"
            f"Summary:\n"
        )

        try:
            response = await self._llm.generate(
                prompt=prompt,
                max_tokens=LLM_MAX_TOKENS_SUMMARY,
                temperature=LLM_TEMPERATURE_SUMMARY,
            )

            # Parse the response
            key_points = []
            participants = set()
            summary_text = response

            for line in response.split("\n"):
                line = line.strip()
                if line.startswith("•"):
                    key_points.append(line.lstrip("• ").strip())
                # Try to extract participant names

            # Extract participants from original messages
            for msg in messages:
                sender = msg.get("sender", "")
                if sender and sender != my_user_id:
                    participants.add(sender)

            return MessageSummary(
                text=summary_text,
                message_count=len(messages),
                key_points=key_points[:5],
                participants_mentioned=list(participants),
            )

        except Exception as exc:
            logger.error("Summarization failed: %s", exc)
            return None

    # -------------------------------------------------------------------
    # Translation
    # -------------------------------------------------------------------

    async def translate(
        self,
        text: str,
        source_lang: str,
        target_lang: str,
    ) -> Optional[TranslationResult]:
        """Translate text between languages.

        Args:
            text: Text to translate.
            source_lang: Source language code (e.g. "en").
            target_lang: Target language code (e.g. "ru").

        Returns:
            TranslationResult or None if translation fails.
        """
        if not self.is_feature_available(AIFeature.TRANSLATION):
            return None

        try:
            return await self._translation.translate(text, source_lang, target_lang)
        except TranslationError as exc:
            logger.error("Translation failed: %s", exc)
            return None

    # -------------------------------------------------------------------
    # Draft Enhancement
    # -------------------------------------------------------------------

    async def enhance_draft(
        self,
        draft_text: str,
        style: str = "polite",
    ) -> Optional[DraftEnhancement]:
        """Improve a message draft.

        Args:
            draft_text: The original draft text.
            style: Enhancement style (polite, concise, formal, casual).

        Returns:
            DraftEnhancement or None if enhancement fails.
        """
        if not self.is_feature_available(AIFeature.DRAFT_ENHANCEMENT):
            return None

        style_descriptions = {
            "polite": "more polite and considerate",
            "concise": "shorter and more concise",
            "formal": "more formal and professional",
            "casual": "more casual and friendly",
        }

        description = style_descriptions.get(style, "more polite and considerate")

        prompt = (
            f"<|begin_of_text|><|start_header_id|>system<|end_header_id|>\n"
            f"You are a writing assistant. Rewrite the following message to be "
            f"{description}. Keep the core meaning. Output ONLY the improved "
            f"message, nothing else.\n\n"
            f"<|start_header_id|>user<|end_header_id|>\n"
            f"Original: {draft_text}\n\n"
            f"Improved:\n"
        )

        try:
            response = await self._llm.generate(
                prompt=prompt,
                max_tokens=LLM_MAX_TOKENS_ENHANCE,
                temperature=LLM_TEMPERATURE_ENHANCE,
            )

            return DraftEnhancement(
                original=draft_text,
                improved=response.strip(),
                style=style,
            )

        except Exception as exc:
            logger.error("Draft enhancement failed: %s", exc)
            return None

    # -------------------------------------------------------------------
    # Internal helpers
    # -------------------------------------------------------------------

    def _build_chat_context(
        self,
        messages: list[dict[str, str]],
        my_user_id: str = "",
        max_messages: int = 20,
    ) -> str:
        """Build a formatted chat context for the LLM prompt."""
        recent = messages[-max_messages:]
        lines = []
        for msg in recent:
            sender = msg.get("sender", "Unknown")
            if sender == my_user_id:
                sender = "Me"
            text = msg.get("text", "")
            lines.append(f"{sender}: {text}")
        return "\n".join(lines)

    # -------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------

    def get_stats(self) -> dict[str, Any]:
        """Return AI assistant statistics."""
        return {
            "initialized": self._is_initialized,
            "llm_loaded": self._llm.is_loaded,
            "llm_available": self._llm.is_available,
            "features": dict(self._features_enabled),
        }

    async def download_model(self, progress_callback: Optional[Callable[[float], None]] = None) -> bool:
        """Download the default LLM model.

        Args:
            progress_callback: Optional callback with download progress (0.0-1.0).

        Returns:
            True if download succeeded.
        """
        model_dir = self._data_dir / "models"
        model_dir.mkdir(parents=True, exist_ok=True)
        model_path = model_dir / DEFAULT_LLM_MODEL

        if model_path.exists():
            logger.info("Model already downloaded: %s", model_path)
            return True

        try:
            import aiohttp
            url = DEFAULT_LLM_URL + DEFAULT_LLM_MODEL

            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.error("Model download failed: HTTP %d", resp.status)
                        return False

                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0

                    with open(model_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            f.write(chunk)
                            downloaded += len(chunk)
                            if progress_callback and total > 0:
                                progress_callback(downloaded / total)

            logger.info("Model downloaded: %s", model_path)
            return True

        except Exception as exc:
            logger.error("Model download failed: %s", exc)
            return False
