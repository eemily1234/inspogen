import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

import line_server
from ops import db, linebot as lb, services as s

SECRET = "topsecret"


def sign(body: bytes) -> str:
    return base64.b64encode(hmac.new(SECRET.encode(), body, hashlib.sha256).digest()).decode()


@pytest.fixture
def env(tmp_path, monkeypatch):
    path = str(tmp_path / "t.db")
    monkeypatch.setenv("OPS_DB", path)
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setenv("LINE_CHANNEL_SECRET", SECRET)
    sent = []
    monkeypatch.setattr(line_server, "send_reply", lambda tok, text: sent.append((tok, text)))
    conn = db.connect(path)
    s.create_user(conn, "老闆", s.OWNER, "1234")
    return TestClient(line_server.app), conn, sent


def event(text, eid="e1", user="Uboss"):
    return {"events": [{"type": "message", "webhookEventId": eid, "replyToken": "tok",
                        "source": {"userId": user}, "message": {"type": "text", "id": "m1", "text": text}}]}


def post(client, payload, signature=None):
    body = json.dumps(payload).encode()
    return client.post("/webhook", content=body,
                       headers={"x-line-signature": signature if signature is not None else sign(body)})


def test_rejects_bad_signature(env):
    client, conn, sent = env
    r = post(client, event("支出 午餐 120"), signature="bad")
    assert r.status_code == 400 and not sent


def test_end_to_end_and_dedup(env):
    client, conn, sent = env
    code = lb.create_bind_code(conn, 1)
    assert post(client, event(f"綁定 {code}", "e0")).status_code == 200
    assert post(client, event("支出 午餐 120", "e1")).status_code == 200
    assert post(client, event("支出 午餐 120", "e1")).status_code == 200  # Line 重送同一事件
    assert [t for _, t in sent].count(sent[1][1]) == 1
    from datetime import date
    assert s.report(conn, date.today(), date.today())["expense"] == 120


def test_ignores_non_text_events(env):
    client, conn, sent = env
    payload = {"events": [{"type": "follow", "source": {"userId": "U1"}, "replyToken": "t"}]}
    assert post(client, payload).status_code == 200 and not sent
