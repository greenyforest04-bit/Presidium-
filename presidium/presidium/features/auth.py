"""Web credential boundary. Matrix owns users, unique logins and password hashes.

SQLite contains only session fingerprints/expiry and bounded rate-limit buckets.
No password or reusable access token is stored here.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid
from urllib.parse import quote

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field

router = APIRouter(prefix="/v1/auth")
bearer = HTTPBearer(auto_error=False)


def database():
    directory = Path(os.environ.get("PRESIDIUM_PRODUCT_DATA_DIR", ".data/product"))
    directory.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(directory / "auth.sqlite3", timeout=20)
    db.row_factory = sqlite3.Row
    db.executescript("""
    CREATE TABLE IF NOT EXISTS web_sessions(
      token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL, device_id TEXT NOT NULL,
      created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER);
    CREATE INDEX IF NOT EXISTS web_sessions_owner ON web_sessions(user_id,device_id);
    CREATE TABLE IF NOT EXISTS auth_limits(bucket TEXT PRIMARY KEY, count INTEGER NOT NULL,
      reset_at INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS registration_jobs(
      login TEXT PRIMARY KEY, request_hash TEXT NOT NULL, state TEXT NOT NULL,
      started_at INTEGER NOT NULL, user_id TEXT, device_id TEXT);
    """)
    return db


def fingerprint(token):
    return hashlib.sha256(token.encode()).hexdigest()


def check_expiry(token):
    with database() as db:
        row = db.execute("SELECT * FROM web_sessions WHERE token_hash=?", (fingerprint(token),)).fetchone()
    if row and (row["revoked_at"] or row["expires_at"] <= int(time.time())):
        raise HTTPException(401, "Сеанс завершён. Войдите снова.")


async def enforce_expiry(token):
    try:
        check_expiry(token)
    except HTTPException:
        # An expired web session must also stop authorizing direct Matrix requests.
        try:
            await matrix("POST", "logout", token, {})
        except HTTPException:
            pass
        raise


def remember(result):
    now = int(time.time())
    expiry = now + int(os.environ.get("PRESIDIUM_SESSION_SECONDS", "2592000"))
    with database() as db:
        db.execute("INSERT INTO web_sessions VALUES(?,?,?,?,?,NULL) ON CONFLICT(token_hash) DO NOTHING",
                   (fingerprint(result["access_token"]), result["user_id"], result["device_id"], now, expiry))
    return result


def rate_limit(request: Request, operation: str, login=""):
    # Never trust X-Forwarded-For supplied by a browser. Next supplies no identity headers.
    peer = request.client.host if request.client else "unknown"
    now = int(time.time())
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("DELETE FROM auth_limits WHERE reset_at<=?", (now,))
        for key, maximum in [(peer, 100), (peer + ":" + login, 10)]:
            bucket = fingerprint(operation + ":" + key)
            row = db.execute("SELECT count FROM auth_limits WHERE bucket=?", (bucket,)).fetchone()
            if row and row[0] >= maximum:
                raise HTTPException(429, "Слишком много попыток. Повторите через 15 минут.", headers={"Retry-After": "900"})
            db.execute("INSERT INTO auth_limits VALUES(?,1,?) ON CONFLICT(bucket) DO UPDATE SET count=count+1", (bucket, now + 900))


async def matrix(method, path, token="", body=None, *, interactive=False, password="", user_id=""):
    url = (os.environ.get("PRESIDIUM_MATRIX_HOMESERVER_URL") or "http://localhost:8008").rstrip("/")
    headers = {"Authorization": "Bearer " + token} if token else {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=35)) as client:
        for attempt in range(2):
            try:
                async with client.request(method, url + "/_matrix/client/v3/" + path, json=body, headers=headers) as response:
                    result = await response.json()
                    if response.status == 401 and interactive and attempt == 0 and result.get("session"):
                        body = {**body, "auth": {"type": "m.login.password", "identifier": {"type": "m.id.user", "user": user_id}, "password": password, "session": result["session"]}}
                        continue
                    if response.status >= 400:
                        if response.status == 401 and interactive and attempt == 1:
                            raise HTTPException(403, "Текущий пароль неверен. Сеанс не изменён.")
                        code = result.get("errcode", "M_UNKNOWN")
                        message = {"M_USER_IN_USE": "Этот логин уже занят. Войдите в существующий аккаунт.",
                                   "M_FORBIDDEN": "Неверный логин или пароль.", "M_UNKNOWN_TOKEN": "Сеанс завершён. Войдите снова.",
                                   "M_LIMIT_EXCEEDED": "Слишком много попыток. Повторите позже.",
                                   "M_NOT_FOUND": "Пользователь не найден."}.get(code, "Сервер не выполнил операцию (" + code + ").")
                        raise HTTPException(response.status, message)
                    return result
            except (aiohttp.ClientError, TimeoutError) as exc:
                raise HTTPException(503, "Сервер сообщений недоступен. Повторите запрос.") from exc
    raise HTTPException(401, "Нужно подтвердить текущий пароль.")


async def identity(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials:
        raise HTTPException(401, "Войдите в аккаунт.")
    token = credentials.credentials
    await enforce_expiry(token)
    who = await matrix("GET", "account/whoami", token)
    return token, who


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)
    display_name: str = Field(default="", max_length=80)
    device_id: str | None = Field(default=None, max_length=255)
    device_name: str = Field(default="Presidium · браузер", max_length=80)
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=128)


def normalize(value):
    value = value.strip().lower()
    if value.startswith("@"):
        # Existing full Matrix IDs are accepted only on this configured server.
        server = os.environ.get("PRESIDIUM_MATRIX_SERVER_NAME", "presidium.local")
        if not value.endswith(":" + server):
            raise HTTPException(400, "Используйте логин на этом сервере Presidium.")
        value = value[1:-(len(server) + 1)]
    if not re.fullmatch(r"[a-z0-9][a-z0-9._=-]{2,63}", value):
        raise HTTPException(400, "Логин: 3–64 символа, латиница, цифры, . _ = -; первый символ — буква или цифра.")
    return value


@router.post("/register")
async def register(data: Credentials, request: Request):
    username = normalize(data.username)
    rate_limit(request, "register", username)
    if len(data.password) < 12 or not data.display_name.strip():
        raise HTTPException(400, "Укажите имя и пароль от 12 символов.")
    request_hash = fingerprint(data.idempotency_key or uuid.uuid4().hex)
    # Tuwunel can race its existence check against account creation. The PRIMARY
    # KEY serializes provisioning across API workers, without another user/password DB.
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        inserted = db.execute("INSERT OR IGNORE INTO registration_jobs VALUES(?,?,'pending',?,NULL,NULL)",
                              (username, request_hash, int(time.time()))).rowcount
        job = db.execute("SELECT * FROM registration_jobs WHERE login=?", (username,)).fetchone()
    if not inserted:
        if job["request_hash"] != request_hash or job["state"] == "occupied":
            raise HTTPException(409, "Этот логин уже занят или регистрируется. Если это ваш аккаунт, войдите.")
        # Retry after a lost registration response: prove the existing password.
        # Never repeat register on an uncertain job, which could replace credentials.
        payload = {"type":"m.login.password", "identifier":{"type":"m.id.user","user":username}, "password":data.password}
        if job["device_id"]:
            payload["device_id"] = job["device_id"]
        try:
            result = await matrix("POST", "login", body=payload)
        except HTTPException as exc:
            raise HTTPException(409, "Регистрация ещё не подтверждена. Повторите вход позже; повторное создание аккаунта заблокировано.") from exc
    else:
        try:
            result = await matrix("POST", "register", body={"username": username, "password": data.password,
                "initial_device_display_name": data.device_name, "auth": {"type": "m.login.dummy"}})
        except HTTPException as exc:
            with database() as db:
                db.execute("UPDATE registration_jobs SET state=? WHERE login=?", ("uncertain" if exc.status_code >= 500 else "occupied", username))
            raise
    with database() as db:
        db.execute("UPDATE registration_jobs SET state='created',user_id=?,device_id=? WHERE login=?",
                   (result["user_id"], result["device_id"], username))
    remember(result)
    try:
        await matrix("PUT", "profile/" + quote(result["user_id"], safe="") + "/displayname", result["access_token"], {"displayname": data.display_name.strip()})
    except HTTPException:
        # Account already exists. Never create another identity on a profile failure.
        result["profile_pending"] = True
    return result


@router.post("/login")
async def login(data: Credentials, request: Request):
    username = normalize(data.username)
    rate_limit(request, "login", username)
    body = {"type": "m.login.password", "identifier": {"type": "m.id.user", "user": username},
            "password": data.password, "initial_device_display_name": data.device_name}
    if data.device_id:
        body["device_id"] = data.device_id
    return remember(await matrix("POST", "login", body=body))


@router.get("/session")
async def session(auth=Depends(identity)):
    token, who = auth
    remember({**who, "access_token": token})
    profile = await matrix("GET", "profile/" + quote(who["user_id"], safe=""), token)
    return {**who, "access_token": token, "display_name": profile.get("displayname", "")}


@router.get("/policy")
async def token_policy(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    """Expiry guard for the fixed-target Matrix proxy, NOT identity verification.

    The destination homeserver must still authenticate the forwarded token.
    Unknown legacy tokens acquire a fixed lifetime on the normal verified bootstrap.
    """
    if not credentials:
        raise HTTPException(401, "Войдите в аккаунт.")
    await enforce_expiry(credentials.credentials)
    return {"ok": True}


@router.post("/logout")
async def logout(auth=Depends(identity)):
    token, _ = auth
    await matrix("POST", "logout", token, {})
    with database() as db:
        db.execute("UPDATE web_sessions SET revoked_at=? WHERE token_hash=?", (int(time.time()), fingerprint(token)))
    return {"ok": True}


@router.get("/devices")
async def devices(auth=Depends(identity)):
    token, who = auth
    result = await matrix("GET", "devices", token)
    return {"devices": [{**d, "current": d["device_id"] == who["device_id"]} for d in result["devices"]], "user_id": who["user_id"]}


class SecurityAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str | None = Field(default=None, min_length=12, max_length=256)
    device_id: str | None = Field(default=None, max_length=255)


@router.post("/devices")
async def revoke(data: SecurityAction, request: Request, auth=Depends(identity)):
    token, who = auth
    rate_limit(request, "security", who["user_id"])
    rows = (await matrix("GET", "devices", token))["devices"]
    ids = [d["device_id"] for d in rows if d["device_id"] != who["device_id"] and (data.device_id is None or d["device_id"] == data.device_id)]
    if data.device_id is not None and not ids:
        raise HTTPException(404, "Устройство не найдено среди ваших остальных устройств.")
    if ids:
        await matrix("POST", "delete_devices", token, {"devices": ids}, interactive=True, password=data.current_password, user_id=who["user_id"])
        with database() as db:
            db.executemany("UPDATE web_sessions SET revoked_at=? WHERE user_id=? AND device_id=?", [(int(time.time()), who["user_id"], d) for d in ids])
    return {"ok": True, "revoked": ids}


@router.post("/password")
async def password(data: SecurityAction, request: Request, auth=Depends(identity)):
    token, who = auth
    rate_limit(request, "security", who["user_id"])
    if not data.new_password:
        raise HTTPException(400, "Укажите новый пароль.")
    await matrix("POST", "account/password", token, {"new_password": data.new_password, "logout_devices": True},
                 interactive=True, password=data.current_password, user_id=who["user_id"])
    with database() as db:
        db.execute("UPDATE web_sessions SET revoked_at=? WHERE user_id=? AND token_hash<>?", (int(time.time()), who["user_id"], fingerprint(token)))
    return {"ok": True, "other_sessions_revoked": True}


@router.get("/lookup")
async def lookup(request: Request, auth=Depends(identity)):
    token, _ = auth
    username = normalize(request.query_params.get("login", ""))
    uid = "@" + username + ":" + os.environ.get("PRESIDIUM_MATRIX_SERVER_NAME", "presidium.local")
    profile = await matrix("GET", "profile/" + quote(uid, safe=""), token)
    return {"user_id": uid, "login": username, "display_name": profile.get("displayname") or username}
