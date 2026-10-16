"""Regression tests for confidentiality, authenticated gateways, and release trust."""

import asyncio
import base64
import hashlib
import json
import os
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from presidium.core.messaging.chat import ChatMessage
from presidium.core.messaging.matrix_transport import MatrixTransport, MatrixTransportConfig
from presidium.db.manager import DatabaseManager, DatabaseError
from presidium.release.updater import AutoUpdater, UpdateManifest


@pytest.mark.asyncio
async def test_history_is_encrypted_migrated_and_locked(tmp_path):
    path = tmp_path / "history.db"
    key = os.urandom(32)
    db = DatabaseManager(path)
    await db.initialize()
    message = ChatMessage(
        os.urandom(16), "alice", "bob", "LEGACY_PRIVATE_MARKER_9321", 1.0, is_mine=True
    )
    try:
        with pytest.raises(DatabaseError, match="locked"):
            await db.save_message(message)
        # Simulate an existing plaintext database, including its old FTS index.
        await db._db.execute(
            "INSERT INTO messages(message_id,sender_user_id,recipient_user_id,content,timestamp,is_mine,session_peer_id) VALUES (?,?,?,?,?,1,?)",
            (message.message_id_hex, "alice", "bob", message.content, 1.0, "bob"),
        )
        await db._db.execute("CREATE VIRTUAL TABLE messages_fts USING fts5(content)")
        await db._db.execute("INSERT INTO messages_fts VALUES (?)", (message.content,))
        await db._db.commit()
        await db.unlock_history(key)
        assert (await db.get_messages("bob"))[0].content == message.content
        await db.save_message(
            replace(message, message_id=os.urandom(16), content="NEW_PRIVATE_MARKER_4213")
        )
        db.lock_history()
        with pytest.raises(DatabaseError, match="locked"):
            await db.get_messages("bob")
        with pytest.raises(Exception):
            await db.unlock_history(os.urandom(32))
        await db.unlock_history(key)
        assert len(await db.get_messages("bob")) == 2
    finally:
        await db.close()
    assert b"LEGACY_PRIVATE_MARKER_9321" not in path.read_bytes()
    assert b"NEW_PRIVATE_MARKER_4213" not in path.read_bytes()
    reopened = DatabaseManager(path)
    await reopened.initialize()
    try:
        await reopened.unlock_history(key)
        assert (await reopened.get_last_message("bob")).content in {
            "LEGACY_PRIVATE_MARKER_9321",
            "NEW_PRIVATE_MARKER_4213",
        }
        await reopened._db.execute("UPDATE messages SET sender_user_id = 'mallory'")
        await reopened._db.commit()
        with pytest.raises(Exception):
            await reopened.get_messages("bob")
    finally:
        await reopened.close()


@pytest.mark.asyncio
async def test_updates_require_pinned_signature_and_untampered_metadata(tmp_path):
    package = tmp_path / "update.zip"
    package.write_bytes(b"signed package")
    key = Ed25519PrivateKey.generate()
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    manifest = UpdateManifest(
        version="2.0.0",
        full_package_size=package.stat().st_size,
        full_package_sha256=hashlib.sha256(package.read_bytes()).hexdigest(),
    )
    manifest.full_package_signature = base64.b64encode(
        key.sign(AutoUpdater.signature_payload(manifest))
    ).decode()
    updater = AutoUpdater(current_version="1.0.0", release_public_key=public, install_dir=tmp_path)
    assert await updater._verify_package(package, manifest)
    assert not await AutoUpdater(current_version="1.0.0", install_dir=tmp_path)._verify_package(
        package, manifest
    )
    assert not await updater._verify_package(package, replace(manifest, version="3.0.0"))
    assert not await updater._verify_package(package, replace(manifest, full_package_signature=""))
    package.write_bytes(b"modified package")
    assert not await updater._verify_package(package, manifest)


async def asgi_request(app, path, data, token=None):
    body = json.dumps(data).encode()
    headers = [(b"content-type", b"application/json")]
    if token:
        headers.append((b"authorization", f"Bearer {token}".encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "server": ("test", 80),
        "client": ("127.0.0.1", 1),
        "root_path": "",
    }
    messages = []

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    status = next(m["status"] for m in messages if m["type"] == "http.response.start")
    content = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return status, json.loads(content)


@pytest.mark.asyncio
async def test_gateway_authentication_room_isolation_and_encrypted_submission(monkeypatch):
    import presidium.api as api

    alice, bob = "@alice:local", "@bob:local"
    members = [alice, bob]
    sent = []
    encrypted = {
        "algorithm": "m.megolm.v1.aes-sha2",
        "sender_key": "sender",
        "session_id": "session",
        "device_id": "DEVICE",
        "ciphertext": "ciphertext",
    }

    async def matrix(request):
        if request.headers.get("Authorization") != "Bearer valid":
            return web.json_response({"errcode": "M_UNKNOWN_TOKEN"}, status=401)
        if request.path.endswith("whoami"):
            return web.json_response({"user_id": alice, "device_id": "DEVICE"})
        if request.path.endswith("/state"):
            return web.json_response(
                [
                    {
                        "type": "m.room.encryption",
                        "state_key": "",
                        "content": {"algorithm": encrypted["algorithm"]},
                    }
                ]
                + [
                    {"type": "m.room.member", "state_key": user, "content": {"membership": "join"}}
                    for user in members
                ]
            )
        if "state/m.room.encryption" in request.path:
            return web.json_response({"algorithm": encrypted["algorithm"]})
        if "/send/m.room.encrypted/" in request.path:
            sent.append(await request.json())
            return web.json_response({"event_id": "$actual_server_event"})
        return web.json_response({"errcode": "M_UNRECOGNIZED"}, status=404)

    upstream = web.Application()
    upstream.router.add_route("*", "/{path:.*}", matrix)
    runner = web.AppRunner(upstream)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(
        api,
        "transport",
        lambda token="": MatrixTransport(
            MatrixTransportConfig(homeserver_url=f"http://127.0.0.1:{port}", access_token=token)
        ),
    )
    payload = {"room_id": "!room:local", "recipient": bob, "encrypted_content": encrypted}
    try:
        assert (await asgi_request(api.app, "/v1/messaging/direct", payload))[0] == 401
        assert (await asgi_request(api.app, "/v1/messaging/direct", payload, "invalid"))[0] == 401
        assert (
            await asgi_request(
                api.app,
                "/v1/messaging/direct",
                {**payload, "sender": "mallory", "text": "plaintext"},
                "valid",
            )
        )[0] == 422
        assert (
            await asgi_request(
                api.app,
                "/v1/messaging/direct",
                {**payload, "encrypted_content": {"body": "plaintext"}},
                "valid",
            )
        )[0] == 400
        status, result = await asgi_request(api.app, "/v1/messaging/direct", payload, "valid")
        assert status == 200 and result["event_id"] == "$actual_server_event"
        assert sent == [encrypted]
        members.append("@eve:local")
        assert (await asgi_request(api.app, "/v1/messaging/direct", payload, "valid"))[0] == 400
        assert sent == [encrypted]
    finally:
        await runner.cleanup()


@pytest.mark.asyncio
async def test_desktop_idle_lock_clears_identity_history_and_messages(
    tmp_path, qt_application, monkeypatch
):
    from presidium.ui.app import MainWindow, ScreenIndex
    from presidium.utils.config import AppConfig, UIConfig

    window = MainWindow(AppConfig(data_dir=tmp_path, ui=UIConfig(auto_lock_timeout=1)))
    window._init_timer.stop()
    window._lock_timer.stop()
    monkeypatch.setattr(window, "_start_networking", AsyncMock())
    try:
        await window._async_init()
        await window._identity_manager.create_identity("test-password", "Tester")
        await window._finish_unlock()
        assert window._db_manager._history_key is not None
        window._last_activity -= 2
        window._check_auto_lock()
        await asyncio.sleep(0)
        assert not window._identity_manager.is_unlocked
        assert window._db_manager._history_key is None
        assert window._stack.currentIndex() == ScreenIndex.LOCK
        assert window._stack.widget(ScreenIndex.CHAT_ROOM).current_peer is None
        window._unlock_password.setText("test-password")
        await window._unlock_identity()
        assert window._identity_manager.is_unlocked
        assert window._stack.currentIndex() == ScreenIndex.CHAT_LIST
    finally:
        await window._cleanup_async()
        window.deleteLater()


@pytest.mark.asyncio
async def test_desktop_starts_and_stops_real_network_stack(tmp_path, qt_application):
    import socket
    from presidium.ui.app import MainWindow
    from presidium.utils.config import AppConfig, NetworkConfig

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = AppConfig(
        data_dir=tmp_path,
        network=NetworkConfig(listen_port=port, mdns_enabled=False, bootstrap_nodes=[]),
    )
    window = MainWindow(config)
    window._init_timer.stop()
    window._lock_timer.stop()
    try:
        await window._async_init()
        await window._identity_manager.create_identity("test-password")
        await window._finish_unlock()
        assert window._identity_manager.is_unlocked, window._unlock_error.text()
        assert window._chat_manager is not None
        assert window._transport is not None
        peer_id = "ab" * 16
        await window._on_peer_connected(peer_id)
        assert window._chat_manager.get_session(peer_id) is not None
        assert window._stack.widget(1)._sessions[0].peer_user_id == peer_id
        await window._lock_identity()
        assert window._transport is None
        assert window._chat_manager is None
    finally:
        await window._cleanup_async()
        window.deleteLater()


@pytest.mark.asyncio
async def test_disappearing_messages_require_real_wipe_support():
    from presidium.core.messaging.disappearing import DisappearingMessageScheduler

    deleted = AsyncMock()
    with pytest.raises(ValueError, match="wipe_callback"):
        DisappearingMessageScheduler(deleted, secure_wipe=True)
    wiped = AsyncMock()
    scheduler = DisappearingMessageScheduler(deleted, secure_wipe=True, wipe_callback=wiped)
    scheduler.schedule("message", "peer", 1)
    scheduler._scheduled["message"].expires_at = 0
    await scheduler.start()
    try:
        await asyncio.wait_for(_wait_for_deletion(deleted), 1)
        wiped.assert_awaited_once_with("message", "peer")
        deleted.assert_awaited_once_with("message", "peer")
        assert scheduler.scheduled_count == 0
    finally:
        await scheduler.stop()


async def _wait_for_deletion(callback):
    while not callback.await_count:
        await asyncio.sleep(0)
