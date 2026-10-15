import os, sys
sys.path.insert(0, r'd:\Presidium\presidium')
from presidium.core.messaging.ratchet import DoubleRatchet, decrypt_header
from presidium.core.crypto import X25519KeyPair
shared_secret = os.urandom(32)
alice = DoubleRatchet()
alice_pub = alice.init_as_initiator(shared_secret, X25519KeyPair().public_key_bytes)
bob = DoubleRatchet()
bob.init_as_responder(shared_secret, alice_pub)
print('alice send_header', alice._state.send_header_key.hex())
print('bob recv_header', bob._state.recv_header_key.hex())
print('header eq', alice._state.send_header_key == bob._state.recv_header_key)
print('alice send_next', alice._state.send_next_header_key.hex())
print('bob recv_next', bob._state.recv_next_header_key.hex())
print('next eq', alice._state.send_next_header_key == bob._state.recv_next_header_key)
enc_h, enc_b = alice.encrypt(b'Hello')
print('enc_h len', len(enc_h), 'enc_b len', len(enc_b))
for label, key in [('recv_header', bob._state.recv_header_key), ('recv_next', bob._state.recv_next_header_key)]:
    try:
        hdr = decrypt_header(enc_h, key)
        print(label, 'ok', hdr.message_number, hdr.previous_chain_length)
    except Exception as e:
        print(label, 'fail', e)
try:
    pt = bob.decrypt(enc_h, enc_b)
    print('bob decrypt ok', pt)
except Exception as e:
    print('bob decrypt fail', type(e).__name__, e)
