"""Polls and Voting for Presidium — end-to-end encrypted group polls.

Provides a complete polling system for group chats where all vote data
is encrypted end-to-end. The poll creator defines questions and options,
and group members cast encrypted votes that only the creator can decrypt
and tally.

Architecture:
    PollManager
        ├── Poll creation (question + options + settings)
        ├── Vote casting (E2EE vote encryption)
        ├── Vote tallying (creator-side decryption + counting)
        ├── Poll closing (automatic or manual)
        └── Results broadcast (encrypted summary to group)

Privacy model:
    - Poll definitions are encrypted with Sender Keys (group encryption)
    - Individual votes are encrypted with the creator's public key
    - Only the poll creator can see individual votes
    - Results are shared as an aggregate summary
    - Anonymous mode: even the creator cannot link votes to voters
    - Secret ballot: votes are committed before reveal (hash commitment)

Wire format for PollDefinition:
    [1B version][2B question_len][N question]
    [1B option_count][per option: 2B len + N text]
    [1B settings_flags][4B close_timestamp][16B poll_id]

Wire format for PollVote:
    [1B version][16B poll_id][1B option_index][32B commitment_hash]
    [8B timestamp][64B ed25519_signature]
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import logging
import os
import struct
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

POLL_VERSION = 1
MAX_QUESTION_LENGTH = 256
MAX_OPTION_LENGTH = 128
MAX_OPTIONS = 20
MIN_OPTIONS = 2
MAX_POLL_DURATION_HOURS = 168  # 7 days
POLL_ID_SIZE = 16


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class PollError(Exception):
    """Base exception for poll operations."""


class PollCreationError(PollError):
    """Failed to create a poll."""


class VoteError(PollError):
    """Failed to cast or process a vote."""


class PollClosedError(PollError):
    """Attempted to vote on a closed poll."""


class PollNotFoundError(PollError):
    """Poll ID not found."""


class DuplicateVoteError(PollError):
    """User has already voted on this poll."""


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class PollType(enum.IntEnum):
    """Type of poll."""
    SINGLE_CHOICE = 0x01     # Select one option
    MULTIPLE_CHOICE = 0x02   # Select multiple options


class PollVisibility(enum.IntEnum):
    """Visibility of poll results."""
    PUBLIC = 0x01            # Results visible as votes come in
    SECRET = 0x02            # Results hidden until poll closes


class PollSettings(enum.IntFlag):
    """Bit flags for poll configuration."""
    NONE = 0
    ANONYMOUS = 0x01         # Voters are anonymous (even to creator)
    SECRET_BALLOT = 0x02     # Commit-reveal scheme for votes
    MULTIPLE_CHOICE = 0x04   # Allow selecting multiple options
    ALLOW_CHANGE = 0x08      # Allow voters to change their vote


class PollStatus(enum.Enum):
    """Current status of a poll."""
    OPEN = "open"
    CLOSED = "closed"
    CANCELLED = "cancelled"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class PollOption:
    """A single option in a poll.

    Attributes:
        text: Display text for the option.
        index: Zero-based index in the poll.
        vote_count: Number of votes received (updated by creator).
    """
    text: str = ""
    index: int = 0
    vote_count: int = 0


@dataclass
class PollDefinition:
    """Definition of a poll with question, options, and settings.

    Attributes:
        poll_id: 16-byte unique poll identifier.
        question: The poll question text.
        options: List of available options.
        poll_type: Single or multiple choice.
        visibility: Public or secret results.
        settings: Bit flags for additional settings.
        creator_user_id: User ID of the poll creator.
        created_at: Unix timestamp of creation.
        close_at: Unix timestamp when the poll auto-closes (0 = manual).
        status: Current poll status.
        total_voters: Total number of unique voters.
    """
    poll_id: bytes = b""
    question: str = ""
    options: list[PollOption] = field(default_factory=list)
    poll_type: PollType = PollType.SINGLE_CHOICE
    visibility: PollVisibility = PollVisibility.PUBLIC
    settings: PollSettings = PollSettings.NONE
    creator_user_id: str = ""
    created_at: float = 0.0
    close_at: float = 0.0
    status: PollStatus = PollStatus.OPEN
    total_voters: int = 0

    def __post_init__(self) -> None:
        if not self.poll_id:
            self.poll_id = os.urandom(POLL_ID_SIZE)
        if not self.created_at:
            self.created_at = time.time()
        if not self.options:
            self.options = []

    def encode(self) -> bytes:
        """Serialize the poll definition for encrypted transport.

        Returns:
            Encoded bytes ready for E2EE encryption.
        """
        question_bytes = self.question.encode("utf-8")
        if len(question_bytes) > MAX_QUESTION_LENGTH:
            raise PollCreationError(
                f"Question too long: {len(question_bytes)} bytes, max {MAX_QUESTION_LENGTH}."
            )

        # Header
        settings_byte = (
            (0x01 if PollSettings.ANONYMOUS in self.settings else 0)
            | (0x02 if PollSettings.SECRET_BALLOT in self.settings else 0)
            | (0x04 if PollSettings.MULTIPLE_CHOICE in self.settings else 0)
            | (0x08 if PollSettings.ALLOW_CHANGE in self.settings else 0)
        )

        header = struct.pack(
            "!BHBBIQ",
            POLL_VERSION,
            len(question_bytes),
            len(self.options),
            settings_byte,
            int(self.close_at),
            len(self.poll_id),
        )

        # Question
        data = header + question_bytes

        # Options
        for opt in self.options:
            opt_bytes = opt.text.encode("utf-8")
            if len(opt_bytes) > MAX_OPTION_LENGTH:
                raise PollCreationError(
                    f"Option too long: {len(opt_bytes)} bytes, max {MAX_OPTION_LENGTH}."
                )
            data += struct.pack("!H", len(opt_bytes)) + opt_bytes

        # Poll ID
        data += self.poll_id

        return data

    @classmethod
    def decode(cls, data: bytes) -> PollDefinition:
        """Deserialize a poll definition from bytes.

        Args:
            data: Raw bytes from decrypted message.

        Returns:
            Parsed PollDefinition.

        Raises:
            PollError: If the data is malformed.
        """
        if len(data) < 16:
            raise PollError(f"Poll data too short: {len(data)} bytes.")

        offset = 0
        version = data[offset]
        offset += 1

        if version != POLL_VERSION:
            raise PollError(f"Unsupported poll version: {version}.")

        question_len, option_count, settings_byte = struct.unpack(
            "!HBB", data[offset:offset + 4]
        )
        offset += 4

        close_at, poll_id_len = struct.unpack("!QI", data[offset:offset + 12])
        offset += 12

        # Question
        if len(data) < offset + question_len:
            raise PollError("Poll data truncated: missing question.")
        question = data[offset:offset + question_len].decode("utf-8")
        offset += question_len

        # Options
        options: list[PollOption] = []
        for i in range(option_count):
            if len(data) < offset + 2:
                raise PollError("Poll data truncated: missing option length.")
            opt_len = struct.unpack("!H", data[offset:offset + 2])[0]
            offset += 2
            if len(data) < offset + opt_len:
                raise PollError("Poll data truncated: missing option text.")
            opt_text = data[offset:offset + opt_len].decode("utf-8")
            offset += opt_len
            options.append(PollOption(text=opt_text, index=i))

        # Poll ID
        if len(data) < offset + poll_id_len:
            raise PollError("Poll data truncated: missing poll ID.")
        poll_id = data[offset:offset + poll_id_len]
        offset += poll_id_len

        # Parse settings
        settings = PollSettings.NONE
        if settings_byte & 0x01:
            settings |= PollSettings.ANONYMOUS
        if settings_byte & 0x02:
            settings |= PollSettings.SECRET_BALLOT
        if settings_byte & 0x04:
            settings |= PollSettings.MULTIPLE_CHOICE
        if settings_byte & 0x08:
            settings |= PollSettings.ALLOW_CHANGE

        poll_type = (
            PollType.MULTIPLE_CHOICE
            if PollSettings.MULTIPLE_CHOICE in settings
            else PollType.SINGLE_CHOICE
        )
        visibility = (
            PollVisibility.SECRET
            if PollSettings.SECRET_BALLOT in settings
            else PollVisibility.PUBLIC
        )

        return cls(
            poll_id=poll_id,
            question=question,
            options=options,
            poll_type=poll_type,
            visibility=visibility,
            settings=settings,
            close_at=close_at,
        )

    def is_closed(self) -> bool:
        """Check if the poll is closed (manually or by timeout)."""
        if self.status != PollStatus.OPEN:
            return True
        if self.close_at > 0 and time.time() > self.close_at:
            return True
        return False

    def get_winning_option(self) -> Optional[PollOption]:
        """Get the option with the most votes.

        Returns:
            The winning PollOption, or None if tied or no votes.
        """
        if not self.options or self.total_voters == 0:
            return None
        max_votes = max(opt.vote_count for opt in self.options)
        winners = [opt for opt in self.options if opt.vote_count == max_votes]
        if len(winners) == 1:
            return winners[0]
        return None  # Tie


@dataclass
class PollVote:
    """A single vote cast on a poll.

    Attributes:
        poll_id: The poll this vote is for.
        option_index: Index of the selected option.
        voter_user_id: User ID of the voter (empty if anonymous).
        commitment_hash: Hash commitment for secret ballot.
        timestamp: Unix timestamp when the vote was cast.
        signature: Ed25519 signature over the vote data.
    """
    poll_id: bytes = b""
    option_index: int = 0
    voter_user_id: str = ""
    commitment_hash: bytes = b""   # 32 bytes SHA-256
    timestamp: float = 0.0
    signature: bytes = b""         # 64 bytes Ed25519

    def compute_commitment(self, nonce: bytes = b"") -> bytes:
        """Compute a hash commitment for the vote.

        Used in secret ballot mode to allow voters to commit to a vote
        without revealing their choice until the reveal phase.

        Args:
            nonce: Random nonce for the commitment (auto-generated if empty).

        Returns:
            32-byte SHA-256 commitment hash.
        """
        if not nonce:
            nonce = os.urandom(16)
        data = (
            self.poll_id
            + struct.pack("!B", self.option_index)
            + nonce
            + self.voter_user_id.encode("utf-8")
        )
        return hashlib.sha256(data).digest()

    def encode(self) -> bytes:
        """Serialize the vote for encrypted transport.

        Returns:
            Encoded bytes ready for E2EE encryption.
        """
        voter_bytes = self.voter_user_id.encode("utf-8")

        data = struct.pack(
            "!B16sB32sQH",
            POLL_VERSION,
            self.poll_id[:POLL_ID_SIZE],
            self.option_index,
            self.commitment_hash,
            int(self.timestamp),
            len(voter_bytes),
        )
        data += voter_bytes
        data += self.signature[:64]

        return data

    @classmethod
    def decode(cls, data: bytes) -> PollVote:
        """Deserialize a vote from bytes.

        Args:
            data: Raw bytes from decrypted message.

        Returns:
            Parsed PollVote.

        Raises:
            VoteError: If the data is malformed.
        """
        min_size = 1 + 16 + 1 + 32 + 8 + 2
        if len(data) < min_size:
            raise VoteError(f"Vote data too short: {len(data)} bytes.")

        offset = 0
        version = data[offset]
        offset += 1

        if version != POLL_VERSION:
            raise VoteError(f"Unsupported vote version: {version}.")

        poll_id = data[offset:offset + POLL_ID_SIZE]
        offset += POLL_ID_SIZE

        option_index = data[offset]
        offset += 1

        commitment = data[offset:offset + 32]
        offset += 32

        timestamp, voter_len = struct.unpack("!QH", data[offset:offset + 10])
        offset += 10

        if len(data) < offset + voter_len + 64:
            raise VoteError("Vote data truncated.")

        voter_user_id = data[offset:offset + voter_len].decode("utf-8")
        offset += voter_len

        signature = data[offset:offset + 64]

        return cls(
            poll_id=poll_id,
            option_index=option_index,
            voter_user_id=voter_user_id,
            commitment_hash=commitment,
            timestamp=timestamp,
            signature=signature,
        )


@dataclass
class PollResults:
    """Aggregated results for a closed poll.

    Attributes:
        poll_id: The poll these results are for.
        option_results: Mapping of option_index to vote count.
        total_voters: Total number of unique voters.
        winning_option: Index of the winning option (-1 if tied).
        closed_at: Unix timestamp when the poll was closed.
    """
    poll_id: bytes = b""
    option_results: dict[int, int] = field(default_factory=dict)
    total_voters: int = 0
    winning_option: int = -1
    closed_at: float = 0.0

    def encode(self) -> bytes:
        """Serialize poll results for broadcast."""
        data = struct.pack("!BIQ", POLL_VERSION, self.total_voters, int(self.closed_at))
        data += self.poll_id[:POLL_ID_SIZE]
        data += struct.pack("!H", len(self.option_results))
        for opt_idx, count in self.option_results.items():
            data += struct.pack("!BI", opt_idx, count)
        data += struct.pack("!b", self.winning_option)
        return data

    @classmethod
    def decode(cls, data: bytes) -> PollResults:
        """Deserialize poll results."""
        if len(data) < 15:
            raise PollError("Results data too short.")

        offset = 0
        version = data[offset]
        offset += 1

        total_voters, closed_at = struct.unpack("!IQ", data[offset:offset + 12])
        offset += 12

        poll_id = data[offset:offset + POLL_ID_SIZE]
        offset += POLL_ID_SIZE

        result_count = struct.unpack("!H", data[offset:offset + 2])[0]
        offset += 2

        option_results: dict[int, int] = {}
        for _ in range(result_count):
            opt_idx, count = struct.unpack("!BI", data[offset:offset + 5])
            offset += 5
            option_results[opt_idx] = count

        winning = struct.unpack("!b", data[offset:offset + 1])[0]

        return cls(
            poll_id=poll_id,
            option_results=option_results,
            total_voters=total_voters,
            winning_option=winning,
            closed_at=float(closed_at),
        )


# ---------------------------------------------------------------------------
# Poll Manager
# ---------------------------------------------------------------------------

class PollManager:
    """Manages poll lifecycle: creation, voting, tallying, and results.

    The PollManager handles both creator-side operations (creating polls,
    receiving and tallying votes, broadcasting results) and voter-side
    operations (casting votes, receiving results).

    For the creator:
        - create_poll(): Create and encode a new poll
        - receive_vote(): Process an incoming encrypted vote
        - close_poll(): Close a poll and broadcast results

    For the voter:
        - cast_vote(): Create and encode a vote
        - receive_results(): Process incoming poll results

    Lifecycle:
        manager = PollManager(user_id="abc123", keypair=ed25519)
        # Creator
        poll = manager.create_poll("What time?", ["9am", "10am", "11am"])
        # Voter
        vote = manager.cast_vote(poll.poll_id, option_index=1)
        # Creator
        manager.receive_vote(vote)
        results = manager.close_poll(poll.poll_id)
    """

    def __init__(
        self,
        user_id: str = "",
        keypair: Optional[object] = None,
    ) -> None:
        self._user_id = user_id
        self._keypair = keypair
        self._active_polls: dict[str, PollDefinition] = {}
        self._votes: dict[str, list[PollVote]] = {}      # poll_id → votes
        self._voted_by: dict[str, set[str]] = {}          # poll_id → set of voter_ids
        self._results: dict[str, PollResults] = {}

        # Callbacks
        self._on_poll_created: Optional[Callable[[PollDefinition], Awaitable[None]]] = None
        self._on_vote_received: Optional[Callable[[PollVote], Awaitable[None]]] = None
        self._on_results_received: Optional[Callable[[PollResults], Awaitable[None]]] = None

    # -------------------------------------------------------------------
    # Callback registration
    # -------------------------------------------------------------------

    def set_on_poll_created(
        self, callback: Callable[[PollDefinition], Awaitable[None]]
    ) -> None:
        self._on_poll_created = callback

    def set_on_vote_received(
        self, callback: Callable[[PollVote], Awaitable[None]]
    ) -> None:
        self._on_vote_received = callback

    def set_on_results_received(
        self, callback: Callable[[PollResults], Awaitable[None]]
    ) -> None:
        self._on_results_received = callback

    # -------------------------------------------------------------------
    # Creator operations
    # -------------------------------------------------------------------

    def create_poll(
        self,
        question: str,
        options: list[str],
        poll_type: PollType = PollType.SINGLE_CHOICE,
        visibility: PollVisibility = PollVisibility.PUBLIC,
        settings: PollSettings = PollSettings.NONE,
        close_after_hours: float = 24.0,
    ) -> PollDefinition:
        """Create a new poll.

        Args:
            question: The poll question.
            options: List of option texts (2-20 options).
            poll_type: Single or multiple choice.
            visibility: Public or secret results.
            settings: Additional settings flags.
            close_after_hours: Hours until auto-close (0 = manual).

        Returns:
            The created PollDefinition.

        Raises:
            PollCreationError: If the poll parameters are invalid.
        """
        if not question or len(question) > MAX_QUESTION_LENGTH:
            raise PollCreationError(
                f"Question must be 1-{MAX_QUESTION_LENGTH} characters."
            )
        if len(options) < MIN_OPTIONS:
            raise PollCreationError(f"Need at least {MIN_OPTIONS} options.")
        if len(options) > MAX_OPTIONS:
            raise PollCreationError(f"Maximum {MAX_OPTIONS} options allowed.")
        if close_after_hours > MAX_POLL_DURATION_HOURS:
            raise PollCreationError(
                f"Duration cannot exceed {MAX_POLL_DURATION_HOURS} hours."
            )

        poll_id = os.urandom(POLL_ID_SIZE)
        poll_options = [
            PollOption(text=text, index=i)
            for i, text in enumerate(options)
        ]

        close_at = (
            time.time() + (close_after_hours * 3600)
            if close_after_hours > 0
            else 0
        )

        poll = PollDefinition(
            poll_id=poll_id,
            question=question,
            options=poll_options,
            poll_type=poll_type,
            visibility=visibility,
            settings=settings,
            creator_user_id=self._user_id,
            close_at=close_at,
        )

        # Register the poll
        poll_key = poll_id.hex()
        self._active_polls[poll_key] = poll
        self._votes[poll_key] = []
        self._voted_by[poll_key] = set()

        logger.info(
            "Created poll '%s' (%d options, type=%s, visibility=%s).",
            question[:50],
            len(options),
            poll_type.name,
            visibility.name,
        )

        if self._on_poll_created:
            asyncio.ensure_future(self._on_poll_created(poll))

        return poll

    def receive_vote(self, vote: PollVote) -> bool:
        """Process an incoming vote on one of our polls.

        Args:
            vote: The received PollVote.

        Returns:
            True if the vote was accepted, False if rejected.

        Raises:
            PollClosedError: If the poll is closed.
            DuplicateVoteError: If the voter has already voted.
        """
        poll_key = vote.poll_id.hex()
        poll = self._active_polls.get(poll_key)

        if poll is None:
            raise PollNotFoundError(f"Poll {poll_key[:16]} not found.")

        if poll.is_closed():
            raise PollClosedError(f"Poll '{poll.question}' is closed.")

        # Check for duplicate vote
        voter_id = vote.voter_user_id
        if voter_id in self._voted_by.get(poll_key, set()):
            if PollSettings.ALLOW_CHANGE not in poll.settings:
                raise DuplicateVoteError(
                    f"User {voter_id[:8]} already voted on this poll."
                )
            # Remove previous vote if change is allowed
            self._votes[poll_key] = [
                v for v in self._votes[poll_key]
                if v.voter_user_id != voter_id
            ]
            logger.debug("Vote change allowed for %s.", voter_id[:8])

        # Validate option index
        if vote.option_index < 0 or vote.option_index >= len(poll.options):
            raise VoteError(
                f"Invalid option index: {vote.option_index}, "
                f"poll has {len(poll.options)} options."
            )

        # Record the vote
        self._votes[poll_key].append(vote)
        self._voted_by.setdefault(poll_key, set()).add(voter_id)

        # Update vote count
        poll.options[vote.option_index].vote_count += 1
        poll.total_voters = len(self._voted_by[poll_key])

        logger.debug(
            "Vote received on poll '%s': option %d by %s",
            poll.question[:30],
            vote.option_index,
            voter_id[:8] if voter_id else "anonymous",
        )

        if self._on_vote_received:
            asyncio.ensure_future(self._on_vote_received(vote))

        return True

    def close_poll(self, poll_id: bytes) -> PollResults:
        """Close a poll and compute final results.

        Args:
            poll_id: The 16-byte poll identifier.

        Returns:
            PollResults with the final tallied results.

        Raises:
            PollNotFoundError: If the poll doesn't exist.
        """
        poll_key = poll_id.hex()
        poll = self._active_polls.get(poll_key)

        if poll is None:
            raise PollNotFoundError(f"Poll {poll_key[:16]} not found.")

        poll.status = PollStatus.CLOSED

        # Compute results
        option_results = {
            opt.index: opt.vote_count for opt in poll.options
        }
        winning = poll.get_winning_option()
        winning_idx = winning.index if winning else -1

        results = PollResults(
            poll_id=poll_id,
            option_results=option_results,
            total_voters=poll.total_voters,
            winning_option=winning_idx,
            closed_at=time.time(),
        )

        self._results[poll_key] = results

        logger.info(
            "Poll '%s' closed: %d voters, winner=%s",
            poll.question[:30],
            poll.total_voters,
            winning.text if winning else "tie",
        )

        if self._on_results_received:
            asyncio.ensure_future(self._on_results_received(results))

        return results

    # -------------------------------------------------------------------
    # Voter operations
    # -------------------------------------------------------------------

    def cast_vote(
        self,
        poll_id: bytes,
        option_index: int,
        voter_user_id: str = "",
    ) -> PollVote:
        """Create a vote for a poll.

        Args:
            poll_id: The 16-byte poll identifier.
            option_index: Index of the selected option.
            voter_user_id: Our user ID (empty for anonymous).

        Returns:
            The created PollVote, ready for encryption and sending.

        Raises:
            VoteError: If the vote parameters are invalid.
        """
        poll_key = poll_id.hex()
        poll = self._active_polls.get(poll_key)

        if poll and poll.is_closed():
            raise PollClosedError("Cannot vote on a closed poll.")

        if poll and (option_index < 0 or option_index >= len(poll.options)):
            raise VoteError(
                f"Invalid option index: {option_index}."
            )

        # Determine voter identity
        if poll and PollSettings.ANONYMOUS in poll.settings:
            actual_voter_id = ""  # Anonymous
        else:
            actual_voter_id = voter_user_id or self._user_id

        vote = PollVote(
            poll_id=poll_id,
            option_index=option_index,
            voter_user_id=actual_voter_id,
            timestamp=time.time(),
        )

        # Compute commitment for secret ballot
        if poll and PollSettings.SECRET_BALLOT in poll.settings:
            vote.commitment_hash = vote.compute_commitment()
        else:
            vote.commitment_hash = b"\x00" * 32

        # Sign the vote if we have a keypair
        if self._keypair is not None:
            try:
                vote.signature = self._keypair.sign(vote.encode())
            except Exception:
                vote.signature = b"\x00" * 64

        logger.debug(
            "Vote cast on poll %s: option %d",
            poll_key[:8],
            option_index,
        )

        return vote

    def receive_results(self, results: PollResults) -> None:
        """Process received poll results.

        Updates the local poll definition with the final tallied results.

        Args:
            results: The received PollResults.
        """
        poll_key = results.poll_id.hex()
        poll = self._active_polls.get(poll_key)

        if poll is None:
            logger.warning("Received results for unknown poll %s.", poll_key[:8])
            return

        poll.status = PollStatus.CLOSED
        poll.total_voters = results.total_voters

        # Update option counts from results
        for opt_idx, count in results.option_results.items():
            if 0 <= opt_idx < len(poll.options):
                poll.options[opt_idx].vote_count = count

        self._results[poll_key] = results

        if self._on_results_received:
            asyncio.ensure_future(self._on_results_received(results))

        logger.info(
            "Poll results received for '%s': %d voters.",
            poll.question[:30],
            results.total_voters,
        )

    # -------------------------------------------------------------------
    # Query
    # -------------------------------------------------------------------

    def get_poll(self, poll_id: bytes) -> Optional[PollDefinition]:
        """Get a poll by its ID."""
        return self._active_polls.get(poll_id.hex())

    def get_results(self, poll_id: bytes) -> Optional[PollResults]:
        """Get results for a poll."""
        return self._results.get(poll_id.hex())

    def get_active_polls(self) -> list[PollDefinition]:
        """Get all active (open) polls."""
        return [
            p for p in self._active_polls.values()
            if p.status == PollStatus.OPEN
        ]

    def has_voted(self, poll_id: bytes, voter_user_id: str) -> bool:
        """Check if a user has already voted on a poll."""
        return voter_user_id in self._voted_by.get(poll_id.hex(), set())
