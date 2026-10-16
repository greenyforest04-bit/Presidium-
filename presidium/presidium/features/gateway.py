"""Execute recovered business modules within the existing FastAPI backend.

Only verified Matrix identities enter this interface. No new HTTP service or
alternate authentication provider is introduced. Secrets travel over stdin,
never command arguments or client-visible configuration.
"""
import asyncio
import base64
import hashlib
import io
import json
import os
from pathlib import Path

from fastapi import HTTPException, Request, Response

from .store import migrate


def product_directory() -> Path:
    return Path((os.environ.get("PRESIDIUM_PRODUCT_DATA_DIR") or ".data/product")).resolve()


async def execute(area: str, identity: dict, token: str, *, method="GET", body=None, query=None, headers=None):
    directory = product_directory()
    database = directory / "product.sqlite3"
    await asyncio.to_thread(migrate, database)
    runtime = Path(__file__).with_name("argon-runtime.mjs")
    if not runtime.is_file():
        raise HTTPException(503, "Run npm run build:argon in presidium/web first.")
    config = {k: v for k, v in os.environ.items() if k.startswith(("COMMISSIONKA_", "CAPSULE_")) or k in ("PRESIDIUM_MODERATOR_IDS", "PRESIDIUM_BILLING_WEBHOOK_SECRET")}
    config["MATRIX_PUBLIC_URL"] = (os.environ.get("PRESIDIUM_MATRIX_PUBLIC_URL") or "http://localhost:8008")
    payload = dict(area=area, identity=identity, sessionHash=hashlib.sha256(token.encode()).hexdigest(), database=str(database), mediaDirectory=str(directory / "media"), method=method, body=body or {}, query=query or {}, config=config, headers=headers or {})
    try:
        process = await asyncio.create_subprocess_exec((os.environ.get("PRESIDIUM_NODE_BIN") or "node"), "--disable-warning=ExperimentalWarning", str(runtime), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    except (FileNotFoundError, PermissionError) as exc:
        raise HTTPException(503, "Node 24 runtime unavailable for the integrated domain module.") from exc
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), timeout=45)
    except BaseException:
        process.kill()
        await process.wait()
        raise
    if process.returncode:
        raise HTTPException(503, "Domain module failed. Check server logs and Node version (24 required).")
    result = json.loads(stdout)
    if result["status"] >= 400:
        raise HTTPException(result["status"], result["value"])
    return result["value"]


async def handle(request: Request, area: str, identity: dict, token: str, *, media: bool = False):
    query = dict(request.query_params)
    if media:
        query["media"] = "1"
    if request.method == "GET":
        value = await execute(area, identity, token, query=query)
        if "binary" in value:
            return Response(base64.b64decode(value["binary"]), media_type=value["mime"], headers={"X-Content-Type-Options":"nosniff", "Cache-Control":"no-store", "Content-Security-Policy":"default-src 'none'"})
        return value
    maximum = 25 * 1024 * 1024 if query.get("media") else 200_000
    parts = bytearray()
    async for chunk in request.stream():
        parts.extend(chunk)
        if len(parts) > maximum:
            raise HTTPException(413, "Request too large")
    if query.get("media"):
        data = bytes(parts)
        mime = request.headers.get("content-type", "").split(";")[0]
        if area == "marketplace":
            from PIL import Image
            try:
                with Image.open(io.BytesIO(data)) as image:
                    maximum_dimension = 1600 if query.get("variant", "full") == "full" else 384
                    if image.format != "WEBP" or max(image.size) > maximum_dimension or getattr(image, "n_frames", 1) != 1:
                        raise ValueError("WebP size/animation")
                    # Metadata is removed; output is deterministic for request replay.
                    output = io.BytesIO()
                    image.convert("RGB").save(output, "WEBP", quality=85)
                    data = output.getvalue()
            except Exception as exc:
                raise HTTPException(415, "Expected a valid WebP photo within the dimension limit") from exc
        body = dict(bytes=base64.b64encode(data).decode(), mime=mime, kind=query.get("kind", "image"), name=query.get("name", "media"))
    else:
        try:
            body = json.loads(parts)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(400, "Invalid JSON") from exc
    return await execute(area, identity, token, method=request.method, body=body, query=query)
