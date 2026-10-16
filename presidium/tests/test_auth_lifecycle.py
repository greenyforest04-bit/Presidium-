"""Live homeserver tests with isolated metadata DBs; no mocked Matrix replies."""
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import sqlite3
import time
import uuid
import urllib.error
import urllib.request

import pytest


class Response:
    def __init__(self, status, data):
        self.status_code, self.text = status, data.decode()
    def json(self):
        return json.loads(self.text)


class API:
    def __init__(self, url):
        self.url = url
    def request(self, method, path, headers=None, json=None):
        body = globals()['json'].dumps(json).encode() if json is not None else None
        request = urllib.request.Request(self.url+path, data=body, method=method,
            headers={'Content-Type':'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return Response(response.status, response.read())
        except urllib.error.HTTPError as error:
            return Response(error.code, error.read())
    def get(self, path, **kwargs):
        return self.request('GET', path, **kwargs)
    def post(self, path, **kwargs):
        return self.request('POST', path, **kwargs)


@pytest.fixture
def live(tmp_path):
    try:
        urllib.request.urlopen('http://localhost:8008/_matrix/client/versions', timeout=3).close()
    except (OSError, urllib.error.URLError):
        pytest.skip('Live Matrix homeserver is not available on localhost:8008')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); port=sock.getsockname()[1]
    client=API(f'http://127.0.0.1:{port}')
    environment={**os.environ, 'PRESIDIUM_PRODUCT_DATA_DIR':str(tmp_path), 'PRESIDIUM_MATRIX_HOMESERVER_URL':'http://localhost:8008'}
    process=None
    def restart():
        nonlocal process
        if process is not None:
            process.terminate(); process.wait(timeout=10)
        process=subprocess.Popen([sys.executable,'-m','uvicorn','presidium.api:app','--host','127.0.0.1','--port',str(port),'--log-level','warning'],
            cwd=Path(__file__).resolve().parents[1],env=environment,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            try:
                if client.get('/health').status_code==200:return
            except OSError:pass
            time.sleep(.1)
        raise RuntimeError('Isolated authentication API did not start')
    client.restart=restart
    restart()
    try:yield client,tmp_path
    finally:
        if process is not None:process.terminate(); process.wait(timeout=10)


def register(client, username=None):
    username = username or 'auth_server_' + uuid.uuid4().hex[:12]
    password = uuid.uuid4().hex
    response = client.post('/v1/auth/register', json={'username': username, 'password': password, 'display_name': 'Server test'})
    assert response.status_code == 200, response.text
    return username, password, response.json()


def headers(session):
    return {'Authorization': 'Bearer ' + session['access_token']}


def test_expiry_and_revocation_reach_homeserver(live):
    client, directory = live
    _, _, session = register(client)
    assert client.get('/v1/auth/session', headers=headers(session)).status_code == 200
    with sqlite3.connect(directory/'auth.sqlite3') as db:
        db.execute('UPDATE web_sessions SET expires_at=? WHERE token_hash=?',
                   (int(time.time())-1, hashlib.sha256(session['access_token'].encode()).hexdigest()))
    assert client.get('/v1/auth/policy', headers=headers(session)).status_code == 401
    assert client.get('/v1/auth/session', headers=headers(session)).status_code == 401
    request = urllib.request.Request('http://localhost:8008/_matrix/client/v3/account/whoami', headers=headers(session))
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 401


def test_concurrent_unique_registration_and_persistent_rate_limits(live):
    client, directory = live
    username, password = 'auth_race_' + uuid.uuid4().hex[:12], uuid.uuid4().hex
    def create(index):
        return client.post('/v1/auth/register', json={'username': username.upper(), 'password': password+str(index), 'display_name': 'Race'})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(create, range(2)))
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = next(r.json() for r in results if r.status_code == 200)
    assert winner['user_id'] == '@'+username+':presidium.local'
    winning_index=next(i for i,r in enumerate(results) if r.status_code==200)
    assert client.post('/v1/auth/login',json={'username':username,'password':password+str(winning_index)}).status_code==200
    for _ in range(9):
        response = client.post('/v1/auth/login', json={'username': username, 'password': password+str(1-winning_index)})
        assert response.status_code == 403
        assert 'incorrect-password' not in response.text
    assert client.post('/v1/auth/login', json={'username': username, 'password': password}).status_code == 429
    # The limit is persisted, not reset by constructing another application client.
    client.restart()
    assert client.post('/v1/auth/login', json={'username': username, 'password': password}).status_code == 429
    assert (directory/'auth.sqlite3').exists()


def test_reauth_required_and_foreign_devices_are_not_authority(live):
    client, _ = live
    username, password, alice = register(client)
    _, _, bob = register(client)
    second = client.post('/v1/auth/login', json={'username': username, 'password': password}).json()
    assert second['device_id'] != alice['device_id']
    forged = {**headers(alice), 'x-presidium-user-id': bob['user_id']}
    assert client.get('/v1/auth/devices', headers=forged).json()['user_id'] == alice['user_id']
    assert client.post('/api/account',headers=forged,json={'action':'revoke','sessionId':bob['device_id']}).status_code==410
    assert client.post('/v1/auth/devices', headers=forged, json={'current_password': password, 'device_id': bob['device_id']}).status_code == 404
    assert client.post('/v1/auth/password', headers=headers(alice), json={'current_password': 'incorrect', 'new_password': uuid.uuid4().hex}).status_code == 403
    assert client.post('/v1/auth/devices', headers=headers(alice), json={'current_password': 'incorrect'}).status_code == 403
    assert client.get('/v1/auth/session', headers=headers(second)).status_code == 200
    assert client.post('/v1/auth/devices', headers=headers(alice), json={'current_password': password}).status_code == 200
    assert client.get('/v1/auth/session', headers=headers(second)).status_code == 401
    assert client.get('/v1/auth/session', headers=headers(alice)).status_code == 200


def test_lost_registration_response_resumes_same_identity_and_device(live):
    client, _ = live
    data={'username':'auth_replay_'+uuid.uuid4().hex[:12], 'password':uuid.uuid4().hex,
          'display_name':'Replay', 'idempotency_key':uuid.uuid4().hex}
    first=client.post('/v1/auth/register',json=data)
    assert first.status_code==200
    # Treat the first reply as lost: retry with the same attempt ID and credentials.
    second=client.post('/v1/auth/register',json=data)
    assert second.status_code==200
    assert second.json()['user_id']==first.json()['user_id']
    assert second.json()['device_id']==first.json()['device_id']
    assert client.get('/v1/auth/session',headers=headers(second.json())).status_code==200
    conflict=client.post('/v1/auth/register',json={**data,'idempotency_key':uuid.uuid4().hex})
    assert conflict.status_code==409
