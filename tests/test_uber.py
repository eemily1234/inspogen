import hashlib
import hmac
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

import uber_server
from ops import db, orders as o, services as s, uber

SECRET = "uber-secret"


class FakeClient:
    """假的 Uber 用戶端：回傳預先準備的訂單，記錄接受／拒絕／備餐完成的呼叫。"""

    def __init__(self, order=None, fail_get=False):
        self.order, self.fail_get, self.calls = order, fail_get, []

    def get_order(self, order_id, href=None):
        if self.fail_get:
            raise RuntimeError("Uber API 逾時")
        self.calls.append(("get", order_id))
        return self.order

    def accept(self, order_id):
        self.calls.append(("accept", order_id))

    def deny(self, order_id, explanation=""):
        self.calls.append(("deny", order_id, explanation))

    def mark_ready(self, order_id):
        self.calls.append(("ready", order_id))
        if getattr(self, "ready_fails", False):
            raise RuntimeError("ready 失敗")


def uber_order(oid="ord-1", display="A1B2", items=None, instr=None):
    items = items if items is not None else [
        {"id": "u-curry", "title": "咖哩飯", "quantity": 2, "special_instructions": "少辣",
         "selected_modifier_groups": [{"selected_items": [{"title": "加蛋", "quantity": 1}]}]}]
    return {"id": oid, "display_id": display, "special_instructions": instr, "cart": {"items": items}}


def event(oid="ord-1", etype="orders.notification"):
    return {"event_type": etype, "meta": {"resource_id": oid}, "resource_href": f"https://x/{oid}"}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "u.db"))
    s.create_user(c, "老闆", s.OWNER, "1234")
    s.add_item(c, "咖哩飯", "meal", 150)
    s.add_item(c, "紅茶", "meal", 40)
    return c


def test_new_uber_order_goes_to_kitchen_and_is_accepted(conn):
    fc = FakeClient(uber_order(instr="請放門口"))
    assert uber.handle_event(conn, fc, event()) == ("ok", None)
    assert ("accept", "ord-1") in fc.calls
    t = o.kitchen_tickets(conn)
    assert len(t) == 1 and t[0]["where"] == "Uber A1B2" and t[0]["order_note"] == "請放門口"
    assert [(l["name"], l["qty"], l["note"]) for l in t[0]["lines"]] == [("咖哩飯", 2, "少辣、加蛋")]
    od = o.open_orders(conn)[0]
    assert od["order_type"] == "takeout" and od["source"] == "uber" and od["total"] == 300   # 用菜單價格
    assert o.floor_overview(conn)["takeouts"][0]["where"] == "Uber A1B2"


def test_duplicate_notification_creates_one_order(conn):
    fc = FakeClient(uber_order())
    uber.handle_event(conn, fc, event())
    assert uber.handle_event(conn, fc, event()) == ("duplicate", None)
    assert conn.execute("SELECT COUNT(*) FROM meal_order").fetchone()[0] == 1
    assert [c for c in fc.calls if c[0] == "accept"] == [("accept", "ord-1")]


def test_unmatched_item_becomes_placeholder_not_lost(conn):
    fc = FakeClient(uber_order(items=[{"id": "u-new", "title": "季節限定牛肉飯", "quantity": 1}]))
    status, note = uber.handle_event(conn, fc, event())
    assert status == "ok" and "季節限定牛肉飯" in note
    line = o.kitchen_tickets(conn)[0]["lines"][0]
    assert line["name"] == "[Uber] 季節限定牛肉飯" and "未對應" in line["note"]
    assert "[Uber] 季節限定牛肉飯" not in [i["name"] for i in s.list_items(conn)]       # 不會出現在菜單
    uber.handle_event(conn, FakeClient(uber_order("ord-2", items=[{"id": "u-new", "title": "季節限定牛肉飯", "quantity": 1}])), event("ord-2"))
    assert conn.execute("SELECT COUNT(*) FROM item WHERE name LIKE '[Uber]%'").fetchone()[0] == 1   # 重用同一個暫存品項


def test_item_mapping_is_remembered_by_external_id(conn):
    uber.handle_event(conn, FakeClient(uber_order("a")), event("a"))                   # 以品名對上，並記住 u-curry
    renamed = uber_order("b", items=[{"id": "u-curry", "title": "招牌咖哩飯（Uber 改名）", "quantity": 1}])
    uber.handle_event(conn, FakeClient(renamed), event("b"))
    names = [l["name"] for t in o.kitchen_tickets(conn) for l in t["lines"]]
    assert names == ["咖哩飯", "咖哩飯"]                                                # 改名後仍對得上


def test_kitchen_paused_denies_and_creates_no_order(conn):
    o.set_kitchen_accepting(conn, False, "客滿")
    fc = FakeClient(uber_order())
    status, note = uber.handle_event(conn, fc, event())
    assert status == "denied" and "客滿" in note
    assert any(c[0] == "deny" for c in fc.calls) and not any(c[0] == "accept" for c in fc.calls)
    assert conn.execute("SELECT COUNT(*) FROM meal_order").fetchone()[0] == 0
    o.set_kitchen_accepting(conn, True)
    assert uber.handle_event(conn, FakeClient(uber_order()), event())[0] == "ok"        # 恢復後可再收單


def test_cancel_before_and_after_cooking(conn):
    uber.handle_event(conn, FakeClient(uber_order("c1")), event("c1"))
    assert uber.handle_event(conn, FakeClient(), event("c1", "orders.failure")) == ("cancelled", "cancelled")
    assert o.open_orders(conn) == []
    uber.handle_event(conn, FakeClient(uber_order("c2")), event("c2"))
    oid = o.open_orders(conn)[0]["id"]
    o.advance_ticket(conn, oid, 1)                                                      # 廚房已開始做
    assert uber.handle_event(conn, FakeClient(), event("c2", "orders.failure"))[1] == "flagged"
    od = o.open_orders(conn)[0]
    assert od["note"].startswith("⚠️ 平台已取消")                                        # 留給人處理，不會默默消失
    assert uber.handle_event(conn, FakeClient(), event("nope", "orders.failure"))[1] == "unknown"


def test_api_failure_is_recorded_and_alerts(conn):
    fc = FakeClient(fail_get=True)
    status, note = uber.handle_event(conn, fc, event())
    assert status == "failed" and "逾時" in note
    assert conn.execute("SELECT COUNT(*) FROM meal_order").fetchone()[0] == 0
    assert o.external_alerts(conn) == 1
    row = conn.execute("SELECT * FROM external_event WHERE status='failed'").fetchone()
    assert row["external_id"] == "ord-1" and "orders.notification" in row["payload"]     # 留下原始資料方便除錯


def test_notify_ready_only_for_uber_orders_and_failures_are_recorded(conn):
    uber.handle_event(conn, FakeClient(uber_order()), event())
    uber_id = o.open_orders(conn)[0]["id"]
    pos = o.place_order(conn, "takeout", [{"item_id": 2, "qty": 1}])
    fc = FakeClient()
    assert uber.notify_ready(conn, fc, pos["order_id"]) is None and fc.calls == []
    assert uber.notify_ready(conn, fc, uber_id) is True and fc.calls == [("ready", "ord-1")]
    fc.ready_fails = True
    assert uber.notify_ready(conn, fc, uber_id) is False
    assert o.external_alerts(conn) == 1


def test_uber_order_checkout_uses_menu_price_and_uber_payment(conn):
    uber.handle_event(conn, FakeClient(uber_order()), event())
    oid = o.open_orders(conn)[0]["id"]
    o.checkout_order(conn, oid, "Uber Eats")
    assert s.report(conn, date.today(), date.today())["income"] == 300
    assert conn.execute("SELECT pay_method FROM sale").fetchone()[0] == "Uber Eats"


def test_parse_is_tolerant():
    assert uber.parse_uber_order({"order": {"id": "xyz123", "cart": {"items": []}}})["external_id"] == "xyz123"
    p = uber.parse_uber_order({"id": "abc", "items": [{"title": "紅茶"}]})
    assert p["lines"] == [{"external_item_id": None, "name": "紅茶", "qty": 1, "note": None}] and p["label"] == "ABC"[-4:]
    assert uber.parse_uber_order({})["lines"] == []


def test_place_external_order_validation(conn):
    with pytest.raises(s.OpsError):
        o.place_external_order(conn, "uber", "", [{"name": "x", "qty": 1}])
    with pytest.raises(s.OpsError):
        o.place_external_order(conn, "uber", "z", [])
    with pytest.raises(s.OpsError):
        o.place_external_order(conn, "uber", "z", [{"name": "咖哩飯", "qty": 0}])
    assert conn.execute("SELECT COUNT(*) FROM meal_order").fetchone()[0] == 0           # 失敗整筆回復


# ───────────── webhook 伺服器 ─────────────
def sign(body: bytes, secret=SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def server(tmp_path, monkeypatch):
    path = str(tmp_path / "w.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    monkeypatch.setenv("UBER_CLIENT_SECRET", SECRET)
    c = db.connect(path)
    s.add_item(c, "咖哩飯", "meal", 150)
    fc = FakeClient(uber_order())
    monkeypatch.setattr(uber_server, "get_client", lambda: fc)
    return TestClient(uber_server.app), c, fc


def test_server_rejects_bad_signature(server):
    client, c, fc = server
    body = json.dumps(event()).encode()
    r = client.post("/webhook", content=body, headers={"x-uber-signature": "bad"})
    assert r.status_code == 401 and fc.calls == []
    assert client.post("/webhook", content=body).status_code == 401


def test_server_acks_with_empty_200_then_creates_order(server):
    client, c, fc = server
    body = json.dumps(event()).encode()
    r = client.post("/webhook", content=body, headers={"x-uber-signature": sign(body)})
    assert r.status_code == 200 and r.content == b""                                    # Uber 要求：200 且內容為空
    assert o.open_orders(c)[0]["where"] == "Uber A1B2"
    assert ("accept", "ord-1") in fc.calls
    r = client.post("/webhook", content=body, headers={"x-uber-signature": sign(body).upper()})   # 大小寫都接受
    assert r.status_code == 200 and conn_count(c) == 1


def conn_count(c):
    return c.execute("SELECT COUNT(*) FROM meal_order").fetchone()[0]


def test_server_without_secret_and_bad_json(server, monkeypatch):
    client, c, fc = server
    body = b"not json"
    assert client.post("/webhook", content=body, headers={"x-uber-signature": sign(body)}).status_code == 400
    monkeypatch.delenv("UBER_CLIENT_SECRET")
    assert client.post("/webhook", content=body).status_code == 500


def test_combined_webhook_app_mounts_both(server):
    import webhook_app
    client = TestClient(webhook_app.app)
    assert client.get("/health").json() == {"ok": True}
    assert client.get("/uber/health").status_code == 200
    body = json.dumps(event("ord-9")).encode()
    assert client.post("/uber/webhook", content=body, headers={"x-uber-signature": sign(body)}).status_code == 200
