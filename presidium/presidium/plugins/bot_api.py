"""Bot API plugin for Presidium.

Provides a framework for creating chat bots that can interact with
Presidium users. Bots are special peers that respond to messages
automatically based on configured rules or AI models.

Features:
    - Command-based interaction (/command arg1 arg2)
    - Natural language processing integration points
    - Rate limiting per bot
    - Permission model (what the bot can do)
    - Inline and standalone bot modes
    - Bot registration and discovery

Bot development:
    A bot is a Python class that extends BotHandler and implements
    handle_message(). It can also define commands, menus, and
    inline query handlers.

Example bot:

    class EchoBot(BotHandler):
        name = "echo"
        description = "Echoes back your messages"

        async def handle_message(self, msg: BotMessage) -> Optional[str]:
            return f"Echo: {msg.text}"
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from presidium.plugins.base import (
    BasePlugin,
    PluginContext,
    PluginHook,
    PluginPermission,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Bot message model
# ---------------------------------------------------------------------------

@dataclass
class BotMessage:
    """A message received by a bot handler."""
    message_id: str = ""
    peer_id: str = ""        # Sender's user ID
    text: str = ""           # Full message text
    command: str = ""        # First word if it starts with /
    args: list[str] = field(default_factory=list)  # Command arguments
    is_private: bool = True  # True if 1:1, False if group
    timestamp: float = 0.0


# ---------------------------------------------------------------------------
# Bot command definition
# ---------------------------------------------------------------------------

@dataclass
class BotCommand:
    """Definition of a bot command."""
    name: str = ""          # Command name without /
    description: str = ""   # One-line description
    usage: str = ""         # Usage example
    handler: Optional[Callable[[BotMessage], Awaitable[Optional[str]]]] = None


# ---------------------------------------------------------------------------
# Abstract bot handler
# ---------------------------------------------------------------------------

class BotHandler:
    """Abstract base class for bot implementations.

    Bots extend this class and implement handle_message() to process
    incoming messages and return responses.

    Attributes:
        name: Bot identifier (lowercase, no spaces)
        description: One-line description of the bot
        commands: List of commands the bot supports
        rate_limit: Max messages per minute the bot will process
    """

    name: str = "unnamed_bot"
    description: str = ""
    commands: list[BotCommand] = []
    rate_limit: int = 60  # messages per minute

    async def handle_message(self, msg: BotMessage) -> Optional[str]:
        """Process an incoming message and return a response.

        Args:
            msg: The incoming bot message.

        Returns:
            Response text, or None to not respond.
        """
        return None

    async def on_start(self) -> None:
        """Called when the bot is started. Override for initialization."""
        pass

    async def on_stop(self) -> None:
        """Called when the bot is stopped. Override for cleanup."""
        pass


# ---------------------------------------------------------------------------
# Bot registry
# ---------------------------------------------------------------------------

class BotRegistry:
    """Manages registered bots and their configurations."""

    def __init__(self) -> None:
        self._bots: dict[str, BotHandler] = {}
        self._rate_limiters: dict[str, list[float]] = {}  # bot_name → timestamps

    def register(self, bot: BotHandler) -> None:
        """Register a bot handler."""
        self._bots[bot.name] = bot
        self._rate_limiters[bot.name] = []
        logger.info("Registered bot: %s", bot.name)

    def unregister(self, name: str) -> None:
        """Unregister a bot by name."""
        self._bots.pop(name, None)
        self._rate_limiters.pop(name, None)

    def get_bot(self, name: str) -> Optional[BotHandler]:
        """Look up a bot by name."""
        return self._bots.get(name)

    def list_bots(self) -> list[BotHandler]:
        """List all registered bots."""
        return list(self._bots.values())

    def check_rate_limit(self, bot_name: str) -> bool:
        """Check if a bot is within its rate limit.

        Returns:
            True if the request is allowed, False if rate-limited.
        """
        bot = self._bots.get(bot_name)
        if bot is None:
            return False

        now = time.time()
        timestamps = self._rate_limiters.get(bot_name, [])

        # Remove timestamps older than 60 seconds
        cutoff = now - 60.0
        timestamps = [ts for ts in timestamps if ts > cutoff]

        if len(timestamps) >= bot.rate_limit:
            return False

        timestamps.append(now)
        self._rate_limiters[bot_name] = timestamps
        return True


# ---------------------------------------------------------------------------
# Command parser
# ---------------------------------------------------------------------------

class CommandParser:
    """Parses bot command messages.

    Recognizes messages starting with / and extracts command name
    and arguments. Supports quoted arguments.

    Examples:
        "/echo hello world"     → command="echo", args=["hello", "world"]
        '/search "hello world"' → command="search", args=["hello world"]
        "/help"                 → command="help", args=[]
    """

    COMMAND_PREFIX = "/"

    @classmethod
    def parse(cls, text: str) -> tuple[str, list[str]]:
        """Parse a command message.

        Args:
            text: Raw message text.

        Returns:
            Tuple of (command_name, arguments).
            If not a command, returns ("", []).
        """
        text = text.strip()
        if not text.startswith(cls.COMMAND_PREFIX):
            return "", []

        # Remove leading /
        rest = text[1:]

        # Split into parts, respecting quoted strings
        parts = cls._split_with_quotes(rest)
        if not parts:
            return "", []

        command = parts[0].lower()
        args = parts[1:]

        return command, args

    @classmethod
    def _split_with_quotes(cls, text: str) -> list[str]:
        """Split text respecting double-quoted strings."""
        parts: list[str] = []
        current = []
        in_quotes = False

        for char in text:
            if char == '"':
                in_quotes = not in_quotes
            elif char == ' ' and not in_quotes:
                if current:
                    parts.append(''.join(current))
                    current = []
            else:
                current.append(char)

        if current:
            parts.append(''.join(current))

        return parts


# ---------------------------------------------------------------------------
# Built-in bots
# ---------------------------------------------------------------------------

class HelpBot(BotHandler):
    """Built-in help bot that lists available bots and commands."""

    name = "help"
    description = "Show available bots and commands"
    commands = [
        BotCommand(name="help", description="Show this help message"),
    ]

    def __init__(self, registry: BotRegistry) -> None:
        self._registry = registry

    async def handle_message(self, msg: BotMessage) -> Optional[str]:
        bots = self._registry.list_bots()
        if not bots:
            return "No bots are currently registered."

        lines = ["Available bots:"]
        for bot in bots:
            lines.append(f"  /{bot.name} — {bot.description}")
            for cmd in bot.commands:
                lines.append(f"    /{bot.name} {cmd.name} — {cmd.description}")

        return "\n".join(lines)


class PingBot(BotHandler):
    """Simple ping/pong bot for testing."""

    name = "ping"
    description = "Responds with pong"
    commands = [
        BotCommand(name="ping", description="Check if the bot is alive"),
    ]

    async def handle_message(self, msg: BotMessage) -> Optional[str]:
        if msg.command == "ping" or msg.text.lower() == "ping":
            return "🏓 Pong!"
        return None


# ---------------------------------------------------------------------------
# Bot API plugin
# ---------------------------------------------------------------------------

class BotApiPlugin(BasePlugin):
    """Built-in Bot API plugin for Presidium.

    Manages bot registration, message routing, and command dispatching.
    Bots receive messages that are directed to them via /botname commands
    or by mentioning them in group chats.

    Message routing:
        1. User sends "/echo hello" in a chat
        2. BotApiPlugin intercepts the message via MESSAGE_RECEIVED hook
        3. Parses the command: bot="echo", args=["hello"]
        4. Routes to the EchoBot handler
        5. EchoBot returns "hello"
        6. Plugin sends the response back to the chat
    """

    def __init__(self) -> None:
        super().__init__()
        self._registry = BotRegistry()
        self._parser = CommandParser()
        self._bot_prefix = "/"

    async def on_load(self, ctx: PluginContext) -> None:
        """Initialize the bot API plugin."""
        self._context = ctx  # type: ignore[assignment]

        # Register built-in bots
        self._registry.register(HelpBot(self._registry))
        self._registry.register(PingBot())

        # Register hooks
        self.register_hook(PluginHook.MESSAGE_RECEIVED, self._on_message)
        self.register_hook(PluginHook.MESSAGE_SENDING, self._on_message_sending)

        logger.info(
            "Bot API plugin loaded with %d bots",
            len(self._registry.list_bots()),
        )

    async def on_unload(self) -> None:
        """Clean up the bot API plugin."""
        # Stop all bots
        for bot in self._registry.list_bots():
            try:
                await bot.on_stop()
            except Exception as exc:
                logger.error("Error stopping bot %s: %s", bot.name, exc)

    async def _on_message(self, hook: PluginHook, data: dict[str, Any]) -> None:
        """Route incoming messages to appropriate bots."""
        text = data.get("content", "")
        peer_id = data.get("peer_id", data.get("sender_user_id", ""))
        message_id = data.get("message_id", "")
        is_group = data.get("is_group", False)

        # Check if this is a bot command
        command, args = self._parser.parse(text)
        if not command:
            return

        # Find the bot
        bot = self._registry.get_bot(command)
        if bot is None:
            return

        # Rate limit check
        if not self._registry.check_rate_limit(bot.name):
            logger.warning("Bot %s rate limited", bot.name)
            return

        # Create bot message
        bot_msg = BotMessage(
            message_id=message_id,
            peer_id=peer_id,
            text=text,
            command=command,
            args=args,
            is_private=not is_group,
            timestamp=time.time(),
        )

        # Dispatch to bot handler
        try:
            response = await bot.handle_message(bot_msg)
            if response and self._context:
                await self._context.send_message(peer_id, response)
        except Exception as exc:
            logger.error("Bot %s handler error: %s", bot.name, exc, exc_info=exc)

    async def _on_message_sending(
        self, hook: PluginHook, data: dict[str, Any]
    ) -> None:
        """Intercept outgoing messages for bot processing."""
        # Bots can also intercept outgoing messages if needed
        # For now, we only process incoming messages
        pass

    # -------------------------------------------------------------------
    # Public API
    # -------------------------------------------------------------------

    def register_bot(self, bot: BotHandler) -> None:
        """Register a new bot handler."""
        self._registry.register(bot)

    def unregister_bot(self, name: str) -> None:
        """Unregister a bot by name."""
        self._registry.unregister(name)

    def list_bots(self) -> list[BotHandler]:
        """List all registered bots."""
        return self._registry.list_bots()
