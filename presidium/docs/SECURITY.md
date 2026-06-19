# Presidium Security Model

## Threat Model

### Adversaries

| Adversary | Capability | Mitigation |
|-----------|-----------|------------|
| **Network Observer** | Passive eavesdropping on all traffic | E2EE (AES-256-GCM); no plaintext traverses the network |
| **Active Network Attacker** | MITM, packet injection, replay | Ed25519 signature verification on key exchange; GCM authentication tags; replay protection via message numbering |
| **Compromised Peer** | Access to a peer's long-term keys | Perfect Forward Secrecy via ephemeral X25519 keys per session; Double Ratchet limits exposure |
| **Malicious Bootstrap** | Bootstrap node that logs connections | E2EE ensures message content is never visible to relays; bootstrap nodes only see metadata |
| **Correlation Attacker** | Traffic analysis, timing correlation | PHASE2: future padding and cover traffic; currently, message sizes and timing may leak metadata |

### Attack Scenarios

#### MITM during Key Exchange
- **Attack**: Attacker intercepts X25519 key exchange and substitutes their own key
- **Mitigation**: Key exchange messages are signed with long-term Ed25519 keys. Both parties verify the signature before accepting the exchange. User IDs are derived from public keys, making impersonation detectable.

#### Replay Attack
- **Attack**: Attacker replays a previously captured message
- **Mitigation**: Double Ratchet message numbering ensures each message key is used exactly once. The receiving chain advances, making old message keys unavailable.

#### Key Compromise
- **Attack**: Long-term Ed25519 key is compromised
- **Mitigation**: Ephemeral X25519 keys provide Perfect Forward Secrecy. Past sessions remain secure. New sessions should be initiated with a new key pair.

#### Offline Message Access
- **Attack**: Attacker gains access to encrypted dead drops on bootstrap nodes
- **Mitigation**: Dead drops are encrypted with the recipient's public key. Without the recipient's private key, the content is inaccessible.

## Cryptographic Primitives

| Primitive | Algorithm | Key Size | Purpose |
|-----------|-----------|----------|---------|
| Identity | Ed25519 | 256-bit | Digital signatures, node identity |
| Key Exchange | X25519 (ephemeral) | 256-bit | ECDH for forward secrecy |
| Encryption | AES-256-GCM | 256-bit | Authenticated encryption |
| Key Derivation | HKDF-SHA256 | 256-bit | Root key, chain key, message key derivation |
| Password Hashing | Argon2id | Variable | Password to encryption key for identity storage |
| User ID | SHA-256 (truncated) | 128-bit | Deterministic, verifiable identity |

## Perfect Forward Secrecy (PFS)

PFS is achieved through the Double Ratchet protocol:

1. **DH Ratchet**: When either party sends a message after receiving a new DH public key from the peer, a new X25519 key pair is generated and a DH operation is performed. This creates a new root key, breaking the chain of key derivation.

2. **Symmetric Ratchet**: Each message advances the chain key via HKDF, producing a unique message key. Compromising a message key does not expose past or future message keys.

3. **Skipped Keys**: Out-of-order messages are handled by caching up to 1000 skipped message keys. Once used, a skipped key is deleted from the cache.

## Identity Storage

- Private keys are encrypted on disk using AES-256-GCM with a key derived from the user's password via Argon2id (time=3, memory=65536 KiB, parallelism=4).
- A 32-byte random salt is prepended to the encrypted data.
- The password is never stored; only the salt-encrypted key pair is persisted.

## Memory Security

- `SecureBytes` class wraps sensitive key material and zeroing on deletion via `ctypes.memset`.
- Message keys derived from chain keys are zeroed after use (best-effort; Python's memory model limits guarantees).
- `X25519KeyPair.zero_private_key()` attempts to zero the raw private key bytes.

## Constant-Time Operations

- `hmac.compare_digest` is used for MAC comparison to prevent timing attacks.
- Ed25519 signature verification uses the `cryptography` library's constant-time implementation.

## Security Audit Checklist

- [x] All cryptographic operations use well-vetted libraries (`cryptography`, `argon2-cffi`)
- [x] No hardcoded secrets; all configuration via environment variables or pydantic-settings
- [x] Perfect Forward Secrecy via Double Ratchet with ephemeral X25519 keys
- [x] Message authentication via AES-256-GCM (128-bit tag)
- [x] Ed25519 signatures on key exchange messages
- [x] Password-based key derivation uses Argon2id (resistant to GPU/ASIC attacks)
- [x] Secure zeroing of key material where possible
- [ ] Side-channel resistance audit (not yet performed)
- [ ] Formal protocol verification (not yet performed)
- [ ] Third-party security audit (not yet performed)
