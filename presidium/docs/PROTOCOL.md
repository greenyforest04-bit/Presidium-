# Presidium Protocol Specification

## Overview

Presidium uses a binary protocol over UDP for P2P communication. All messages follow a common envelope format with msgspec (MessagePack) serialization for payloads.

## Wire Format

```
[4 bytes: magic "PSPM"] [1 byte: version] [1 byte: msg_type]
[8 bytes: timestamp (double)] [4 bytes: payload_length]
[N bytes: payload (msgspec-encoded)]
```

- **Magic**: `PSPM` (0x5053504D)
- **Version**: Currently `1`
- **Timestamp**: IEEE 754 double (seconds since epoch)
- **Max payload**: 65536 bytes (64 KiB)

## Message Types

### Handshake (1-4)

| Type | ID | Direction | Description |
|------|-----|-----------|-------------|
| HELLO | 1 | Both | Initial handshake with identity |
| HELLO_ACK | 2 | Both | Handshake acceptance |
| PUNCH | 3 | Both | NAT hole-punching probe |
| PUNCH_ACK | 4 | Both | Hole-punching confirmation |

### Key Exchange (10-11)

| Type | ID | Direction | Description |
|------|-----|-----------|-------------|
| KEY_EXCHANGE | 10 | Initiator | X25519 ephemeral key + Ed25519 signature |
| KEY_EXCHANGE_ACK | 11 | Responder | X25519 ephemeral key + Ed25519 signature |

### Chat (20-25)

| Type | ID | Direction | Description |
|------|-----|-----------|-------------|
| CHAT_MESSAGE | 20 | Both | Encrypted message with Double Ratchet |
| CHAT_ACK | 21 | Both | Delivery receipt (received/delivered/read) |
| CHAT_READ | 22 | Both | Mark messages as read |
| TYPING_START | 23 | Both | User started typing |
| TYPING_STOP | 24 | Both | User stopped typing |
| REACTION | 25 | Both | Emoji reaction to a message |

### DHT (30-37)

| Type | ID | Direction | Description |
|------|-----|-----------|-------------|
| DHT_PING | 30 | Both | DHT liveness check |
| DHT_PING_ACK | 31 | Both | DHT ping response |
| DHT_FIND_NODE | 32 | Both | Find k closest nodes to target |
| DHT_FIND_NODE_RESP | 33 | Both | Response with node list |
| DHT_FIND_VALUE | 34 | Both | Retrieve a DHT value by key |
| DHT_FIND_VALUE_RESP | 35 | Both | Value or closest nodes |
| DHT_STORE | 36 | Both | Store a key-value pair |
| DHT_STORE_ACK | 37 | Both | Store confirmation |

### File Transfer (40-44)

| Type | ID | Direction | Description |
|------|-----|-----------|-------------|
| FILE_OFFER | 40 | Sender | Offer a file for transfer |
| FILE_ACCEPT | 41 | Receiver | Accept file (with resume offset) |
| FILE_CHUNK | 42 | Sender | File data chunk with SHA-256 |
| FILE_CHUNK_ACK | 43 | Receiver | Chunk received confirmation |
| FILE_DONE | 44 | Both | Transfer complete/failure |

### Voice (50-53)

| Type | ID | Direction | Description |
|------|-----|-----------|-------------|
| VOICE_OFFER | 50 | Caller | WebRTC SDP offer |
| VOICE_ANSWER | 51 | Callee | WebRTC SDP answer |
| VOICE_ICE_CANDIDATE | 52 | Both | ICE candidate exchange |
| VOICE_HANGUP | 53 | Both | Call termination |

### Keepalive (60-63)

| Type | ID | Direction | Description |
|------|-----|-----------|-------------|
| PING | 60 | Both | Keepalive ping |
| PONG | 61 | Both | Keepalive pong |
| DISCONNECT | 62 | Both | Graceful disconnect |
| ERROR | 63 | Both | Error response |

## Handshake Flow

```
Alice                          Bob
  |---- HELLO ---------------->|
  |<--- HELLO_ACK -------------|
  |                             |
  |---- KEY_EXCHANGE --------->|  (X25519 ephemeral + Ed25519 sig)
  |<--- KEY_EXCHANGE_ACK ------|  (X25519 ephemeral + Ed25519 sig)
  |                             |
  |==== E2EE SESSION ESTABLISHED ====
  |                             |
  |---- CHAT_MESSAGE --------->|  (Double Ratchet encrypted)
  |<--- CHAT_ACK --------------|
```

## NAT Traversal Flow

```
Alice (NAT)                   Bob (NAT)
  |---- PUNCH --------------->|  (to Bob's external addr)
  |<--- PUNCH ----------------|  (to Alice's external addr)
  |                             |
  |<--- PUNCH_ACK ------------|  (Bob sees Alice's punch)
  |---- PUNCH_ACK ----------->|  (Alice sees Bob's punch)
  |                             |
  |==== HOLE PUNCHED ============|
  |                             |
  |---- HELLO ---------------->|  (direct communication)
```

## Double Ratchet Message Format

Messages encrypted with the Double Ratchet have this structure:

```
[4 bytes: encrypted_header_length]
[encrypted_header_length bytes: AES-256-GCM(header_key, header)]
[remaining bytes: AES-256-GCM(message_key, plaintext, aad=raw_header)]
```

### Header Structure (before encryption)

```
[32 bytes: dh_public_key]
[4 bytes: message_number (uint32 big-endian)]
[4 bytes: previous_chain_length (uint32 big-endian)]
```

## DHT RPC Protocol

### FIND_NODE

```
Request:
  sender_id: bytes(16)   — requester's user ID
  target_id: bytes(16)   — target node ID to find
  sender_addr: string    — requester's host:port

Response:
  sender_id: bytes(16)   — responder's user ID
  target_id: bytes(16)   — same target ID
  nodes: list of {user_id, addr, public_key}
```

### STORE

```
Request:
  sender_id: bytes(16)   — storer's user ID
  key: bytes(16)         — DHT key
  value: bytes           — value to store
  ttl: int               — time-to-live in seconds
  signature: bytes       — Ed25519 signature of (key + value)

Response:
  key: bytes(16)         — same key
  stored: bool           — whether the store succeeded
  error_msg: string      — error if not stored
```

## Contact URI Format

```
presidium://<user_id>:<base64url_ed25519_pubkey>[:<base64url_x25519_prekey>]
```

- `user_id`: 32 hex characters (first 16 bytes of SHA-256(Ed25519 public key))
- `ed25519_pubkey`: base64url-encoded 32-byte Ed25519 public key (no padding)
- `x25519_prekey`: optional base64url-encoded 32-byte X25519 prekey

Example:
```
presidium://a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4:ABCDEFGHijklmnopQRSTUVWXYZ0123456789abcd
```
