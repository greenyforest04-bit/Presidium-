# Presidium v1.5 "Prime" Release Announcement

## 🔒 Presidium — Fully Decentralized E2EE P2P Messenger

> **Your messages. Your keys. No servers. No compromises.**

Presidium is a privacy-first, fully decentralized peer-to-peer messenger built with pure Python. No central servers, no phone numbers, no metadata leaks. Every message is end-to-end encrypted using the Double Ratchet protocol — the same algorithm used by Signal — but without any server dependency.

---

## 🆕 What's New in v1.5 "Prime"

### Zero-Knowledge Push Notifications
- **Self-hosted Push Relay (aiohttp)**: Delivers only encrypted blobs — no plaintext ever touches the relay. Recipients poll by pseudorandom blob IDs computed from their own public key, so the relay can't even tell which IDs belong to whom.
- **Blind Fetch**: Recipients compute `SHA-256(public_key || timestamp_bucket)[:16]` to derive expected blob IDs. The relay returns matching encrypted blobs without knowing the recipient's identity.
- **FCM/APNS Fallback**: Sends only a 16-byte encrypted_payload_id via push providers. The client then performs a blind fetch to retrieve the actual E2EE payload.
- **Rate Limiting**: 100 pushes/hour/IP, 600 pulls/hour/IP with sliding window enforcement.

### Voice Messages
- **OPUS-encoded recording**: Configurable bitrate (6-32 kbps), max 5-minute duration. Waveform extraction for visual preview with 64 amplitude bars.
- **Streaming Playback**: Start listening before the full download completes. Speed control (0.5x - 2.0x) with position scrubbing.
- **Waveform Widget**: Interactive PySide6 widget showing amplitude bars with playback progress. Click to seek.

### Location Sharing
- **Precision Control**: Four levels — Exact, Building (~100m), Neighborhood (~1km), City (~10km). Precision reduction is applied CLIENT-SIDE before encryption.
- **Live Location**: Periodic updates at configurable intervals (15min, 30min, 1hr, 8hr, 24hr durations). Automatic expiry.
- **Point of Interest**: Share named locations with labels.

### Polls & Voting
- **End-to-End Encrypted Polls**: Poll definitions encrypted with Sender Keys (group), votes encrypted with creator's public key. Only the creator sees individual votes.
- **Anonymous Mode**: Even the poll creator cannot link votes to voters.
- **Secret Ballot**: Commit-reveal scheme using SHA-256 hash commitments.
- **Single/Multiple Choice**: Configurable poll types with vote change option.

### Internationalization (15+ Languages)
- **Full i18n Framework**: `_()` translation function with interpolation and plural forms. CLDR plural rules for English, Russian, Chinese, Japanese, Korean, German, French, Spanish, Portuguese, Arabic, Hindi, Turkish, Farsi, Ukrainian.
- **RTL Support**: Right-to-left layout for Arabic, Farsi, and Hebrew. Bidirectional text markers for mixed content.
- **Lazy Locale Loading**: JSON locale files loaded on demand. English and Russian translations included.

### Accessibility
- **Screen Reader Support**: Qt Accessibility integration with `setAccessibleName()` on all interactive widgets. Rich descriptions for chat bubbles, contacts, and voice messages.
- **High Contrast Modes**: Dark and Light high contrast themes with customizable focus indicators.
- **Font Scaling**: 75% to 200% font size scaling. Color blind palette transformations (Protanopia, Deuteranopia, Tritanopia).
- **Reduced Motion**: Three levels — Full, Reduced (shorter durations), None (all animations disabled).
- **Keyboard Navigation**: Enhanced focus chains with configurable shortcuts.

### Semantic Search
- **AI-Powered Message Search**: Natural language queries using sentence-transformers embeddings (all-MiniLM-L6-v2, 384 dimensions). Falls back to character n-gram embeddings without ML dependencies.
- **FAISS Index**: Fast similarity search with incremental background indexing. Hybrid search combining semantic similarity with FTS5 keyword matching.
- **Cross-Lingual**: Query in one language, find messages in another.

### Smart Folders
- **Automatic Message Categorization**: Virtual folders with compound filter rules (keyword, peer, group, time period, read status, message type, tags).
- **Built-in Folders**: Unread, Groups, Mentions, Files, Today.
- **Custom Folders**: Create with AND/OR rule logic. Pin to sidebar.

### Contact Discovery v2
- **Blind Signature Protocol**: RSA blind signatures for privacy-preserving contact matching. The server signs blinded contact tokens without seeing plaintext contact info.
- **Privacy**: Server never learns which contacts you're checking. Only mutual contacts produce matching signatures.

### Data Import
- **Signal Import**: Parse Signal JSON exports with full conversation history and attachments.
- **WhatsApp Import**: Parse `_chat.txt` exports with US/EU date formats and multi-line messages.
- **Telegram Import**: Parse `result.json` with stickers, animations, and voice messages.
- **Validation Pipeline**: HTML sanitization, control character stripping, length limits, timestamp validation.

### White-Label SDK
- **Simple API**: `PresidiumSDK` class with `initialize()`, `send_message()`, `receive_messages()`, `create_group()`, `get_contacts()`.
- **Thread-Safe**: Async wrappers with sync convenience methods. Event bus for handler registration.
- **Configurable**: App name, relay URL, timeout, reconnection, encryption settings.

### Virtualized Lists
- **Efficient Rendering**: Only visible items + buffer are rendered. Widget recycling with free-list. Variable-height support.
- **Smooth Scrolling**: QPropertyAnimation-based smooth scroll with `scroll_to_bottom()` for chat views.

### Onboarding v2
- **5-Step Setup Wizard**: Welcome → Create Identity → Recovery Phrase (12-word BIP39-like) → Profile Setup → Ready.
- **Password Strength Indicator**: Real-time validation with visual feedback.
- **Recovery Phrase**: Crypto-secure word selection with save confirmation gate.

### E2E Test Suite v1.5
- **15 Test Classes**: Crypto, Double Ratchet, DHT, Discovery v2, Push Relay, Voice Messages, Location, Polls, i18n, a11y, Search, Import, SDK, Virtual Lists, Welcome v2.
- **100+ Test Cases**: Covering all Phase 6 features with pytest-asyncio.

---

## ✨ What Makes Presidium Different

| Feature | Presidium | Signal | Telegram | Matrix |
|---------|-----------|--------|----------|--------|
| E2EE by default | ✅ | ✅ | ❌ (opt-in) | ✅ |
| No central server | ✅ | ❌ | ❌ | ❌ |
| Post-quantum crypto | ✅ ML-KEM-768 | ❌ | ❌ | ❌ |
| Onion routing | ✅ 3-hop | ❌ | ❌ | ❌ |
| Sealed sender | ✅ | ✅ | ❌ | ❌ |
| Federation bridges | ✅ Matrix + XMPP | ❌ | ❌ | ✅ |
| Local AI assistant | ✅ On-device LLM | ❌ | ❌ | ❌ |
| Group calls | ✅ P2P SFU | ✅ | ✅ | ✅ |
| Plugin system | ✅ | ❌ | ❌ | ❌ |
| Self-hostable | ✅ (it's P2P!) | ❌ | ❌ | ✅ |
| Open source | ✅ AGPL-3.0 | ✅ | ❌ | ✅ |

---

## 🚀 Key Features

### Absolute Privacy (Phase 5)
- **Sealed Sender**: Recipients can read your messages, but relays can't even tell who sent them. Uses HPKE (RFC 9180) with X25519 key encapsulation.
- **3-Hop Onion Routing**: Messages traverse guard → middle → exit nodes with layered AES-256-GCM encryption. Each relay only knows its immediate neighbor.
- **Cover Traffic**: Poisson-distributed dummy messages with adaptive rate matching hide your real traffic pattern from network observers. All messages are padded to power-of-2 sizes.

### Post-Quantum Cryptography
- **Hybrid Key Exchange**: X25519 + ML-KEM-768 (Kyber) — security even if quantum computers break classical ECC. Falls back gracefully if PQ library unavailable.
- **LMS Signatures**: Stateful hash-based signatures for master identity keys — quantum-resistant, only relying on hash function security.
- **Crypto Agility**: Versioned crypto suites (v1 classical, v2 hybrid PQ, v3 future) with automatic negotiation during handshake.

### Group Voice/Video Calls
- **P2P SFU Architecture**: No central media server. The call host acts as a lightweight Selective Forwarding Unit for 4-16 participants, with mesh mode for ≤3.
- **Simulcast**: 3 quality layers (low/mid/high) with adaptive bitrate switching per participant.
- **Screen Sharing**: Share your screen with the group, one participant at a time.
- **Call Recording + Transcription**: Local recording (OPUS/OGG) with offline Whisper transcription. Consent required from all participants.

### Seamless Multi-Device
- **X3DH Between Devices**: Each device has its own prekey bundle signed by the master Ed25519 key. Encrypted history sync when adding new devices.
- **CRDT Sync**: G-Counter for read receipts, LWW-Register for settings, OR-Set for delivery status — all merge without conflicts.
- **Draft Handoff**: Start typing on your phone, continue on desktop. Incoming calls ring everywhere, answer on one device silences others.

### On-Device AI Assistant
- **Local LLM**: Powered by llama.cpp (LLaMA-3-8B-GGUF, 4-bit quantized, ~5GB RAM). No cloud, no data leaving your device.
- **Smart Replies**: 3 context-aware reply suggestions generated in <2 seconds.
- **Summarization**: Summarize the last 50 messages in a group chat.
- **Translation**: On-device translation using opus-mt models (16 language pairs).
- **Draft Enhancement**: Polish your messages (polite, concise, formal, or casual).

### Federation Bridges
- **Matrix Bridge**: Bidirectional relay with any Matrix homeserver. Supports text, reactions, typing indicators, and media.
- **XMPP Bridge**: Connect to XMPP servers with full protocol support including MUC group chats and XEP-0444 reactions.

### Plugin System
- **BasePlugin API**: Hook into 18+ event types with permission-based access control.
- **Stickers Plugin**: Install and manage sticker packs, search by emoji/keyword.
- **Bot API**: Create chat bots with command routing, rate limiting, and inline handlers.
- **Scheduled Messages**: Schedule messages with natural language time parsing, recurring delivery, and cancellation.

### Relay Network
- **Reputation Marketplace**: Run a relay node and earn reputation for uptime, bandwidth, and storage. Non-transferable, anti-Sybil via proof-of-uptime attestations.
- **Priority Routing**: Higher-score relays are preferred for onion circuits and message delivery.

---

## 📦 Installation

### From Source (Recommended)
```bash
git clone https://github.com/presidium-chat/presidium.git
cd presidium
pip install -e ".[all]"
presidium
```

### Pre-built Binaries (Coming Soon)
```bash
# Linux
wget https://github.com/presidium-chat/presidium/releases/latest/download/presidium-linux-x86_64.AppImage
chmod +x presidium-linux-x86_64.AppImage
./presidium-linux-x86_64.AppImage

# macOS
brew install --cask presidium

# Windows
winget install Presidium.Presidium
```

### Docker
```bash
docker run -v ~/.presidium:/data presidium/presidium:latest
```

---

## 🔐 Security Audit Summary

| Component | Status | Verification |
|-----------|--------|-------------|
| Double Ratchet | ✅ Proven | Tamarin Prover: forward secrecy, break-in recovery, authentication |
| X3DH Key Exchange | ✅ Proven | ProVerif: mutual authentication, key secrecy |
| AES-256-GCM | ✅ Standard | NIST SP 800-38D |
| ML-KEM-768 | ✅ Standardized | NIST FIPS 203 |
| Sealed Sender | ✅ Reviewed | HPKE RFC 9180 compliance |
| Onion Routing | ✅ Reviewed | 3-hop layered encryption |
| Formal Models | ✅ Verified | 5 Tamarin lemmas + 3 ProVerif queries proven |

Full security proof: `docs/formal/SECURITY_PROOF.md`

---

## 🧪 Testing

```bash
# Unit tests
make test

# Phase 5 E2E tests
make e2e

# Performance benchmarks (100-node simulated network)
make benchmark-v2

# Build all platform installers
make build-all
```

---

## 🏗️ Architecture Overview

```
┌─────────────────────────────────────────────────┐
│                   Presidium v1.0                │
├──────────┬──────────┬──────────┬────────────────┤
│   Core   │ Messaging│  Network │    Enterprise  │
│──────────│──────────│──────────│────────────────│
│ Ed25519  │ Double   │ UDP      │ Admin          │
│ X25519   │ Ratchet  │ Hole     │ Dashboard      │
│ ML-KEM   │ Sealed   │ Punching │ Backup/        │
│ LMS      │ Sender   │ DHT      │ Restore        │
│ AES-GCM  │ Onion    │ mDNS     │ Contact        │
│ HKDF     │ Routing  │ Onion    │ Verification   │
│ Argon2id │ Cover    │ Pool     │ Audit Log      │
├──────────┼──────────┼──────────┼────────────────┤
│    AI    │   UI     │ Bridges  │   Plugins      │
│──────────│──────────│──────────│────────────────│
│ Local    │ PySide6  │ Matrix   │ Stickers       │
│ LLM      │ Material │ XMPP     │ Bot API        │
│ Whisper  │ You      │          │ Scheduled      │
│ opus-mt  │ Mobile   │          │                │
├──────────┴──────────┴──────────┴────────────────┤
│              P2P Transport Layer                │
│        (UDP + NAT Traversal + TCP Fallback)     │
└─────────────────────────────────────────────────┘
```

---

## 📊 Project Statistics

| Phase | Lines of Code | Files | Key Modules |
|-------|--------------|-------|-------------|
| Phase 1 | ~8,000 | 25+ | P2P, E2EE, Qt6 UI, DHT, mDNS |
| Phase 2 | ~12,000 | 54 | Double Ratchet, Kademlia, Groups, Docker |
| Phase 3 | ~15,000 | 65 | WebRTC, Connection Pool, Sharded DB, Audit |
| Phase 4 | ~10,900 | 30 | Bridges, Mobile UI, Plugins, Formal Verif. |
| Phase 5 | ~12,000 | 14 | Sealed Sender, Onion, PQ Crypto, AI, Relay |
| Phase 6 | ~14,000 | 20 | Push, Voice Msg, Location, Polls, i18n, a11y, Search, Import, SDK |
| **Total** | **~72,000** | **~208** | **Full platform** |

---

## 🤝 Contributing

Presidium is open source under AGPL-3.0. Contributions welcome:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing`)
5. Open a Pull Request

---

## 📜 License

GNU Affero General Public License v3.0 (AGPL-3.0)

---

*Privacy is not a feature — it's a foundation.* 🔒
