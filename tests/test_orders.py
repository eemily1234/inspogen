from datetime import date, datetime

import pytest

from ops import db, orders as o, services as s


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    s.create_user(c, "老闆", s.OWNER, "1234")
    s.add_item(c, "咖哩飯", "meal", 150, cost=60)
    s.add_item(c, "紅茶", "meal", 40)
    s.add_item(c, "魚油", "health", 1000)
    return c


def L(item_id, qty=1, note=None):
    return {"item_id": item_id, "qty": qty, "note": note}


def test_place_dine_in_order_shows_on_kitchen_with_table_and_notes(conn):
    r = o.place_order(conn, "dine_in", [L(1, 2, "少辣、不要香菜"), L(2)], table_no="A3", note="有花生過敏")
    assert r["round_no"] == 1 and not r["is_addon"]
    t = o.kitchen_tickets(conn)
    assert len(t) == 1
    assert t[0]["where"] == "桌 A3" and t[0]["order_note"] == "有花生過敏"
    assert {(l["name"], l["qty"], l["note"]) for l in t[0]["lines"]} == {("咖哩飯", 2, "少辣、不要香菜"), ("紅茶", 1, None)}
    assert t[0]["status"] == "new"


def test_validation(conn):
    with pytest.raises(s.OpsError):
        o.place_order(conn, "dine_in", [L(1)])             # 沒桌號
    with pytest.raises(s.OpsError):
        o.place_order(conn, "takeout", [])                  # 空單
    with pytest.raises(s.OpsError):
        o.place_order(conn, "takeout", [L(3)])              # 保健品不能走點餐
    with pytest.raises(s.OpsError):
        o.place_order(conn, "takeout", [L(1, 0)])
    assert conn.execute("SELECT COUNT(*) FROM meal_order").fetchone()[0] == 0


def test_addon_goes_to_same_open_order_as_new_round(conn):
    a = o.place_order(conn, "dine_in", [L(1)], table_no="A3")
    b = o.place_order(conn, "dine_in", [L(2, 2, "去冰")], table_no="A3", note="加急")
    assert b["order_id"] == a["order_id"] and b["round_no"] == 2 and b["is_addon"]
    tickets = o.kitchen_tickets(conn)
    assert [(t["round_no"], t["is_addon"]) for t in tickets] == [(1, False), (2, True)]
    assert o.open_orders(conn)[0]["note"] == "加急"
    other = o.place_order(conn, "dine_in", [L(1)], table_no="B1")
    assert other["order_id"] != a["order_id"]


def test_takeout_pickup_numbers_increment_per_day(conn):
    a = o.place_order(conn, "takeout", [L(1)], today=date(2026, 11, 4))
    b = o.place_order(conn, "takeout", [L(1)], today=date(2026, 11, 4))
    assert (a["pickup_no"], b["pickup_no"]) == (1, 2)
    assert o.kitchen_tickets(conn)[0]["where"] == "外帶 #1"


def test_kitchen_flow_and_serving(conn):
    r = o.place_order(conn, "dine_in", [L(1), L(2)], table_no="A3")
    assert o.advance_ticket(conn, r["order_id"], 1) == "preparing"
    assert o.kitchen_tickets(conn)[0]["status"] == "preparing"
    assert o.advance_ticket(conn, r["order_id"], 1) == "ready"
    assert o.kitchen_tickets(conn) == []                    # 出餐後離開廚房看板
    od = o.open_orders(conn)[0]
    assert od["to_serve"] == 2 and od["unfinished"] == 0
    with pytest.raises(s.OpsError):
        o.advance_ticket(conn, r["order_id"], 1)
    for ln in od["lines"]:
        o.serve_line(conn, ln["id"])
    assert o.open_orders(conn)[0]["to_serve"] == 0
    with pytest.raises(s.OpsError):
        o.serve_line(conn, od["lines"][0]["id"])


def test_cancel_only_before_cooking(conn):
    r = o.place_order(conn, "dine_in", [L(1), L(2)], table_no="A3")
    ids = [l["id"] for l in o.open_orders(conn)[0]["lines"]]
    o.cancel_line(conn, ids[1])
    assert o.open_orders(conn)[0]["total"] == 150
    assert len(o.kitchen_tickets(conn)[0]["lines"]) == 1
    o.advance_ticket(conn, r["order_id"], 1)
    with pytest.raises(s.OpsError):
        o.cancel_line(conn, ids[0])
    with pytest.raises(s.OpsError):
        o.cancel_order(conn, r["order_id"])


def test_cancel_whole_order(conn):
    r = o.place_order(conn, "takeout", [L(1)])
    o.cancel_order(conn, r["order_id"])
    assert o.open_orders(conn) == [] and o.kitchen_tickets(conn) == []


def test_checkout_writes_sale_and_income_in_one_transaction(conn):
    r = o.place_order(conn, "dine_in", [L(1, 2, "少辣"), L(2, 1)], table_no="A3")
    o.place_order(conn, "dine_in", [L(2, 2)], table_no="A3")             # 加點
    sale_id = o.checkout_order(conn, r["order_id"], "現金")
    assert conn.execute("SELECT total FROM sale WHERE id=?", (sale_id,)).fetchone()[0] == 2 * 150 + 40 + 2 * 40
    rep = s.report(conn, date.today(), date.today())
    assert rep["income"] == 420
    assert o.open_orders(conn) == []
    assert conn.execute("SELECT COUNT(*) FROM stock_move").fetchone()[0] == 0   # 餐點不扣庫存
    with pytest.raises(s.OpsError):
        o.checkout_order(conn, r["order_id"], "現金")                   # 不能重複結帳
    # 結帳後同桌再點 = 新訂單
    again = o.place_order(conn, "dine_in", [L(1)], table_no="A3")
    assert again["order_id"] != r["order_id"] and again["round_no"] == 1


def test_checkout_ignores_cancelled_lines_and_empty_fails(conn):
    r = o.place_order(conn, "takeout", [L(1), L(2)])
    ids = [l["id"] for l in o.open_orders(conn)[0]["lines"]]
    o.cancel_line(conn, ids[0])
    o.checkout_order(conn, r["order_id"], "現金")
    assert s.report(conn, date.today(), date.today())["income"] == 40
    r2 = o.place_order(conn, "takeout", [L(1)])
    o.cancel_line(conn, o.open_orders(conn)[0]["lines"][0]["id"])
    with pytest.raises(s.OpsError):
        o.checkout_order(conn, r2["order_id"], "現金")


def test_void_paid_meal_sale_reverses_income(conn):
    r = o.place_order(conn, "takeout", [L(1)])
    sid = o.checkout_order(conn, r["order_id"], "現金")
    s.void_sale(conn, sid)
    assert s.report(conn, date.today(), date.today())["income"] == 0


def test_wait_minutes():
    assert o.wait_minutes("2026-11-04 12:00:00", now=datetime(2026, 11, 4, 12, 7, 30)) == 7
