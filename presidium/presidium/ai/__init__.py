"""On-Device AI Assistant for Presidium.

Provides local AI capabilities powered by llama.cpp (via llama-cpp-python)
for privacy-preserving smart features:
    - Smart replies: 3 context-aware reply suggestions
    - Summarization: Summarize last N messages in group chats
    - Translation: On-device translation (opus-mt via transformers)
    - Draft enhancement: Improve/polish message drafts

All AI inference runs locally — no data leaves the device. Models are
downloaded once and cached in the user's data directory.

Model requirements:
    - Smart replies / summarization: quantized LLaMA-3-8B-GGUF (4-bit, ~5GB)
    - Translation: opus-mt models (~300MB per language pair)
    - Draft enhancement: same LLM as smart replies

Performance targets:
    - Smart replies: < 2 seconds for 3 suggestions
    - Summarization: < 5 seconds for 50 messages
    - Translation: < 1 second per message
    - RAM usage: < 8GB peak (LLM loaded)

Fallback behavior:
    If llama-cpp-python is not installed or no model is downloaded,
    the AI features are gracefully disabled with a UI prompt to
    download the model.
"""

from presidium.ai.assistant import AIAssistant

__all__ = ["AIAssistant"]
