"""Smart Folders for Presidium — automatic message categorization.

Smart Folders automatically organize chats and messages into virtual
folders based on configurable rules. Unlike regular folders, Smart
Folders are dynamically populated based on criteria such as:

    - Message content (semantic or keyword match)
    - Contact identity (peer user ID, group membership)
    - Time period (today, this week, this month)
    - Read status (unread, mentions)
    - Message type (files, voice, links)
    - Custom tags

Architecture:
    SmartFolderManager
        ├── SmartFolder — folder definition with filter rules
        ├── RuleEngine — evaluates messages against folder rules
        ├── FolderView — virtual view of messages matching rules
        └── TagManager — user-defined tags for messages

Usage:
    manager = SmartFolderManager()

    # Built-in smart folders
    manager.create_builtin_folders()

    # Custom folder
    folder = manager.create_folder(
        name="Work Projects",
        rules=[
            KeywordRule("project"),
            PeerRule(peer_ids=["abc123", "def456"]),
        ],
    )

    # Get messages in a folder
    messages = await manager.get_folder_messages("Work Projects")
"""

from __future__ import annotations

import enum
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class SmartFolderError(Exception):
    """Base exception for smart folder operations."""


class FolderNotFoundError(SmartFolderError):
    """Requested folder does not exist."""


class RuleEvaluationError(SmartFolderError):
    """Failed to evaluate a folder rule."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class RuleType(enum.Enum):
    """Types of filter rules for smart folders."""
    KEYWORD = "keyword"
    PEER = "peer"
    GROUP = "group"
    TIME_PERIOD = "time_period"
    READ_STATUS = "read_status"
    MESSAGE_TYPE = "message_type"
    TAG = "tag"
    SEMANTIC = "semantic"


class TimePeriod(enum.Enum):
    """Time period for time-based rules."""
    TODAY = "today"
    YESTERDAY = "yesterday"
    THIS_WEEK = "this_week"
    THIS_MONTH = "this_month"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"


class MessageContentType(enum.Enum):
    """Type of message content."""
    TEXT = "text"
    FILE = "file"
    VOICE = "voice"
    IMAGE = "image"
    LINK = "link"
    LOCATION = "location"
    POLL = "poll"


class FolderIcon(enum.Enum):
    """Built-in folder icons."""
    INBOX = "inbox"
    UNREAD = "unread"
    GROUPS = "groups"
    WORK = "work"
    PERSONAL = "personal"
    ARCHIVE = "archive"
    PINNED = "pinned"
    STARRED = "starred"
    CUSTOM = "custom"


# ---------------------------------------------------------------------------
# Filter Rules
# ---------------------------------------------------------------------------

@dataclass
class FolderRule:
    """Base class for smart folder filter rules.

    All rules implement the matches() method to check if a message
    or chat session satisfies the rule criteria.
    """
    rule_type: RuleType = RuleType.KEYWORD
    negated: bool = False  # If True, the rule matches when the condition is NOT met

    def matches(self, context: dict) -> bool:
        """Check if the context matches this rule.

        Args:
            context: Dictionary with message/chat context data:
                - content: str — message text
                - peer_user_id: str — sender user ID
                - group_id: str — group chat ID (empty for DMs)
                - timestamp: float — Unix timestamp
                - is_read: bool — read status
                - content_type: str — message content type
                - tags: list[str] — user tags
                - has_mention: bool — whether the user is @mentioned

        Returns:
            True if the rule matches.
        """
        result = self._evaluate(context)
        return not result if self.negated else result

    def _evaluate(self, context: dict) -> bool:
        """Evaluate the rule against the context. Override in subclasses."""
        return False


@dataclass
class KeywordRule(FolderRule):
    """Matches messages containing specific keywords.

    Attributes:
        keywords: List of keywords to match (case-insensitive).
        match_all: If True, ALL keywords must be present (AND logic).
                   If False, ANY keyword is sufficient (OR logic).
    """
    rule_type: RuleType = RuleType.KEYWORD
    keywords: list[str] = field(default_factory=list)
    match_all: bool = False

    def _evaluate(self, context: dict) -> bool:
        content = context.get("content", "").lower()
        if not content:
            return False

        if self.match_all:
            return all(kw.lower() in content for kw in self.keywords)
        return any(kw.lower() in content for kw in self.keywords)


@dataclass
class PeerRule(FolderRule):
    """Matches messages from specific contacts.

    Attributes:
        peer_ids: List of peer user IDs to match.
    """
    rule_type: RuleType = RuleType.PEER
    peer_ids: list[str] = field(default_factory=list)

    def _evaluate(self, context: dict) -> bool:
        peer_id = context.get("peer_user_id", "")
        return peer_id in self.peer_ids


@dataclass
class GroupRule(FolderRule):
    """Matches messages from group chats.

    Attributes:
        group_ids: List of group IDs to match. Empty = any group.
    """
    rule_type: RuleType = RuleType.GROUP
    group_ids: list[str] = field(default_factory=list)

    def _evaluate(self, context: dict) -> bool:
        group_id = context.get("group_id", "")
        if not group_id:
            return False  # Not a group message
        if not self.group_ids:
            return True  # Any group matches
        return group_id in self.group_ids


@dataclass
class TimePeriodRule(FolderRule):
    """Matches messages within a specific time period.

    Attributes:
        period: The time period to match against.
    """
    rule_type: RuleType = RuleType.TIME_PERIOD
    period: TimePeriod = TimePeriod.TODAY

    def _evaluate(self, context: dict) -> bool:
        timestamp = context.get("timestamp", 0.0)
        if not timestamp:
            return False

        now = time.time()
        age_seconds = now - timestamp

        period_seconds = {
            TimePeriod.TODAY: 86400,
            TimePeriod.YESTERDAY: 172800,
            TimePeriod.THIS_WEEK: 604800,
            TimePeriod.THIS_MONTH: 2592000,
            TimePeriod.LAST_7_DAYS: 604800,
            TimePeriod.LAST_30_DAYS: 2592000,
        }

        max_age = period_seconds.get(self.period, 86400)
        return age_seconds <= max_age


@dataclass
class ReadStatusRule(FolderRule):
    """Matches messages based on read status.

    Attributes:
        is_read: Whether to match read (True) or unread (False) messages.
    """
    rule_type: RuleType = RuleType.READ_STATUS
    is_read: bool = False

    def _evaluate(self, context: dict) -> bool:
        return context.get("is_read", True) == self.is_read


@dataclass
class MessageTypeRule(FolderRule):
    """Matches messages by content type.

    Attributes:
        content_types: List of message content types to match.
    """
    rule_type: RuleType = RuleType.MESSAGE_TYPE
    content_types: list[MessageContentType] = field(default_factory=list)

    def _evaluate(self, context: dict) -> bool:
        msg_type = context.get("content_type", "text")
        return msg_type in [ct.value for ct in self.content_types]


@dataclass
class TagRule(FolderRule):
    """Matches messages with specific user-defined tags.

    Attributes:
        tags: List of tags to match.
        match_all: If True, ALL tags must be present.
    """
    rule_type: RuleType = RuleType.TAG
    tags: list[str] = field(default_factory=list)
    match_all: bool = False

    def _evaluate(self, context: dict) -> bool:
        msg_tags = set(context.get("tags", []))
        required = set(self.tags)

        if self.match_all:
            return required.issubset(msg_tags)
        return bool(required & msg_tags)


@dataclass
class SemanticRule(FolderRule):
    """Matches messages using semantic similarity.

    Attributes:
        query: Natural language description of the topic.
        threshold: Minimum similarity score (0.0 - 1.0).
    """
    rule_type: RuleType = RuleType.SEMANTIC
    query: str = ""
    threshold: float = 0.5

    def _evaluate(self, context: dict) -> bool:
        # Semantic evaluation requires the search engine
        # This is handled separately by the folder manager
        return False


# ---------------------------------------------------------------------------
# Smart Folder
# ---------------------------------------------------------------------------

@dataclass
class SmartFolder:
    """A smart folder with filter rules.

    Attributes:
        folder_id: Unique folder identifier.
        name: Display name for the folder.
        icon: Folder icon.
        rules: List of filter rules (all must match for AND logic).
        match_any: If True, ANY rule matching is sufficient (OR logic).
        is_builtin: Whether this is a built-in folder.
        is_pinned: Whether the folder is pinned to the sidebar.
        created_at: Unix timestamp of creation.
        last_updated: Unix timestamp of last content change.
    """
    folder_id: str = ""
    name: str = ""
    icon: FolderIcon = FolderIcon.CUSTOM
    rules: list[FolderRule] = field(default_factory=list)
    match_any: bool = False
    is_builtin: bool = False
    is_pinned: bool = False
    created_at: float = 0.0
    last_updated: float = 0.0

    def matches(self, context: dict) -> bool:
        """Check if the context matches all (or any) of the folder rules.

        Args:
            context: Message/chat context dictionary.

        Returns:
            True if the folder's rules match.
        """
        if not self.rules:
            return False

        if self.match_any:
            return any(rule.matches(context) for rule in self.rules)
        return all(rule.matches(context) for rule in self.rules)


# ---------------------------------------------------------------------------
# Smart Folder Manager
# ---------------------------------------------------------------------------

class SmartFolderManager:
    """Manages smart folders for Presidium.

    Provides folder creation, rule evaluation, and message categorization.
    Includes built-in folders for common use cases (unread, groups, etc.)
    and supports custom folders with compound filter rules.
    """

    def __init__(self) -> None:
        self._folders: dict[str, SmartFolder] = {}
        self._message_fetch_callback: Optional[Callable[[str], Awaitable[list[dict]]]] = None

    def set_message_fetch_callback(
        self, callback: Callable[[str], Awaitable[list[dict]]]
    ) -> None:
        """Set the callback for fetching messages from the database.

        Args:
            callback: Async function(folder_id) → list of message context dicts.
        """
        self._message_fetch_callback = callback

    # -------------------------------------------------------------------
    # Folder CRUD
    # -------------------------------------------------------------------

    def create_folder(
        self,
        name: str,
        rules: list[FolderRule],
        icon: FolderIcon = FolderIcon.CUSTOM,
        match_any: bool = False,
    ) -> SmartFolder:
        """Create a new smart folder.

        Args:
            name: Display name.
            rules: Filter rules.
            icon: Folder icon.
            match_any: If True, any rule matching is sufficient.

        Returns:
            The created SmartFolder.
        """
        import os
        folder_id = os.urandom(8).hex()

        folder = SmartFolder(
            folder_id=folder_id,
            name=name,
            icon=icon,
            rules=rules,
            match_any=match_any,
            created_at=time.time(),
            last_updated=time.time(),
        )
        self._folders[folder_id] = folder

        logger.info("Created smart folder '%s' with %d rules.", name, len(rules))
        return folder

    def delete_folder(self, folder_id: str) -> None:
        """Delete a smart folder.

        Args:
            folder_id: ID of the folder to delete.

        Raises:
            FolderNotFoundError: If the folder doesn't exist.
        """
        folder = self._folders.get(folder_id)
        if folder is None:
            raise FolderNotFoundError(f"Folder '{folder_id}' not found.")

        if folder.is_builtin:
            raise SmartFolderError("Cannot delete built-in folders.")

        del self._folders[folder_id]
        logger.info("Deleted smart folder '%s'.", folder.name)

    def get_folder(self, folder_id: str) -> Optional[SmartFolder]:
        """Get a folder by ID."""
        return self._folders.get(folder_id)

    def get_all_folders(self) -> list[SmartFolder]:
        """Get all folders, sorted with pinned first."""
        folders = sorted(
            self._folders.values(),
            key=lambda f: (not f.is_pinned, f.name),
        )
        return folders

    def rename_folder(self, folder_id: str, new_name: str) -> None:
        """Rename a smart folder."""
        folder = self._folders.get(folder_id)
        if folder is None:
            raise FolderNotFoundError(f"Folder '{folder_id}' not found.")
        folder.name = new_name

    def pin_folder(self, folder_id: str, pinned: bool = True) -> None:
        """Pin or unpin a folder to the sidebar."""
        folder = self._folders.get(folder_id)
        if folder is not None:
            folder.is_pinned = pinned

    # -------------------------------------------------------------------
    # Built-in folders
    # -------------------------------------------------------------------

    def create_builtin_folders(self) -> None:
        """Create the built-in smart folders.

        Built-in folders:
            - Unread: all unread messages
            - Groups: all group chat messages
            - Mentions: messages that @mention the user
            - Files: messages with file attachments
            - Today: messages from today
        """
        builtins = [
            SmartFolder(
                folder_id="builtin_unread",
                name="Unread",
                icon=FolderIcon.UNREAD,
                rules=[ReadStatusRule(is_read=False)],
                is_builtin=True,
                is_pinned=True,
                created_at=time.time(),
            ),
            SmartFolder(
                folder_id="builtin_groups",
                name="Groups",
                icon=FolderIcon.GROUPS,
                rules=[GroupRule()],
                is_builtin=True,
                is_pinned=True,
                created_at=time.time(),
            ),
            SmartFolder(
                folder_id="builtin_mentions",
                name="Mentions",
                icon=FolderIcon.STARRED,
                rules=[KeywordRule(keywords=["@"])],
                is_builtin=True,
                created_at=time.time(),
            ),
            SmartFolder(
                folder_id="builtin_files",
                name="Files",
                icon=FolderIcon.ARCHIVE,
                rules=[MessageTypeRule(content_types=[MessageContentType.FILE])],
                is_builtin=True,
                created_at=time.time(),
            ),
            SmartFolder(
                folder_id="builtin_today",
                name="Today",
                icon=FolderIcon.INBOX,
                rules=[TimePeriodRule(period=TimePeriod.TODAY)],
                is_builtin=True,
                created_at=time.time(),
            ),
        ]

        for folder in builtins:
            self._folders[folder.folder_id] = folder

        logger.info("Created %d built-in smart folders.", len(builtins))

    # -------------------------------------------------------------------
    # Message categorization
    # -------------------------------------------------------------------

    def categorize_message(self, context: dict) -> list[str]:
        """Find all folders that a message belongs to.

        Args:
            context: Message context dictionary.

        Returns:
            List of folder IDs that the message matches.
        """
        matching_folders: list[str] = []
        for folder_id, folder in self._folders.items():
            if folder.matches(context):
                matching_folders.append(folder_id)
        return matching_folders

    def get_matching_folders(self, context: dict) -> list[SmartFolder]:
        """Get all folders that match the given context.

        Args:
            context: Message/chat context dictionary.

        Returns:
            List of matching SmartFolder objects.
        """
        return [
            folder for folder in self._folders.values()
            if folder.matches(context)
        ]

    async def get_folder_messages(self, folder_id: str) -> list[dict]:
        """Get all messages that belong to a smart folder.

        Uses the message fetch callback to retrieve candidate messages
        and filters them through the folder's rules.

        Args:
            folder_id: ID of the folder to query.

        Returns:
            List of message context dictionaries that match the folder.

        Raises:
            FolderNotFoundError: If the folder doesn't exist.
        """
        folder = self._folders.get(folder_id)
        if folder is None:
            raise FolderNotFoundError(f"Folder '{folder_id}' not found.")

        if self._message_fetch_callback is None:
            return []

        # Fetch candidate messages from the database
        all_messages = await self._message_fetch_callback(folder_id)

        # Filter through folder rules
        return [
            msg for msg in all_messages
            if folder.matches(msg)
        ]

    @property
    def folder_count(self) -> int:
        """Number of smart folders."""
        return len(self._folders)
