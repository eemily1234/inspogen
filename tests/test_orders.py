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


# ───────────── 分開結帳 ─────────────
def _order(conn, table="A3"):
    r = o.place_order(conn, "dine_in", [L(1, 3), L(2, 2)], table_no=table)   # 150×3 + 40×2 = 530
    od = o.open_orders(conn)[0]
    return r["order_id"], {l["name"]: l["id"] for l in od["lines"]}


def test_split_checkout_by_items_and_partial_qty(conn):
    oid, ids = _order(conn)
    s1 = o.checkout_lines(conn, oid, [(ids["咖哩飯"], 1), (ids["紅茶"], 1)], "現金")   # 190
    od = o.open_orders(conn)[0]
    assert (od["paid_total"], od["unpaid_total"], od["total"]) == (190, 340, 530)
    assert s.report(conn, date.today(), date.today())["income"] == 190
    s2 = o.checkout_lines(conn, oid, [(ids["咖哩飯"], 2)], "行動支付")               # 300
    assert o.open_orders(conn)[0]["unpaid_total"] == 40
    assert s1 != s2
    o.checkout_order(conn, oid, "現金")                                              # 結清剩下
    assert o.open_orders(conn) == []
    assert s.report(conn, date.today(), date.today())["income"] == 530
    assert conn.execute("SELECT COUNT(*) FROM sale WHERE status='done'").fetchone()[0] == 3
    pays = [r[0] for r in conn.execute("SELECT pay_method FROM sale ORDER BY id")]
    assert pays == ["現金", "行動支付", "現金"]


def test_split_checkout_validation(conn):
    oid, ids = _order(conn)
    with pytest.raises(s.OpsError):
        o.checkout_lines(conn, oid, [], "現金")
    with pytest.raises(s.OpsError):
        o.checkout_lines(conn, oid, [(ids["紅茶"], 3)], "現金")                       # 超過份數
    o.checkout_lines(conn, oid, [(ids["紅茶"], 2)], "現金")
    with pytest.raises(s.OpsError):
        o.checkout_lines(conn, oid, [(ids["紅茶"], 1)], "現金")                       # 已付清
    assert s.report(conn, date.today(), date.today())["income"] == 80               # 失敗不入帳


def test_paid_items_cannot_be_cancelled(conn):
    oid, ids = _order(conn)
    o.checkout_lines(conn, oid, [(ids["咖哩飯"], 1)], "現金")
    with pytest.raises(s.OpsError):
        o.cancel_line(conn, ids["咖哩飯"])
    with pytest.raises(s.OpsError):
        o.cancel_order(conn, oid)
    o.cancel_line(conn, ids["紅茶"])                                                 # 沒付的仍可取消
    o.checkout_order(conn, oid, "現金")
    assert o.open_orders(conn) == []
    assert s.report(conn, date.today(), date.today())["income"] == 450


def test_void_split_payment_restores_paid_qty_and_reopens(conn):
    oid, ids = _order(conn)
    s1 = o.checkout_lines(conn, oid, [(ids["咖哩飯"], 3)], "現金")
    s2 = o.checkout_order(conn, oid, "現金")                                         # 結清 → paid
    assert o.open_orders(conn) == []
    s.void_sale(conn, s2)                                                            # 作廢第二筆
    od = o.open_orders(conn)[0]
    assert od["unpaid_total"] == 80 and od["paid_total"] == 450
    assert s.report(conn, date.today(), date.today())["income"] == 450
    # 新訂單佔用同桌後，不能再還原舊訂單
    o.checkout_order(conn, oid, "現金")
    o.place_order(conn, "dine_in", [L(1)], table_no="A3")
    with pytest.raises(s.OpsError):
        s.void_sale(conn, s1)
    assert s.report(conn, date.today(), date.today())["income"] == 530


def test_split_evenly():
    assert o.split_evenly(530, 3) == [177, 177, 176]
    assert sum(o.split_evenly(1001, 4)) == 1001
    with pytest.raises(s.OpsError):
        o.split_evenly(100, 0)


# ───────────── 併桌／換桌 ─────────────
def test_move_table_when_target_empty(conn):
    r = o.place_order(conn, "dine_in", [L(1)], table_no="A3")
    out = o.merge_tables(conn, "A3", "C9")
    assert out["action"] == "move" and out["order_id"] == r["order_id"]
    assert o.open_orders(conn)[0]["where"] == "桌 C9"
    assert o.kitchen_tickets(conn)[0]["where"] == "桌 C9"
    with pytest.raises(s.OpsError):
        o.merge_tables(conn, "A3", "D1")                                             # 原桌已沒有訂單


def test_merge_tables_combines_orders_and_keeps_kitchen_rounds_separate(conn):
    a = o.place_order(conn, "dine_in", [L(1, 1, "少辣")], table_no="A3", note="A桌備註")
    b = o.place_order(conn, "dine_in", [L(2, 2)], table_no="B1", note="B桌過敏")
    o.place_order(conn, "dine_in", [L(1)], table_no="B1")                            # B 桌加點（第 2 輪）
    o.advance_ticket(conn, a["order_id"], 1)                                         # A 桌第 1 輪已在製作
    out = o.merge_tables(conn, "B1", "A3")
    assert out["action"] == "merge" and out["order_id"] == a["order_id"]
    ods = o.open_orders(conn)
    assert len(ods) == 1 and ods[0]["where"] == "桌 A3" and len(ods[0]["lines"]) == 3
    assert ods[0]["note"] == "A桌備註；B桌過敏"
    rounds = sorted({l["round_no"] for l in ods[0]["lines"]})
    assert rounds == [1, 2, 3]                                                       # 不與 A 桌的輪次混在一起
    tickets = {(t["round_no"]): t for t in o.kitchen_tickets(conn)}
    assert tickets[1]["status"] == "preparing" and tickets[1]["origins"] == []
    assert tickets[2]["status"] == "new" and tickets[2]["origins"] == ["併自 B1"]
    assert tickets[3]["status"] == "new"
    # 併入後可正常結帳；B 桌可重新開單
    o.checkout_order(conn, a["order_id"], "現金")
    assert s.report(conn, date.today(), date.today())["income"] == 150 + 80 + 150
    again = o.place_order(conn, "dine_in", [L(1)], table_no="B1")
    assert again["order_id"] != a["order_id"] and again["round_no"] == 1


def test_merge_keeps_paid_qty(conn):
    a = o.place_order(conn, "dine_in", [L(1)], table_no="A3")
    b = o.place_order(conn, "dine_in", [L(2, 2)], table_no="B1")
    ids = {l["name"]: l["id"] for l in o.open_orders(conn)[1]["lines"]}
    o.checkout_lines(conn, b["order_id"], [(ids["紅茶"], 1)], "現金")
    o.merge_tables(conn, "B1", "A3")
    od = o.open_orders(conn)[0]
    assert od["paid_total"] == 40 and od["unpaid_total"] == 150 + 40
    sid = o.checkout_order(conn, a["order_id"], "現金")
    assert o.open_orders(conn) == []
    assert s.report(conn, date.today(), date.today())["income"] == 230


def test_merge_errors(conn):
    o.place_order(conn, "dine_in", [L(1)], table_no="A3")
    with pytest.raises(s.OpsError):
        o.merge_tables(conn, "A3", "A3")
    with pytest.raises(s.OpsError):
        o.merge_tables(conn, "", "B1")
    o.place_order(conn, "takeout", [L(1)])
    with pytest.raises(s.OpsError):
        o.merge_tables(conn, "外帶", "A3")                                           # 外帶不是桌號


def test_old_database_is_migrated(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript("""
        CREATE TABLE meal_order (id INTEGER PRIMARY KEY, order_type TEXT NOT NULL, table_no TEXT,
            pickup_no INTEGER, note TEXT, status TEXT NOT NULL DEFAULT 'open', sale_id INTEGER,
            created_by INTEGER, created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
        CREATE TABLE order_line (id INTEGER PRIMARY KEY, order_id INTEGER NOT NULL, round_no INTEGER NOT NULL DEFAULT 1,
            item_id INTEGER NOT NULL, qty INTEGER NOT NULL, unit_price INTEGER NOT NULL, note TEXT,
            status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')));
        INSERT INTO meal_order(order_type, table_no) VALUES ('dine_in','A3');
    """)
    raw.commit(); raw.close()
    c = db.connect(path)
    cols = {r["name"] for r in c.execute("PRAGMA table_info(order_line)")}
    assert {"paid_qty", "origin"} <= cols
    assert "merged_into" in {r["name"] for r in c.execute("PRAGMA table_info(meal_order)")}
    assert c.execute("SELECT COUNT(*) FROM meal_order").fetchone()[0] == 1       # 舊資料保留
    db.connect(path)                                                              # 重複執行不會出錯
