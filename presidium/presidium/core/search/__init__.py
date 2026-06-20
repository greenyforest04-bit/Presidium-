"""Search subsystem for Presidium.

Provides both keyword-based (FTS5) and semantic (AI-powered) search:
    - SemanticSearchEngine: sentence-transformers + FAISS for meaning-based search
    - SmartFolderManager: automatic message categorization with filter rules
"""

from presidium.core.search.semantic import SemanticSearchEngine
from presidium.core.search.smart_folders import SmartFolderManager

__all__ = [
    "SemanticSearchEngine",
    "SmartFolderManager",
]
