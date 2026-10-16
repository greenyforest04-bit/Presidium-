"""Real SQLite/Node-module integration. These are not live Matrix transport tests."""
import asyncio
import asyncio
import base64
import hashlib
import hmac
import io
import json
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from fastapi import HTTPException
from PIL import Image
import pytest

from presidium.features.gateway import execute
from presidium.features.store import migrate, MigrationConflict

pytestmark = [pytest.mark.asyncio, pytest.mark.timeout(120)]
SELLER = "@seller:presidium.local"
BUYER = "@buyer:presidium.local"
MOD = "@moderator:presidium.local"


@pytest.fixture
def product(tmp_path, monkeypatch):
    monkeypatch.setenv("PRESIDIUM_PRODUCT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COMMISSIONKA_SANDBOX_ORDERS", "1")
    monkeypatch.setenv("COMMISSIONKA_VAT_MODE", "none")
    monkeypatch.setenv("COMMISSIONKA_DELIVERY_KOPECKS", "200")
    monkeypatch.setenv("COMMISSIONKA_PAYMENT_OPERATOR_KOPECKS", "100")
    monkeypatch.setenv("PRESIDIUM_MODERATOR_IDS", MOD)
    monkeypatch.delenv("CAPSULE_AGENT_URL", raising=False)
    return tmp_path / "product.sqlite3"


async def call(user, area, body=None, query=None, device="DEVICE"):
    return await execute(area, {"user_id":user,"device_id":device}, f"test-module-token:{user}:{device}", method="GET" if body is None else "POST", body=body, query=query)


def grant(path, user, end=None):
    now = int(time.time()*1000)
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO subscriptions(user_id,tier,status,period_start,period_end,updated_at,last_event_id) VALUES(?,'business','active',?,?,?,'test-fixture') ON CONFLICT(user_id) DO UPDATE SET period_end=excluded.period_end", (user,now-1000,end or now+86400000,now))


async def assert_error(status, awaitable):
    with pytest.raises(HTTPException) as error:
        await awaitable
    assert error.value.status_code == status, error.value.detail
    return error.value.detail


async def test_clean_install_and_populated_upgrade_are_preserved(tmp_path):
    path = tmp_path / "upgrade.sqlite3"
    migrate(path, through="0008_social_sync.sql")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users VALUES('existing',1)")
        db.execute("INSERT INTO profiles VALUES('existing','Original',NULL,'Preserve me',7,1)")
        db.execute("INSERT INTO social_posts(id,author_id,text,request_digest,created_at,updated_at) VALUES('old-post','existing','Existing publication','digest',1,1)")
    migrate(path)
    migrate(path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name,bio,revision FROM profiles").fetchone() == ("Original","Preserve me",7)
        assert db.execute("SELECT text FROM social_posts").fetchone()[0] == "Existing publication"
        assert db.execute("SELECT count(*) FROM product_migrations").fetchone()[0] == 12
    conflict = tmp_path / "conflict.sqlite3"
    migrate(conflict, through="0008_social_sync.sql")
    with sqlite3.connect(conflict) as db:
        def insert(table, **values):
            cols = db.execute(f"PRAGMA table_info({table})").fetchall()
            for _,name,kind,required,default,_ in cols:
                if name not in values and required and default is None:
                    values[name] = 0 if kind.lower()=="integer" else ""
            names=','.join(values)
            db.execute(f"INSERT INTO {table}({names}) VALUES({','.join('?' for _ in values)})",tuple(values.values()))
        insert("users",id="u",created_at=1)
        insert("market_listings",id="l",seller_id="u",status="sold")
        for order in ("paid1","paid2"):
            insert("market_orders",id=order,listing_id="l",buyer_id="u",seller_id="u",status="paid",locked_price_kopecks=1000,buyer_total_kopecks=1300,delivery_kopecks=200,payment_operator_kopecks=100,idempotency_key=order)
    with pytest.raises(MigrationConflict, match="Duplicate listings"):
        migrate(conflict)
    with sqlite3.connect(conflict) as db:
        assert db.execute("SELECT status,buyer_total_kopecks FROM market_orders").fetchall()==[("paid",1300),("paid",1300)]
        assert "active_order_id" not in [r[1] for r in db.execute("PRAGMA table_info(market_listings)")]


async def test_profile_device_revision_and_capsule_isolation(product):
    a = await call(SELLER,"account")
    await assert_error(409,call(SELLER,"account",{"name":"new","handle":"","bio":"","revision":99}))
    await call(SELLER,"account",{"name":"New seller","handle":"seller_test","bio":"bio","revision":a["profile"]["revision"]})
    b=await call(BUYER,"account")
    assert b["profile"]["name"]!="New seller"
    note={"id":str(uuid4()),"title":"Private note","text":"PRIVATE_MARKER","kind":"note","done":False,"revision":0}
    await call(SELLER,"capsule",note)
    assert (await call(SELLER,"capsule",note))["idempotent"]
    assert (await call(BUYER,"capsule"))["entries"]==[]
    await assert_error(403,call(BUYER,"capsule",note))
    await assert_error(409,call(SELLER,"capsule",{**note,"text":"Stale edit"}))
    assert (await call(SELLER,"capsule"))["entries"][0]["text"]=="PRIVATE_MARKER"
    await call(SELLER,"account",device="SECOND")
    await call(SELLER,"account",{"action":"revoke","sessionId":a["devices"][0]["id"]},device="SECOND")
    await assert_error(401,call(SELLER,"account"))


async def test_social_paid_rights_replay_revisions_comments_and_spaces(product):
    await call(SELLER,"account"); await call(BUYER,"account")
    post={"action":"createPost","id":str(uuid4()),"text":"Network publication","mediaIds":[],"topics":[]}
    await assert_error(403,call(SELLER,"social",post))
    grant(product,SELLER); grant(product,BUYER)
    result=await call(SELLER,"social",post)
    assert result["post"]["text"]==post["text"]
    assert (await call(SELLER,"social",post))["idempotent"]
    await assert_error(409,call(SELLER,"social",{**post,"text":"Reused ID with changed content"}))
    update={"action":"updatePost","id":post["id"],"revision":1,"text":"Updated","mediaIds":[],"topics":[]}
    await assert_error(403,call(BUYER,"social",update))
    await call(SELLER,"social",update)
    await assert_error(409,call(SELLER,"social",update))
    comment={"action":"comment","id":str(uuid4()),"postId":post["id"],"text":"Received in second account"}
    await call(BUYER,"interactions",comment);await call(BUYER,"interactions",comment)
    assert len((await call(SELLER,"interactions",query={"postId":post["id"]}))["comments"])==1
    sid=str(uuid4())
    await call(SELLER,"spaces",{"action":"create","id":sid,"kind":"channel","name":"Private channel","description":"Members only"})
    invite=await call(SELLER,"spaces",{"action":"invite","spaceId":sid})
    private_media=str(uuid4())
    await call(SELLER,"social",{"bytes":base64.b64encode(b"private file").decode(),"mime":"text/plain","kind":"file","name":"secret.txt"},query={"media":"1","id":private_media})
    private={**post,"id":str(uuid4()),"channelId":sid,"text":"CHANNEL_PRIVATE","mediaIds":[private_media]}
    await call(SELLER,"social",private)
    assert all(p["text"]!="CHANNEL_PRIVATE" for p in (await call(BUYER,"social"))["posts"])
    await assert_error(403,call(BUYER,"social",query={"media":"1","id":private_media}))
    await call(BUYER,"spaces",{"action":"join","token":invite["token"]})
    assert any(p["text"]=="CHANNEL_PRIVATE" for p in (await call(BUYER,"social"))["posts"])
    await assert_error(403,call(BUYER,"social",{**private,"id":str(uuid4())}))
    assert (await call(BUYER,"social",query={"media":"1","id":private_media}))["binary"]==base64.b64encode(b"private file").decode()
    await call(SELLER,"spaces",{"action":"remove","spaceId":sid,"userId":BUYER})
    await assert_error(403,call(BUYER,"social",query={"media":"1","id":private_media}))
    await assert_error(409,call(BUYER,"spaces",{"action":"join","token":invite["token"]}))
    grant(product,SELLER,end=1)
    await call(SELLER,"social",{"action":"deletePost","id":post["id"],"revision":2})


async def test_order_retries_financials_disputes_and_restart(product):
    for user in (SELLER,BUYER,MOD):
        await call(user,"account");grant(product,user)
    image=io.BytesIO();Image.new("RGB",(16,16),"blue").save(image,"WEBP")
    photo_id=str(uuid4())
    for variant in ("full","thumb"):
        await call(SELLER,"marketplace",{"bytes":base64.b64encode(image.getvalue()).decode(),"mime":"image/webp"},query={"media":"1","id":photo_id,"variant":variant})
    listing_id=str(uuid4())
    created=await call(SELLER,"marketplace",{"action":"create","id":listing_id,"category":"other","condition":"used","sellerType":"private","title":"Actual used item","description":"A real description longer than twenty characters","city":"Москва","priceKopecks":1000,"photoIds":[photo_id],"specs":[],"acceptedRules":True,"conditionDeclaration":True})
    listing=created["listing"]
    await assert_error(403,call(SELLER,"marketplace",{"action":"moderateListing","listingId":listing_id,"revision":listing["revision"],"decision":"approve"}))
    await call(MOD,"marketplace",{"action":"moderateListing","listingId":listing_id,"revision":listing["revision"],"decision":"approve","conditionVerified":True,"rightToSellVerified":True})
    listing=(await call(BUYER,"marketplace"))["listings"][0]
    checkout={"action":"checkout","listingId":listing_id,"revision":listing["revision"],"idempotencyKey":str(uuid4())}
    results=await asyncio.gather(call(BUYER,"marketplace",checkout),call(BUYER,"marketplace",checkout))
    assert results[0]["order"]["id"]==results[1]["order"]["id"]
    result=results[0]
    order=result["order"]
    assert order["financials"]["buyerTotalKopecks"]==1300
    assert (await call(BUYER,"marketplace",checkout))["order"]["id"]==order["id"]
    await assert_error(409,call(BUYER,"marketplace",{**checkout,"listingId":str(uuid4())}))
    await call(BUYER,"marketplace",{"action":"paySandbox","orderId":order["id"]})
    dispute={"action":"dispute","orderId":order["id"],"reason":"Delivery was never received"}
    first=await call(BUYER,"marketplace",dispute)
    assert (await call(BUYER,"marketplace",dispute))["idempotent"]
    await assert_error(403,call(SELLER,"marketplace",{"action":"resolveDispute","disputeId":first["dispute"]["id"],"decision":"refund_buyer","resolutionNote":"Confirmed by review"}))
    # Each gateway call starts a fresh module process, so this proves persistence across process restarts.
    restored=(await call(BUYER,"marketplace",query={"view":"orders"}))["orders"][0]
    assert restored["id"]==order["id"] and restored["status"]=="disputed"
    assert restored["financials"]["buyerTotalKopecks"]==1300
    resolution={"action":"resolveDispute","disputeId":first["dispute"]["id"],"decision":"refund_buyer","resolutionNote":"Explicit review of sandbox payment"}
    # An imported/real financial obligation without settlement proof must stay open.
    with sqlite3.connect(product) as db:
        db.execute("DELETE FROM market_payment_provenance WHERE order_id=?",(order["id"],))
    await assert_error(409,call(MOD,"marketplace",resolution))
    assert (await call(BUYER,"marketplace",query={"view":"orders"}))["orders"][0]["dispute"]["status"]=="open"
    with sqlite3.connect(product) as db:
        db.execute("INSERT INTO market_payment_provenance VALUES(?,'sandbox',?)",(order["id"],int(time.time()*1000)))
    resolved=await call(MOD,"marketplace",resolution)
    assert resolved["order"]["status"]=="cancelled"
    assert resolved["order"]["financials"]["buyerTotalKopecks"]==1300



async def test_billing_signatures_and_missing_ai_configuration(product,monkeypatch):
    await call(SELLER,"account")
    await assert_error(503,call(SELLER,"capsule-agent",{"requestId":str(uuid4()),"question":"hello","materials":[]}))
    secret="test-secret-only-for-this-disposable-database-123"
    monkeypatch.setenv("PRESIDIUM_BILLING_WEBHOOK_SECRET",secret)
    now=int(time.time()*1000)
    event={"id":str(uuid4()),"userId":SELLER,"tier":"plus","status":"active","periodStart":now-1000,"periodEnd":now+86400000,"occurredAt":now}
    raw=json.dumps(event,separators=(",",":")); stamp=str(now)
    signature=hmac.new(secret.encode(),f"presidium-billing-v1\n{stamp}\n{raw}".encode(),hashlib.sha256).hexdigest()
    headers={"x-presidium-timestamp":stamp,"x-presidium-signature":signature}
    await execute("billing",{},"",method="POST",body={"raw":raw},headers=headers)
    await execute("billing",{},"",method="POST",body={"raw":raw},headers=headers)
    assert (await call(SELLER,"subscription"))["subscription"]["tier"]=="plus"
    await assert_error(401,execute("billing",{},"",method="POST",body={"raw":raw+" "},headers=headers))
