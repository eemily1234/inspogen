"""Uber Eats webhook 伺服器。

環境變數：UBER_CLIENT_ID、UBER_CLIENT_SECRET（必填）、OPS_DB；其餘見 ops/uber.py 的 UberConfig。
Webhook URL（需公開 HTTPS）：https://你的網址/uber/webhook   （由 webhook_app.py 掛載）
流程：驗簽 → 立刻回 200（空內容，Uber 要求）→ 背景處理：取訂單 → 建單進廚房 → 接受；廚房暫停則拒絕。
"""
import hashlib
import hmac
import json
import os

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response

from ops import db, uber

app = FastAPI(title="營運系統 Uber Eats")
_client = None


def get_client():
    """可在測試中替換。"""
    global _client
    if _client is None:
        _client = uber.UberClient()
    return _client


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    """Uber：X-Uber-Signature 為小寫十六進位的 HMAC-SHA256(body, client_secret)。"""
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, (signature or "").lower())


def _process(event):
    conn = db.connect()
    try:
        uber.handle_event(conn, get_client(), event)
    finally:
        conn.close()


@app.get("/health")
def health():
    return {"ok": True, "configured": uber.UberConfig().configured}


@app.post("/webhook")
async def webhook(request: Request, background: BackgroundTasks):
    secret = os.environ.get("UBER_CLIENT_SECRET")
    if not secret:
        raise HTTPException(500, "UBER_CLIENT_SECRET not set")
    body = await request.body()
    if not verify_signature(body, request.headers.get("x-uber-signature", ""), secret):
        raise HTTPException(401, "invalid signature")
    try:
        event = json.loads(body)
    except ValueError:
        raise HTTPException(400, "invalid json")
    background.add_task(_process, event)       # 先回 200，再慢慢處理（Uber 要求快速確認）
    return Response(status_code=200, content=b"")
