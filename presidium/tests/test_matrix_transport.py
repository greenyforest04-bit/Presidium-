from presidium.core.messaging.matrix_transport import MatrixTransport, MatrixTransportConfig


def test_matrix_transport_builds_encrypted_dm_room_payload() -> None:
    transport = MatrixTransport(
        MatrixTransportConfig(
            homeserver_url="https://matrix.example.com",
            access_token="demo-token",
        )
    )

    payload = transport.build_dm_creation_payload(
        local_user_id="@alice:example.com",
        remote_user_id="@bob:example.com",
        room_name="Alice and Bob",
    )

    assert payload["is_direct"] is True
    assert payload["preset"] == "trusted_private_chat"
    assert payload["invite"] == ["@bob:example.com"]

    state_map = {event["type"]: event["content"] for event in payload["initial_state"]}
    assert state_map["m.room.encryption"]["algorithm"] == "m.megolm.v1.aes-sha2"
    assert state_map["m.room.encryption"]["rotation_period_ms"] == 86400000
    assert payload["name"] == "Alice and Bob"


def test_matrix_transport_builds_plaintext_content_for_client_encryption() -> None:
    transport = MatrixTransport(
        MatrixTransportConfig(
            homeserver_url="https://matrix.example.com",
            access_token="demo-token",
        )
    )

    body = transport.build_message_event("PRESIDIUM_E2EE_TEST_SECRET_92841")

    assert body["msgtype"] == "m.text"
    assert body["body"] == "PRESIDIUM_E2EE_TEST_SECRET_92841"
    assert body["org.matrix.msc1767.text"] == "PRESIDIUM_E2EE_TEST_SECRET_92841"
