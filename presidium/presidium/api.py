"""Authenticated Matrix gateway. Encryption and decryption stay on clients."""

from __future__ import annotations

import hashlib
import os
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from presidium.core.messaging.matrix_transport import (
    MatrixRequestError,
    MatrixTransport,
    MatrixTransportConfig,
)

app = FastAPI(title="Presidium API", version="0.2.0")
bearer = HTTPBearer(auto_error=False)


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-z0-9._=-]+$")
    password: str = Field(min_length=8, max_length=256)
    display_name: str = Field(default="", max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=256)
    device_id: str | None = Field(default=None, min_length=1, max_length=255)


class ConversationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str = Field(pattern=r"^@[^:]+:.+$", max_length=255)


class DirectMessageRequest(ConversationRequest):
    room_id: str = Field(pattern=r"^![^\s]+$", max_length=255)
    encrypted_content: dict[str, Any]


def transport(token: str = "") -> MatrixTransport:
    return MatrixTransport(
        MatrixTransportConfig(
            homeserver_url=os.environ.get(
                "PRESIDIUM_MATRIX_HOMESERVER_URL"
            ) or "http://localhost:8008",
            access_token=token,
        )
    )


async def matrix_call(client: MatrixTransport, method: str, path: str, payload=None):
    try:
        return await client.request(method, path, payload)
    except MatrixRequestError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc


async def authenticated(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(
            status_code=401, detail="Bearer token required", headers={"WWW-Authenticate": "Bearer"}
        )
    from presidium.features.auth import enforce_expiry
    await enforce_expiry(credentials.credentials)
    client = transport(credentials.credentials)
    identity = await matrix_call(client, "GET", "account/whoami")
    return client, identity


def account_response(result: dict[str, Any]) -> dict[str, Any]:
    user_id = result["user_id"]
    return {
        **result,
        "matrix_user_id": user_id,
        "presidium_user_id": "presidium_" + hashlib.sha256(user_id.encode()).hexdigest()[:32],
    }


@app.get("/health")
async def health():
    await matrix_call(transport(), "GET", "login")
    return {"status": "ok", "transport": "matrix", "encryption": "client-side"}


from presidium.features.auth import router as auth_router
app.include_router(auth_router)


@app.post("/v1/conversations/direct")
async def create_direct_conversation(request: ConversationRequest, auth=Depends(authenticated)):
    client, identity = auth
    try:
        return await client.create_conversation(
            participants=[identity["user_id"], request.recipient]
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MatrixRequestError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@app.post("/v1/messaging/direct")
async def create_direct_message(request: DirectMessageRequest, auth=Depends(authenticated)):
    client, identity = auth
    try:
        await client.assert_direct_conversation(
            request.room_id, identity["user_id"], request.recipient
        )
        return await client.send_message(request.room_id, request.encrypted_content)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except MatrixRequestError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.code) from exc


@app.api_route("/api/{area}", methods=["GET", "POST"])
async def product_features(area: str, request: Request, auth=Depends(authenticated)):
    from presidium.features.gateway import handle
    client, identity = auth
    if area == "account" and request.method == "POST":
        try:
            data = await request.json()
        except ValueError:
            data = None
        if isinstance(data, dict) and data.get("action") == "revoke":
            # The old product-only revocation left Matrix authorization alive.
            raise HTTPException(410, "Используйте «Устройства и сеансы»: отзыв требует Matrix-подтверждения паролем.")
    return await handle(request, area, identity, client.config.access_token)


@app.api_route("/api/{area}/media", methods=["GET", "POST"])
async def product_media(area: str, request: Request, auth=Depends(authenticated)):
    from presidium.features.gateway import handle
    client, identity = auth
    return await handle(request, area, identity, client.config.access_token, media=True)


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request):
    from presidium.features.gateway import execute
    data = await request.body()
    if len(data) > 8192:
        raise HTTPException(413, "Billing event too large")
    return await execute("billing", {}, "", method="POST", body={"raw":data.decode()}, headers=dict(request.headers))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("presidium.api:app", host="0.0.0.0", port=8000)
