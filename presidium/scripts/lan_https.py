"""Private-LAN TLS reverse proxy for the existing Next application.

Uses cryptography's X.509 implementation. Trust local-ca.crt on each test device;
never distribute local-ca.key. No public tunnel, HTTP fallback or TLS bypass.
"""
import argparse
import datetime as dt
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
from pathlib import Path
import ssl
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--host', required=True, help='Actual RFC1918 LAN address of this PC')
parser.add_argument('--port', type=int, default=5443)
args = parser.parse_args()
address = ipaddress.ip_address(args.host)
if not any(address in ipaddress.ip_network(n) for n in ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16']):
    parser.error('Use a private IPv4 LAN address, not a public or wildcard address')
directory = root / '.cache/lan-tls'
directory.mkdir(parents=True, exist_ok=True)
now = dt.datetime.now(dt.timezone.utc)
key_path, ca_path = directory/'local-ca.key', directory/'local-ca.crt'
if key_path.exists() and ca_path.exists():
    ca_key = serialization.load_pem_private_key(key_path.read_bytes(), None)
    ca = x509.load_pem_x509_certificate(ca_path.read_bytes())
else:
    ca_key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'Presidium local test CA')])
    ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key())
          .serial_number(x509.random_serial_number()).not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(days=365))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
          .sign(ca_key, hashes.SHA256()))
    key_path.write_bytes(ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
key = ec.generate_private_key(ec.SECP256R1())
cert = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, args.host)]))
        .issuer_name(ca.subject).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-dt.timedelta(minutes=5)).not_valid_after(now+dt.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(address)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False).sign(ca_key, hashes.SHA256()))
server_key, server_cert = directory/'server.key', directory/'server.crt'
server_key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
server_cert.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

class Proxy(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *_):
        pass  # No request bodies, credentials or query strings in logs.

    def proxy(self):
        if self.headers.get('Host') != f'{args.host}:{args.port}':
            self.send_error(400); return
        if self.headers.get('Transfer-Encoding'):
            self.send_error(400); return
        size = int(self.headers.get('Content-Length', '0'))
        if size > 26_000_000:
            self.send_error(413); return
        skip = {'connection','transfer-encoding','keep-alive','proxy-connection','upgrade'}
        headers = {k:v for k,v in self.headers.items() if k.lower() not in skip}
        # These are never forwarded from untrusted clients.
        for k in list(headers):
            if k.lower().startswith('x-forwarded-'): del headers[k]
        connection = http.client.HTTPConnection('127.0.0.1', 5173, timeout=90)
        try:
            connection.request(self.command, self.path, self.rfile.read(size) if size else None, headers)
            response = connection.getresponse()
            data = response.read()
            self.send_response(response.status)
            for k,v in response.getheaders():
                if k.lower() not in skip|{'content-length'}: self.send_header(k,v)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Connection', 'close')
            self.end_headers()
            self.wfile.write(data)
        except (OSError, http.client.HTTPException):
            self.send_error(502)
        finally:
            connection.close()
            self.close_connection = True
    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_HEAD = proxy

server = ThreadingHTTPServer((args.host, args.port), Proxy)
context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
context.minimum_version = ssl.TLSVersion.TLSv1_2
context.load_cert_chain(server_cert, server_key)
server.socket = context.wrap_socket(server.socket, server_side=True)
print(f'Presidium LAN: https://{args.host}:{args.port}', flush=True)
print(f'Trust public CA certificate on each test device: {ca_path}', flush=True)
print('CA SHA256: '+ca.fingerprint(hashes.SHA256()).hex(), flush=True)
try:
    server.serve_forever()
except KeyboardInterrupt:
    pass
finally:
    server.server_close()
