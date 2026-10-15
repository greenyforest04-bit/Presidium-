"""Minimal Presidium API foundation.

The API intentionally keeps the product surface clean while delegating actual
transport work to the abstractions in ``core.messaging``. The initial
implementation provides real HTTP endpoints for registration and encrypted direct
messaging metadata without exposing Matrix internals to callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from presidium.core.messaging.matrix_transport import MatrixTransport, MatrixTransportConfig


@dataclass(slots=True)
class AccountRecord:
    presidium_user_id: str
    matrix_user_id: str
    username: str
    password: str
    display_name: str
    plan: str = "free"


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)
    display_name: str = Field(default="", min_length=1, max_length=128)


class DirectMessageRequest(BaseModel):
    sender: str = Field(..., min_length=3, max_length=64)
    recipient: str = Field(..., min_length=3, max_length=64)
    text: str = Field(..., min_length=1, max_length=4096)


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=256)


app = FastAPI(title="Presidium API", version="0.1.0")

_TRANSPORT = MatrixTransport(
    MatrixTransportConfig(
        homeserver_url="http://tuwunel:8008",
        device_id="presidium-device",
    )
)

_ACCOUNTS: dict[str, AccountRecord] = {}


def _matrix_user_id(username: str) -> str:
    return f"@{username}:presidium.local"


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "transport": "matrix",
        "e2ee_default": True,
        "plan_model": {"free": ["e2ee", "direct_messages", "groups"], "plus": ["mesh", "p2p"]},
    }


@app.post("/v1/auth/register")
def register_user(request: RegisterRequest) -> dict[str, Any]:
    username = request.username.strip()
    if not username:
        raise HTTPException(status_code=400, detail="username is required")
    if username in _ACCOUNTS:
        raise HTTPException(status_code=409, detail="username already registered")

    presidium_user_id = f"presidium_{abs(hash(username)) % 1_000_000_000:09d}"
    matrix_user_id = _matrix_user_id(username)
    account = AccountRecord(
        presidium_user_id=presidium_user_id,
        matrix_user_id=matrix_user_id,
        username=username,
        password=request.password,
        display_name=request.display_name or username,
    )
    _ACCOUNTS[username] = account

    return {
        "presidium_user_id": account.presidium_user_id,
        "matrix_user_id": account.matrix_user_id,
        "username": account.username,
        "display_name": account.display_name,
        "plan": account.plan,
        "transport": "matrix",
        "message": "Account created. E2EE is enabled by default.",
    }


@app.post("/v1/auth/login")
def login_user(request: LoginRequest) -> dict[str, Any]:
    account = _ACCOUNTS.get(request.username)
    if account is None or account.password != request.password:
        raise HTTPException(status_code=401, detail="invalid credentials")

    return {
        "presidium_user_id": account.presidium_user_id,
        "matrix_user_id": account.matrix_user_id,
        "username": account.username,
        "display_name": account.display_name,
        "plan": account.plan,
        "transport": "matrix",
    }


@app.post("/v1/messaging/direct")
def create_direct_message(request: DirectMessageRequest) -> dict[str, Any]:
    sender = _ACCOUNTS.get(request.sender)
    recipient = _ACCOUNTS.get(request.recipient)
    if sender is None or recipient is None:
        raise HTTPException(status_code=404, detail="sender or recipient not found")

    conversation = _TRANSPORT.build_dm_creation_payload(
        local_user_id=sender.matrix_user_id,
        remote_user_id=recipient.matrix_user_id,
        room_name=f"{sender.display_name} / {recipient.display_name}",
    )
    message = _TRANSPORT.build_message_event(request.text)

    return {
        "conversation": conversation,
        "message": message,
        "sender": sender.presidium_user_id,
        "recipient": recipient.presidium_user_id,
        "transport": "matrix",
        "e2ee": True,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("presidium.api:app", host="0.0.0.0", port=8000, reload=False)
