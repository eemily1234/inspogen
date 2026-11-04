"""Line webhook 伺服器。啟動：uvicorn line_server:app --port 8000

環境變數：
  LINE_CHANNEL_SECRET        Line Developers → Messaging API → Channel secret
  LINE_CHANNEL_ACCESS_TOKEN  同頁面 → Channel access token (long-lived)
  OPS_DB                     資料庫路徑（需與 ops_app.py 相同）
Webhook URL（需 HTTPS 公開網址）：https://你的網址/webhook
"""
import base64
import hashlib
import hmac
import json
import os
import sqlite3

import requests
from fastapi import FastAPI, HTTPException, Request

from ops import db
from ops.linebot import handle_text

REPLY_URL = "https://api.line.me/v2/bot/message/reply"

app = FastAPI(title="營運系統 Line 記帳")


def verify_signature(body: bytes, signature: str, secret: str) -> bool:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")


def send_reply(reply_token: str, text: str) -> None:
    """回覆訊息。獨立成函式，測試時可替換。"""
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    resp = requests.post(
        REPLY_URL,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps({"replyToken": reply_token,
                         "messages": [{"type": "text", "text": text[:4900]}]}),
        timeout=10,
    )
    if resp.status_code != 200:
        print("Line reply failed:", resp.status_code, resp.text)


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request):
    secret = os.environ.get("LINE_CHANNEL_SECRET")
    if not secret:
        raise HTTPException(500, "LINE_CHANNEL_SECRET not set")
    body = await request.body()
    if not verify_signature(body, request.headers.get("x-line-signature", ""), secret):
        raise HTTPException(400, "invalid signature")

    conn = db.connect()
    try:
        for ev in json.loads(body).get("events", []):
            msg = ev.get("message") or {}
            if ev.get("type") != "message" or msg.get("type") != "text":
                continue
            line_user_id = (ev.get("source") or {}).get("userId")
            if not line_user_id:
                continue
            # Line 可能重送同一事件：只處理一次，避免重複記帳
            event_id = ev.get("webhookEventId") or msg.get("id")
            if event_id:
                try:
                    with conn:
                        conn.execute("INSERT INTO line_event(event_id) VALUES (?)", (event_id,))
                except sqlite3.IntegrityError:
                    continue
            try:
                reply = handle_text(conn, line_user_id, msg.get("text", ""))
            except Exception:
                if event_id:  # 非預期錯誤：釋放事件，讓 Line 重送時可再處理
                    with conn:
                        conn.execute("DELETE FROM line_event WHERE event_id=?", (event_id,))
                raise
            if ev.get("replyToken"):
                send_reply(ev["replyToken"], reply)
    finally:
        conn.close()
    return {"ok": True}
