"""Push notifications for Presidium — privacy-preserving delivery.

Provides:
    - Zero-Knowledge Push Relay: self-hosted aiohttp server
    - Push Client: blind fetch + FCM/APNS fallback
    - Local notification management

Architecture:
    Sender → encrypts push blob → Push Relay (stores by random ID)
    Recipient → periodically fetches blobs by blind ID → decrypts

The relay sees only encrypted blobs with pseudorandom IDs.
It cannot determine sender, recipient, or content.
"""
