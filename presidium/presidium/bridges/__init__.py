"""Federation bridges for Presidium.

Provides protocol bridges that allow Presidium users to communicate with
users on external networks (Matrix, XMPP) while preserving end-to-end
encryption semantics within the Presidium network.

Architecture:
    BaseBridge (abstract)  ←  MatrixBridge
                          ←  XmppBridge

Bridge principles:
    1. Presidium → External: Messages are encrypted on the Presidium side,
       then relayed as ciphertext through the bridge. The bridge itself
       never has access to plaintext content.
    2. External → Presidium: Messages arrive unencrypted from the external
       network. The bridge wraps them in a Presidium envelope. If the
       external network supports E2EE (e.g., Megolm in Matrix), the
       bridge can verify and strip the outer encryption layer before
       re-wrapping in Presidium's Double Ratchet.
    3. Bridge identity: Each bridge runs as a special Presidium peer with
       a "bridge" flag. Contact discovery shows bridged contacts with a
       distinct icon.
    4. Rate limiting: Bridges enforce rate limits to prevent flooding the
       Presidium network from external sources.
"""
