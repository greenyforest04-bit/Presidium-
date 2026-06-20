# Presidium

A fully decentralized, end-to-end encrypted P2P messenger built with Python.

## Architecture Overview

Presidium implements a pure P2P architecture with no central message server. Every node is equal and communicates directly with peers using UDP-based NAT traversal (hole punching) with relay fallback. The network stack consists of three discovery mechanisms working together: mDNS for local network peers, a simplified Kademlia DHT for global peer lookup, and configurable bootstrap nodes for initial network entry.

**E2EE Handshake:** When two peers establish a session, they perform an X25519 ephemeral key exchange signed with their long-term Ed25519 identity keys. The shared secret is passed through HKDF-SHA256 to derive independent sending and receiving chain keys, implementing a simplified Double Ratchet for forward secrecy. Each message is encrypted with AES-256-GCM using a per-message key derived by advancing the chain key.

**NAT Traversal:** The transport layer implements coordinated UDP hole punching — both peers send probe packets to each other's observed external addresses simultaneously. If direct connection fails after a configurable timeout, the system falls back to relay/bootstrap nodes. STUN servers are used for external address detection.

**Offline Messaging:** Messages for offline peers are stored at trusted "dead drop" nodes in the DHT, encrypted with the recipient's public key. When the recipient comes online, they retrieve and decrypt these stored messages.

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/your-org/presidium.git
cd presidium

# Install dependencies with Poetry
poetry install

# Or with pip
pip install PySide6 cryptography argon2-cffi aiosqlite aiofiles aiortc Pillow msgspec pydantic pydantic-settings qasync
```

### Running

```bash
# With Poetry
poetry run presidium

# Or directly
python -m presidium.main
```

### First Run

1. On first launch, you'll see the **Welcome** screen
2. Enter an optional nickname and a password (min 6 characters)
3. Click **Generate Keys** — this creates your Ed25519 identity and encrypts it with Argon2id
4. Your **User ID** (32 hex chars) is displayed — share it with contacts
5. You're ready to chat!

### Adding a Contact

1. Ask your contact for their User ID and share string (or QR data)
2. In the app, click **+ New Chat** and enter their User ID
3. The DHT will locate them and establish a direct P2P connection
4. An E2EE session is automatically established via key exchange

## Testing Two Peers on localhost

To test with two instances on the same machine:

```bash
# Terminal 1: Start peer A on default port 9478
PRESIDIUM_NET_LISTEN_PORT=9478 poetry run presidium

# Terminal 2: Start peer B on a different port
PRESIDIUM_NET_LISTEN_PORT=9479 poetry run presidium

# In peer B, add peer A's User ID
# Use: localhost:9478 as the address
```

For LAN testing, both instances will discover each other via mDNS automatically.

## Project Structure

```
presidium/
├── presidium/
│   ├── main.py                 # Entry point, Qt app initialization
│   ├── core/
│   │   ├── identity.py         # Ed25519 identity, encrypted storage
│   │   ├── crypto.py           # X25519, AES-GCM, Argon2, secure zeroing
│   │   ├── network/
│   │   │   ├── transport.py    # UDP hole punching + reliable datagram
│   │   │   ├── peer.py         # Peer connection & E2EE session manager
│   │   │   ├── dht.py          # Kademlia DHT (simplified)
│   │   │   ├── protocol.py     # Binary message protocol (msgspec)
│   │   │   └── discovery.py    # mDNS + bootstrap + DHT discovery
│   │   └── messaging/
│   │       ├── chat.py         # Chat session, message management
│   │       ├── file_transfer.py # Chunked transfer with resume
│   │       └── voice.py        # WebRTC voice signaling
│   ├── ui/
│   │   ├── app.py              # MainWindow, screen orchestration
│   │   ├── components/         # Reusable widgets
│   │   │   ├── sidebar.py      # Navigation sidebar
│   │   │   ├── chat_bubble.py  # Message bubble widget
│   │   │   ├── chat_list.py    # Conversation list widget
│   │   │   ├── identicon.py    # Pillow-based avatar generation
│   │   │   └── glass_panel.py  # Glassmorphism container
│   │   ├── screens/            # Application screens
│   │   │   ├── welcome.py      # Onboarding + key generation
│   │   │   ├── chat_list.py    # Conversation list screen
│   │   │   ├── chat_room.py    # Chat room screen
│   │   │   └── settings.py     # Settings screen
│   │   └── styles/             # Theme definitions
│   │       ├── dark_theme.py   # Material You dark theme
│   │       └── light_theme.py  # Material You light theme
│   ├── db/
│   │   ├── models.py           # SQLite schema definitions
│   │   └── manager.py          # Async database manager
│   └── utils/
│       ├── config.py           # Pydantic-based configuration
│       └── validators.py       # Input validation utilities
├── tests/
├── pyproject.toml
└── README.md
```

## Security Model

| Component | Algorithm | Purpose |
|-----------|-----------|---------|
| Identity | Ed25519 | Digital signatures, node identification |
| Key Exchange | X25519 (ephemeral) | ECDH for Perfect Forward Secrecy |
| Encryption | AES-256-GCM | Authenticated message encryption |
| Key Derivation | Argon2id | Password → encryption key |
| Session Keys | HKDF-SHA256 | Shared secret → chain keys |
| Message Integrity | GCM tag (128-bit) | Tamper detection |
| Identity Storage | Argon2id + AES | Encrypted on-disk key storage |

### Security Notes

- **Perfect Forward Secrecy:** Each session generates ephemeral X25519 keys. Compromising a long-term key doesn't expose past messages.
- **Memory Zeroing:** Private key material is overwritten with zeros using `ctypes.memset` when no longer needed.
- **No Hardcoded Secrets:** All configuration flows through pydantic-settings with environment variable support.
- **User ID Derivation:** User IDs are the first 16 bytes of SHA-256(public_key), making them deterministic and verifiable.

## Configuration

All settings can be configured via environment variables with the `PRESIDIUM_` prefix:

```bash
# Network
PRESIDIUM_NET_LISTEN_PORT=9478
PRESIDIUM_NET_MDNS_ENABLED=true
PRESIDIUM_NET_DHT_ENABLED=true

# UI
PRESIDIUM_UI_THEME=dark          # dark or light
PRESIDIUM_UI_ACCENT_COLOR=#6366F1

# Logging
PRESIDIUM_LOG_LEVEL=INFO
PRESIDIUM_LOG_FILE=/tmp/presidium.log
```

## License

MIT
