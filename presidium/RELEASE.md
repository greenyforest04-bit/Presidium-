# Presidium v1.0 Release Announcement

## 🔒 Presidium — Fully Decentralized E2EE P2P Messenger

> **Your messages. Your keys. No servers. No compromises.**

Presidium is a privacy-first, fully decentralized peer-to-peer messenger built with pure Python. No central servers, no phone numbers, no metadata leaks. Every message is end-to-end encrypted using the Double Ratchet protocol — the same algorithm used by Signal — but without any server dependency.

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
| **Total** | **~58,000** | **~188** | **Full platform** |

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
