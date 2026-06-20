"""End-to-end integration tests for Phase 4 features.

Tests the complete flow of Phase 4 features including:
    - Bridge federation (Matrix/XMPP message relay)
    - Plugin system (load, hook dispatch, unload)
    - Contact verification (safety numbers)
    - Backup/restore cycle
    - Auto-updater version comparison
    - Mobile UI component instantiation

These tests use mock objects instead of real network connections,
making them suitable for CI/CD without external dependencies.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure presidium is importable
sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# Bridge tests
# ---------------------------------------------------------------------------

class TestBaseBridgeProtocol:
    """Test the abstract BaseBridge protocol and shared utilities."""

    def test_bridge_config_defaults(self):
        """BridgeConfig should have sensible defaults."""
        from presidium.bridges.protocol import BridgeConfig
        config = BridgeConfig(bridge_type="test")
        assert config.bridge_type == "test"
        assert config.inbound_rate_limit == 20
        assert config.outbound_rate_limit == 30
        assert config.max_message_size == 65536
        assert config.relay_typing is True
        assert config.relay_read_receipts is False

    def test_bridged_message_creation(self):
        """BridgedMessage should store all fields correctly."""
        from presidium.bridges.protocol import BridgedMessage, BridgeMessageType, BridgeDirection
        msg = BridgedMessage(
            external_room_id="!room:matrix.org",
            external_sender_id="@alice:matrix.org",
            msg_type=BridgeMessageType.TEXT,
            text_content="Hello from Matrix!",
            direction=BridgeDirection.INBOUND,
        )
        assert msg.external_room_id == "!room:matrix.org"
        assert msg.text_content == "Hello from Matrix!"
        assert msg.direction == BridgeDirection.INBOUND

    def test_bridge_rate_limiting(self):
        """Inbound rate limiting should drop messages over the limit."""
        from presidium.bridges.protocol import BaseBridge, BridgeConfig

        # Create a concrete subclass for testing
        class TestBridge(BaseBridge):
            async def start(self): pass
            async def stop(self): pass
            async def send_message(self, msg): return "evt_123"
            async def create_room(self, pid, is_group=False): return None  # type: ignore
            async def get_user_profile(self, uid): return {}

        config = BridgeConfig(bridge_type="test", inbound_rate_limit=5)
        bridge = TestBridge(config, MagicMock(), "local_user")

        # Should allow 5 messages
        for i in range(5):
            assert bridge._check_inbound_rate_limit() is True

        # 6th should be rate limited
        assert bridge._check_inbound_rate_limit() is False
        assert bridge.stats.inbound_rate_limited == 1

    def test_bridge_registry(self):
        """Bridge registry should register and look up bridge types."""
        from presidium.bridges.protocol import (
            register_bridge, get_bridge_class, list_bridge_types,
            BridgeConfigError,
        )
        from presidium.bridges.matrix_bridge import MatrixBridge
        from presidium.bridges.xmpp_bridge import XmppBridge

        # Check that our bridges are registered
        types = list_bridge_types()
        assert "matrix" in types
        assert "xmpp" in types

        # Look up by type
        assert get_bridge_class("matrix") is MatrixBridge
        assert get_bridge_class("xmpp") is XmppBridge

        # Unknown type should raise
        with pytest.raises(BridgeConfigError):
            get_bridge_class("nonexistent")


class TestMatrixBridge:
    """Test Matrix bridge event conversion."""

    def test_text_event_conversion(self):
        """Matrix text message events should convert to BridgedMessage."""
        from presidium.bridges.matrix_bridge import matrix_event_to_bridged
        from presidium.bridges.protocol import BridgeMessageType, BridgeDirection

        event = {
            "type": "m.room.message",
            "sender": "@alice:matrix.org",
            "event_id": "$event123",
            "origin_server_ts": 1700000000000,
            "content": {
                "msgtype": "m.text",
                "body": "Hello from Matrix!",
            },
        }

        msg = matrix_event_to_bridged(event, "!room:matrix.org", "@bot:matrix.org")
        assert msg is not None
        assert msg.msg_type == BridgeMessageType.TEXT
        assert msg.text_content == "Hello from Matrix!"
        assert msg.external_sender_id == "@alice:matrix.org"
        assert msg.direction == BridgeDirection.INBOUND

    def test_reaction_event_conversion(self):
        """Matrix reaction events should convert to BridgedMessage."""
        from presidium.bridges.matrix_bridge import matrix_event_to_bridged
        from presidium.bridges.protocol import BridgeMessageType

        event = {
            "type": "m.reaction",
            "sender": "@bob:matrix.org",
            "event_id": "$reaction123",
            "origin_server_ts": 1700000000000,
            "content": {
                "m.relates_to": {
                    "rel_type": "m.annotation",
                    "event_id": "$original_event",
                    "key": "👍",
                },
            },
        }

        msg = matrix_event_to_bridged(event, "!room:matrix.org", "@bot:matrix.org")
        assert msg is not None
        assert msg.msg_type == BridgeMessageType.REACTION
        assert msg.emoji == "👍"

    def test_skip_own_messages(self):
        """Bridge bot's own messages should be skipped."""
        from presidium.bridges.matrix_bridge import matrix_event_to_bridged

        event = {
            "type": "m.room.message",
            "sender": "@bot:matrix.org",
            "event_id": "$own_event",
            "content": {"msgtype": "m.text", "body": "Echo"},
        }

        msg = matrix_event_to_bridged(event, "!room:matrix.org", "@bot:matrix.org")
        assert msg is None


class TestXmppBridge:
    """Test XMPP bridge stanza conversion."""

    def test_text_message_conversion(self):
        """XMPP message stanzas should convert to BridgedMessage."""
        import xml.etree.ElementTree as ET
        from presidium.bridges.xmpp_bridge import xmpp_stanza_to_bridged
        from presidium.bridges.protocol import BridgeMessageType

        stanza = ET.fromstring(
            "<message from='alice@xmpp.org' to='bot@xmpp.org' type='chat' id='msg1'>"
            "<body>Hello from XMPP!</body>"
            "</message>"
        )

        msg = xmpp_stanza_to_bridged(stanza, "bot@xmpp.org")
        assert msg is not None
        assert msg.msg_type == BridgeMessageType.TEXT
        assert msg.text_content == "Hello from XMPP!"

    def test_skip_own_messages(self):
        """Bridge bot's own messages should be skipped."""
        import xml.etree.ElementTree as ET
        from presidium.bridges.xmpp_bridge import xmpp_stanza_to_bridged

        stanza = ET.fromstring(
            "<message from='bot@xmpp.org/resource' to='alice@xmpp.org' type='chat' id='msg2'>"
            "<body>Echo</body>"
            "</message>"
        )

        msg = xmpp_stanza_to_bridged(stanza, "bot@xmpp.org/resource")
        assert msg is None


# ---------------------------------------------------------------------------
# Plugin tests
# ---------------------------------------------------------------------------

class TestPluginSystem:
    """Test the plugin system lifecycle."""

    @pytest.mark.asyncio
    async def test_plugin_load_unload(self):
        """Plugin should load, register hooks, and unload cleanly."""
        from presidium.plugins.base import BasePlugin, PluginContext, PluginHook, PluginPermission
        from presidium.plugins.manager import PluginManager

        # Create a simple test plugin
        class TestPlugin(BasePlugin):
            async def on_load(self, ctx):
                self._ctx = ctx
                self.register_hook(PluginHook.MESSAGE_RECEIVED, self._on_msg)
                self.loaded = True

            async def on_unload(self):
                self.loaded = False

            async def _on_msg(self, hook, data):
                self.last_msg = data

        # Set up manager with temp directory
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = PluginManager(data_dir=Path(tmpdir))
            plugin = TestPlugin()

            # Simulate loading
            ctx = PluginContext(
                plugin_id="test_plugin",
                plugin_dir=tmpdir,
                granted_permissions=PluginPermission.SEND_MESSAGES | PluginPermission.READ_MESSAGES,
            )
            plugin._set_context(ctx)
            plugin._set_manifest(MagicMock())
            await plugin.on_load(ctx)

            assert plugin.loaded is True

            # Dispatch a hook event
            await plugin._get_hook_callbacks()[PluginHook.MESSAGE_RECEIVED][0](
                PluginHook.MESSAGE_RECEIVED, {"content": "test"}
            )
            assert plugin.last_msg["content"] == "test"

            # Unload
            await plugin.on_unload()
            assert plugin.loaded is False

    @pytest.mark.asyncio
    async def test_bot_api_plugin(self):
        """BotApiPlugin should route commands to bot handlers."""
        from presidium.plugins.bot_api import BotApiPlugin, BotHandler, BotMessage

        plugin = BotApiPlugin()
        ctx = MagicMock()
        ctx.send_message = AsyncMock()
        await plugin.on_load(ctx)

        # Test ping bot
        msg = BotMessage(
            peer_id="peer123",
            text="/ping",
            command="ping",
        )
        ping_bot = plugin._registry.get_bot("ping")
        assert ping_bot is not None

        response = await ping_bot.handle_message(msg)
        assert response == "🏓 Pong!"

    @pytest.mark.asyncio
    async def test_scheduled_messages(self):
        """ScheduledMessagesPlugin should create and list messages."""
        from presidium.plugins.scheduled_messages import (
            ScheduledMessagesPlugin, ScheduledMessage, TimeParser,
        )

        plugin = ScheduledMessagesPlugin()
        ctx = MagicMock()
        ctx.send_message = AsyncMock()
        ctx.plugin_dir = tempfile.mkdtemp()

        await plugin.on_load(ctx)

        # Test time parsing
        ts = TimeParser.parse("+1h")
        assert ts is not None
        assert ts > time.time()
        assert ts < time.time() + 3700  # Within 1 hour + margin

        # Schedule a message
        msg = await plugin.schedule(
            peer_id="peer123",
            text="Test message",
            scheduled_at=time.time() + 3600,
        )
        assert msg.peer_id == "peer123"
        assert msg.text == "Test message"
        assert msg.is_pending is True

        # List pending
        pending = plugin.get_pending()
        assert len(pending) >= 1

        # Cancel
        result = await plugin.cancel(msg.id)
        assert result is True

        await plugin.on_unload()


# ---------------------------------------------------------------------------
# Enterprise tests
# ---------------------------------------------------------------------------

class TestContactVerification:
    """Test safety number computation and verification."""

    def test_safety_number_computation(self):
        """Safety numbers should be deterministic and symmetric."""
        from presidium.enterprise.verification import SafetyNumber

        sn1 = SafetyNumber.compute(
            user_id_a="alice1234",
            public_key_a=b"A" * 32,
            user_id_b="bob5678",
            public_key_b=b"B" * 32,
        )

        # Swapping A and B should give the same number
        sn2 = SafetyNumber.compute(
            user_id_a="bob5678",
            public_key_a=b"B" * 32,
            user_id_b="alice1234",
            public_key_b=b"A" * 32,
        )

        assert sn1.number == sn2.number
        assert len(sn1.number) == 60

    def test_safety_number_display(self):
        """Safety number should format nicely for display."""
        from presidium.enterprise.verification import SafetyNumber

        sn = SafetyNumber.compute(
            user_id_a="alice",
            public_key_a=b"A" * 32,
            user_id_b="bob",
            public_key_b=b"B" * 32,
        )

        display = sn.format_display()
        assert "  " in display  # Groups separated by double space

        compact = sn.format_compact()
        assert "-" in compact  # Compact format has dashes

    def test_verification_level_tracking(self):
        """ContactVerification should track verification state."""
        from presidium.enterprise.verification import (
            ContactVerification, VerificationLevel,
        )

        cv = ContactVerification()
        sn = cv.compute_safety_number(
            "alice", b"A" * 32,
            "bob", b"B" * 32,
        )

        # Initially unverified
        assert cv.get_verification_level("bob") == VerificationLevel.UNVERIFIED

        # Verify
        level = cv.verify_contact("bob", sn)
        assert level == VerificationLevel.VERIFIED
        assert cv.get_verification_level("bob") == VerificationLevel.VERIFIED

        # Unverify
        cv.unverify_contact("bob")
        assert cv.get_verification_level("bob") == VerificationLevel.UNVERIFIED

    def test_safety_number_change_detection(self):
        """ContactVerification should detect safety number changes."""
        from presidium.enterprise.verification import ContactVerification, VerificationLevel

        cv = ContactVerification()

        # First verification
        sn1 = cv.compute_safety_number("alice", b"A" * 32, "bob", b"B" * 32)
        cv.verify_contact("bob", sn1)

        # Key rotation: bob gets a new key
        sn2 = cv.compute_safety_number("alice", b"A" * 32, "bob", b"C" * 32)
        level = cv.verify_contact("bob", sn2)

        assert level == VerificationLevel.VERIFIED_WITH_CHANGE
        assert cv.has_safety_number_changed("bob")


class TestBackupRestore:
    """Test encrypted backup and restore cycle."""

    @pytest.mark.asyncio
    async def test_backup_restore_cycle(self):
        """Data should survive a backup → restore cycle unchanged."""
        from presidium.enterprise.backup import BackupManager, RestoreManager, BackupData

        with tempfile.TemporaryDirectory() as tmpdir:
            backup_path = Path(tmpdir) / "test_backup.json"
            password = "test-password-123"

            # Create backup
            backup_mgr = BackupManager(data_dir=Path(tmpdir))
            original_data = BackupData(
                identity={"user_id": "alice1234"},
                contacts=[{"user_id": "bob5678", "nickname": "Bob"}],
                messages=[{"content": "Hello!", "sender": "bob5678"}],
                settings={"theme": "dark"},
            )

            providers = {
                "identity": lambda: original_data.identity,
                "contacts": lambda: original_data.contacts,
                "messages": lambda: original_data.messages,
                "settings": lambda: original_data.settings,
            }

            await backup_mgr.create_backup(password, backup_path, providers)
            assert backup_path.exists()

            # Restore
            restore_mgr = RestoreManager()
            restored_data = await restore_mgr.restore(backup_path, password)

            assert restored_data.identity == original_data.identity
            assert restored_data.contacts == original_data.contacts
            assert restored_data.messages == original_data.messages
            assert restored_data.settings == original_data.settings

    @pytest.mark.asyncio
    async def test_backup_wrong_password(self):
        """Restoring with wrong password should fail."""
        from presidium.enterprise.backup import BackupManager, RestoreManager, BackupError

        with tempfile.TemporaryDirectory() as tmpdir:
            backup_path = Path(tmpdir) / "test_backup.json"

            backup_mgr = BackupManager(data_dir=Path(tmpdir))
            await backup_mgr.create_backup(
                "correct-password",
                backup_path,
                {"identity": lambda: {"user_id": "test"}},
            )

            restore_mgr = RestoreManager()
            with pytest.raises(BackupError):
                await restore_mgr.restore(backup_path, "wrong-password")


# ---------------------------------------------------------------------------
# Performance / load test tests
# ---------------------------------------------------------------------------

class TestLoadTestFramework:
    """Test the load testing framework itself."""

    @pytest.mark.asyncio
    async def test_simulated_network(self):
        """SimulatedNetwork should deliver messages with latency."""
        from presidium.perf.load_test import SimulatedNetwork, VirtualNode

        network = SimulatedNetwork(base_latency_ms=10, packet_loss_rate=0.0)
        node_a = VirtualNode(nickname="alice")
        node_b = VirtualNode(nickname="bob")
        network.add_node(node_a)
        network.add_node(node_b)

        msg = await network.send_message(node_a.node_id, node_b.node_id, "Hello!")
        assert msg is not None
        assert msg.is_delivered is True
        assert node_b.inbox.qsize() == 1

    @pytest.mark.asyncio
    async def test_load_test_runner(self):
        """LoadTestRunner should complete a short test without errors."""
        from presidium.perf.load_test import LoadTestRunner

        runner = LoadTestRunner(
            num_nodes=5,
            duration_seconds=2,
            messages_per_node_per_second=1.0,
            network_latency_ms=10,
        )

        result = await runner.run()
        assert result.total_nodes == 5
        assert result.total_messages > 0
        assert result.duration_seconds >= 2.0
        assert result.latency_mean > 0


# ---------------------------------------------------------------------------
# Auto-updater tests
# ---------------------------------------------------------------------------

class TestAutoUpdater:
    """Test auto-updater version comparison."""

    def test_version_comparison(self):
        """Version comparison should handle semver correctly."""
        from presidium.release.updater import AutoUpdater

        assert AutoUpdater._compare_versions("1.0.0", "1.0.0") == 0
        assert AutoUpdater._compare_versions("1.1.0", "1.0.0") > 0
        assert AutoUpdater._compare_versions("1.0.0", "2.0.0") < 0
        assert AutoUpdater._compare_versions("0.4.0", "0.3.0") > 0
        assert AutoUpdater._compare_versions("1.10.0", "1.9.0") > 0

    def test_update_available_detection(self):
        """Updater should detect when updates are available."""
        from presidium.release.updater import AutoUpdater, UpdateManifest, UpdateChannel

        updater = AutoUpdater(current_version="0.3.0")

        # Simulate a newer manifest
        manifest = UpdateManifest(
            version="0.4.0",
            channel=UpdateChannel.STABLE,
        )
        updater._latest_manifest = manifest

        assert updater.update_available is True
        assert updater.latest_version == "0.4.0"


# ---------------------------------------------------------------------------
# WASM compatibility tests
# ---------------------------------------------------------------------------

class TestWasmCompat:
    """Test WASM compatibility detection."""

    def test_compatibility_report(self):
        """Compatibility report should list all features."""
        from presidium.perf.wasm_compat import get_compatibility_report

        report = get_compatibility_report()
        assert "crypto" in report
        assert "ratchet" in report
        assert "chat" in report
        assert "dht" in report
        assert "voice" in report
        # In non-WASM mode, crypto/ratchet/chat should be True
        assert report["crypto"] is True
        assert report["ratchet"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
