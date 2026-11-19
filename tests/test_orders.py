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


def L(item_id, qty=1, note=None, seat=None):
    return {"item_id": item_id, "qty": qty, "note": note, "seat": seat}


def _serve_and_close(conn, order_id):
    """把這張單的餐全部做完、上完，然後客人離開（訂單完成）。"""
    for station in ("kitchen", "bar"):
        while True:
            ts = [t for t in o.kitchen_tickets(conn, station) if t["order_id"] == order_id]
            if not ts:
                break
            for t in ts:
                o.advance_ticket(conn, order_id, t["round_no"], station)
    try:
        o.serve_all_ready(conn, order_id)
    except s.OpsError:
        pass
    o.close_order(conn, order_id)


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
    a = o.place_order(conn, "takeout", [L(1)])
    b = o.place_order(conn, "takeout", [L(1)])
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
    assert o.open_orders(conn)[0]["paid_all"]                           # 結清了，但客人還沒離開 → 訂單仍在
    _serve_and_close(conn, r["order_id"])
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
    o.cancel_line(conn, next(x for x in o.open_orders(conn) if x["id"] == r2["order_id"])["lines"][0]["id"])
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
    _serve_and_close(conn, oid)
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
    _serve_and_close(conn, oid)
    assert o.open_orders(conn) == []
    assert s.report(conn, date.today(), date.today())["income"] == 450


def test_void_split_payment_restores_paid_qty_and_reopens(conn):
    oid, ids = _order(conn)
    s1 = o.checkout_lines(conn, oid, [(ids["咖哩飯"], 3)], "現金")
    s2 = o.checkout_order(conn, oid, "現金")                                         # 結清
    _serve_and_close(conn, oid)                                                      # 客人離開 → 完成
    assert o.open_orders(conn) == []
    s.void_sale(conn, s2)                                                            # 作廢第二筆
    od = o.open_orders(conn)[0]
    assert od["unpaid_total"] == 80 and od["paid_total"] == 450
    assert s.report(conn, date.today(), date.today())["income"] == 450
    # 新訂單佔用同桌後，不能再還原舊訂單
    o.checkout_order(conn, oid, "現金")
    _serve_and_close(conn, oid)
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
    _serve_and_close(conn, a["order_id"])
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


# ───────────── 桌位／外場總覽／廚房接單 ─────────────
@pytest.fixture
def floors(conn):
    o.setup_tables(conn, {1: 3, 2: 2})
    return conn


def test_setup_tables_two_floors_four_seats(floors):
    ts = o.list_tables(floors)
    assert [t["name"] for t in ts] == ["101", "102", "103", "201", "202"]
    assert {t["seats"] for t in ts} == {4} and {t["floor"] for t in ts} == {1, 2}
    o.setup_tables(floors, {1: 4, 2: 2})                      # 加一桌
    assert len(o.list_tables(floors)) == 6
    o.setup_tables(floors, {1: 2, 2: 2})                      # 減少空桌
    assert [t["name"] for t in o.list_tables(floors)] == ["101", "102", "201", "202"]


def test_cannot_remove_table_in_use_and_validation(floors):
    o.place_order(floors, "dine_in", [L(1)], table_no="102")
    with pytest.raises(s.OpsError):
        o.setup_tables(floors, {1: 1, 2: 2})
    assert len(o.list_tables(floors)) == 5                     # 失敗整筆回復
    with pytest.raises(s.OpsError):
        o.setup_tables(floors, {10: 1})


def test_dine_in_must_use_configured_table_once_tables_exist(floors):
    with pytest.raises(s.OpsError):
        o.place_order(floors, "dine_in", [L(1)], table_no="999")
    r = o.place_order(floors, "dine_in", [L(1)], table_no="101", guests=4)
    assert o.open_orders(floors)[0]["guests"] == 4 and r["table_no"] == "101"
    with pytest.raises(s.OpsError):
        o.place_order(floors, "dine_in", [L(1)], table_no="102", guests=0)


def test_floor_overview_stages_and_flow_counts(floors):
    a = o.place_order(floors, "dine_in", [L(1), L(2)], table_no="101", guests=4)
    b = o.place_order(floors, "dine_in", [L(1)], table_no="201", guests=2)
    c = o.place_order(floors, "dine_in", [L(2)], table_no="202", guests=3)
    o.place_order(floors, "takeout", [L(1)])
    o.advance_ticket(floors, b["order_id"], 1)                                   # 201 製作中
    o.advance_ticket(floors, c["order_id"], 1); o.advance_ticket(floors, c["order_id"], 1)   # 202 待上菜
    ov = o.floor_overview(floors)
    stage = {t["name"]: t["stage"] for t in ov["tables"]}
    assert stage == {"101": "new", "102": "empty", "103": "empty", "201": "preparing", "202": "ready"}
    assert ov["flow"] == {"new": 1, "preparing": 1, "ready": 1, "dining": 0, "empty": 2}
    assert ov["floors"] == [1, 2] and len(ov["takeouts"]) == 1
    t202 = next(t for t in ov["tables"] if t["name"] == "202")
    assert t202["to_serve"] == 1 and t202["guests"] == 3
    assert ov["kitchen"]["accepting"] is True


def test_ready_has_priority_and_serving_moves_table_to_dining(floors):
    r = o.place_order(floors, "dine_in", [L(1), L(2)], table_no="101")
    o.advance_ticket(floors, r["order_id"], 1); o.advance_ticket(floors, r["order_id"], 1)   # 全部 ready
    o.place_order(floors, "dine_in", [L(1)], table_no="101")                                # 加點（新單）
    t = next(t for t in o.floor_overview(floors)["tables"] if t["name"] == "101")
    assert t["stage"] == "ready" and t["counts"]["new"] == 1 and t["to_serve"] == 2
    assert o.serve_all_ready(floors, r["order_id"]) == 2
    t = next(t for t in o.floor_overview(floors)["tables"] if t["name"] == "101")
    assert t["stage"] == "new"
    o.advance_ticket(floors, r["order_id"], 2); o.advance_ticket(floors, r["order_id"], 2)
    o.serve_all_ready(floors, r["order_id"])
    t = next(t for t in o.floor_overview(floors)["tables"] if t["name"] == "101")
    assert t["stage"] == "dining"
    with pytest.raises(s.OpsError):
        o.serve_all_ready(floors, r["order_id"])


def test_kitchen_pause_blocks_new_orders_but_not_existing_ones(floors):
    r = o.place_order(floors, "dine_in", [L(1)], table_no="101")
    o.set_kitchen_accepting(floors, False, "客滿，暫停 20 分鐘")
    assert o.kitchen_status(floors) == {"accepting": False, "reason": "客滿，暫停 20 分鐘"}
    with pytest.raises(s.OpsError, match="暫停接單：客滿"):
        o.place_order(floors, "dine_in", [L(1)], table_no="102")
    with pytest.raises(s.OpsError):
        o.place_order(floors, "takeout", [L(1)])
    with pytest.raises(s.OpsError):
        o.place_order(floors, "dine_in", [L(2)], table_no="101")                 # 加點也不行
    assert o.advance_ticket(floors, r["order_id"], 1) == "preparing"             # 已送出的單照常處理
    o.checkout_order(floors, r["order_id"], "現金")
    o.set_kitchen_accepting(floors, True)
    assert o.kitchen_status(floors) == {"accepting": True, "reason": ""}
    o.place_order(floors, "dine_in", [L(1)], table_no="102")


def test_merge_adds_guests(floors):
    o.place_order(floors, "dine_in", [L(1)], table_no="101", guests=4)
    o.place_order(floors, "dine_in", [L(1)], table_no="102", guests=3)
    o.merge_tables(floors, "102", "101")
    assert o.open_orders(floors)[0]["guests"] == 7
    t = {t["name"]: t for t in o.floor_overview(floors)["tables"]}
    assert t["101"]["guests"] == 7 and t["102"]["stage"] == "empty"


def test_legacy_order_on_unconfigured_table_still_visible(conn):
    o.place_order(conn, "dine_in", [L(1)], table_no="A3")                        # 尚未設定桌位前的舊單
    o.setup_tables(conn, {1: 2})
    ov = o.floor_overview(conn)
    assert [t["name"] for t in ov["tables"] if t["floor"] == 0] == ["A3"]


def test_ensure_default_tables_once(conn):
    assert o.ensure_default_tables(conn) is True
    ts = o.list_tables(conn)
    assert [t["name"] for t in ts] == ["101", "102", "103", "104", "201", "202", "203", "204"]
    assert all(t["seats"] == 4 for t in ts)
    assert o.ensure_default_tables(conn) is False
    o.setup_tables(conn, {1: 0, 2: 0})
    assert o.ensure_default_tables(conn) is False and o.list_tables(conn) == []   # 不會又被自動建回來


# ───────────── 每桌餐點、備料總表、廚房呼叫外場 ─────────────
def test_floor_tiles_list_each_tables_dishes_with_status(floors):
    r = o.place_order(floors, "dine_in", [L(1, 2, "少辣"), L(2)], table_no="101", guests=3)
    o.advance_ticket(floors, r["order_id"], 1)
    o.place_order(floors, "dine_in", [L(2, 1)], table_no="101")                     # 加點
    ids = [l["id"] for l in o.open_orders(floors)[0]["lines"]]
    t = {x["name"]: x for x in o.floor_overview(floors)["tables"]}
    assert [(d["name"], d["qty"], d["status"]) for d in t["101"]["dishes"]] == [
        ("咖哩飯", 2, "preparing"), ("紅茶", 1, "preparing"), ("紅茶", 1, "new")]
    assert t["101"]["dishes"][0]["note"] == "少辣"
    assert t["102"]["dishes"] == []
    o.cancel_line(floors, ids[2])                                                    # 取消的不畫出來
    t = {x["name"]: x for x in o.floor_overview(floors)["tables"]}
    assert len(t["101"]["dishes"]) == 2


def test_prep_summary_totals_unfinished_items_across_orders(floors):
    a = o.place_order(floors, "dine_in", [L(1, 2), L(2, 1)], table_no="101")
    o.place_order(floors, "dine_in", [L(1, 3)], table_no="102")
    o.place_order(floors, "takeout", [L(2, 2)])
    o.advance_ticket(floors, a["order_id"], 1)                                      # 101 製作中
    assert o.prep_summary(floors) == [
        {"name": "咖哩飯", "new_qty": 3, "preparing_qty": 2, "total": 5},
        {"name": "紅茶", "new_qty": 2, "preparing_qty": 1, "total": 3}]
    o.advance_ticket(floors, a["order_id"], 1)                                      # 101 出餐 → 不再列入
    assert o.prep_summary(floors) == [
        {"name": "咖哩飯", "new_qty": 3, "preparing_qty": 0, "total": 3},
        {"name": "紅茶", "new_qty": 2, "preparing_qty": 0, "total": 2}]


def test_kitchen_calls_floor_and_floor_acknowledges(floors):
    cid = o.call_floor(floors, "請來取餐", "101", user_id=1)
    o.call_floor(floors, "需要人手")
    calls = o.open_calls(floors)
    assert [(c["message"], c["table_no"]) for c in calls] == [("請來取餐", "101"), ("需要人手", None)]
    assert o.ack_call(floors, cid, user_id=1) is True
    assert o.ack_call(floors, cid, user_id=1) is False                               # 重複按不會重記
    assert [c["message"] for c in o.open_calls(floors)] == ["需要人手"]
    recent = o.recent_calls(floors)
    assert recent[1]["id"] == cid and recent[1]["acked_at"] is not None and recent[0]["acked_at"] is None
    with pytest.raises(s.OpsError):
        o.call_floor(floors, "  ")
    with pytest.raises(s.OpsError):
        o.call_floor(floors, "x" * 101)


def test_floor_can_notify_kitchen_separately_from_kitchen_calls(floors):
    o.call_floor(floors, "請來取餐", "101")                                         # 廚房 → 外場
    kid = o.call_kitchen(floors, "請加快這桌", "102", user_id=1)                     # 外場 → 廚房
    assert [c["message"] for c in o.open_calls(floors)] == ["請來取餐"]
    assert [(c["message"], c["table_no"]) for c in o.open_calls(floors, "to_kitchen")] == [("請加快這桌", "102")]
    o.ack_call(floors, kid)
    assert o.open_calls(floors, "to_kitchen") == []
    assert o.recent_calls(floors, direction="to_kitchen")[0]["acked_at"] is not None
    with pytest.raises(s.OpsError):
        o.call_floor(floors, "x", direction="sideways")


def test_tables_and_orders_carry_open_time(floors):
    o.place_order(floors, "dine_in", [L(1)], table_no="101")
    t = next(x for x in o.floor_overview(floors)["tables"] if x["name"] == "101")
    assert len(t["opened_at"]) == 19 and t["opened_at"][10] == " "                 # YYYY-MM-DD HH:MM:SS
    line = o.open_orders(floors)[0]["lines"][0]
    assert line["created_at"] and line["round_no"] == 1


# ───────────── 結帳 ≠ 完成；客人離開才完成 ─────────────
def test_paying_does_not_finish_order_until_guests_leave(floors):
    r = o.place_order(floors, "dine_in", [L(1)], table_no="101", guests=2)
    oid = r["order_id"]
    o.checkout_order(floors, oid, "現金")                                           # 先結帳
    od = o.open_orders(floors)[0]
    assert od["paid_all"] and not od["can_close"]                                    # 還沒出餐 → 不能完成
    t = next(x for x in o.floor_overview(floors)["tables"] if x["name"] == "101")
    assert t["stage"] == "new" and t["paid_all"]                                     # 桌子仍在用，標示已結帳
    with pytest.raises(s.OpsError, match="還沒上完"):
        o.close_order(floors, oid)
    o.advance_ticket(floors, oid, 1); o.advance_ticket(floors, oid, 1)
    o.serve_all_ready(floors, oid)
    assert o.open_orders(floors)[0]["can_close"]
    o.close_order(floors, oid)
    assert o.open_orders(floors) == []
    assert next(x for x in o.floor_overview(floors)["tables"] if x["name"] == "101")["stage"] == "empty"
    with pytest.raises(s.OpsError):
        o.close_order(floors, oid)


def test_cannot_close_with_money_owed_and_addon_after_paying_reopens_bill(floors):
    r = o.place_order(floors, "dine_in", [L(1)], table_no="101")
    _serve_all(floors, r["order_id"])
    with pytest.raises(s.OpsError, match="未結帳"):
        o.close_order(floors, r["order_id"])
    o.checkout_order(floors, r["order_id"], "現金")
    o.place_order(floors, "dine_in", [L(2)], table_no="101")                        # 結帳後又加點 → 同一張單
    od = o.open_orders(floors)[0]
    assert od["unpaid_total"] == 40 and not od["paid_all"]


def _serve_all(conn, oid):
    for t in [t for t in o.kitchen_tickets(conn) if t["order_id"] == oid]:
        o.advance_ticket(conn, oid, t["round_no"]); o.advance_ticket(conn, oid, t["round_no"])
    o.serve_all_ready(conn, oid)


# ───────────── 排隊單號、叫號看板 ─────────────
def test_queue_numbers_cover_all_order_types_and_board_groups(floors):
    a = o.place_order(floors, "dine_in", [L(1)], table_no="101")
    b = o.place_order(floors, "takeout", [L(2)])
    c = o.place_external_order(floors, "uber", "u-1", [{"name": "咖哩飯", "qty": 1}], label="AB12")
    o.place_order(floors, "dine_in", [L(2)], table_no="101")                        # 加點不換單號
    nums = {x["id"]: x["queue"] for x in o.open_orders(floors)}
    assert [nums[a["order_id"]], nums[b["order_id"]], nums[c["order_id"]]] == ["#001", "#002", "#003"]
    assert a["queue_no"] == 1 and b["queue_no"] == 2
    o.advance_ticket(floors, b["order_id"], 1); o.advance_ticket(floors, b["order_id"], 1)   # 外帶做好了
    board = o.queue_board(floors)
    assert [x["queue"] for x in board["cooking"]] == ["#001", "#003"]
    assert [(x["queue"], x["kind"]) for x in board["ready"]] == [("#002", "takeout")]
    assert board["dining"] == []
    assert [x["kind"] for x in board["cooking"]] == ["dine_in", "uber"]


# ───────────── 座位、誰點什麼、熟客 ─────────────
def test_seats_guests_and_regulars_reach_the_kitchen(floors):
    cid = o.add_customer(floors, "王小姐", "0912", avoid="不吃香菜")
    with pytest.raises(s.OpsError):
        o.add_customer(floors, "王小姐")
    r = o.place_order(floors, "dine_in", [L(1, 1, "少辣", seat=1), L(2, 1, seat=2), L(2, 1)], table_no="101",
                      seats={1: {"customer_id": cid, "avoid": "蝦過敏"}, 2: {"guest_name": "陳先生"}})
    seats = {x["seat_no"]: x for x in o.open_orders(floors)[0]["seats"]}
    assert seats[1]["guest_name"] == "王小姐" and seats[1]["avoid"] == "不吃香菜；蝦過敏"   # 熟客忌口自動帶入
    assert seats[2]["guest_name"] == "陳先生" and seats[2]["avoid"] is None
    lines = o.kitchen_tickets(floors)[0]["lines"]
    first = lines[0]
    assert (first["seat_no"], first["guest"], first["avoid"], first["regular"]) == (1, "王小姐", "不吃香菜；蝦過敏", True)
    assert (lines[1]["seat_no"], lines[1]["guest"]) == (2, "陳先生")
    assert lines[2]["seat_no"] is None                                                # 共用
    o.set_seat(floors, r["order_id"], 3, guest_name="小朋友", avoid="不吃辣")        # 之後補記
    o.set_seat(floors, r["order_id"], 2)                                             # 全部留白＝清除
    assert [x["seat_no"] for x in o.order_seats(floors, r["order_id"])] == [1, 3]
    o.update_customer(floors, cid, avoid="不吃香菜、不吃牛")
    assert o.get_customer(floors, cid)["avoid"] == "不吃香菜、不吃牛"
    o.update_customer(floors, cid, active=0)
    assert o.list_customers(floors) == []


# ───────────── 套餐：主餐送廚房、飲料另外送吧台 ─────────────
@pytest.fixture
def combo(floors):
    o.set_station(floors, 2, "bar")                                                  # 紅茶 → 吧台
    combo_id = s.add_item(floors, "咖哩套餐", "meal", 170)
    o.set_combo(floors, combo_id, [(1, 1), (2, 1)])
    return combo_id


def test_combo_splits_into_kitchen_and_bar_tickets(floors, combo):
    r = o.place_order(floors, "dine_in", [{"item_id": combo, "qty": 2, "seat": 1}], table_no="101")
    k = o.kitchen_tickets(floors, "kitchen")
    b = o.kitchen_tickets(floors, "bar")
    assert [(l["name"], l["qty"], l["combo"]) for l in k[0]["lines"]] == [("咖哩飯", 2, "咖哩套餐")]
    assert [(l["name"], l["qty"], l["combo"]) for l in b[0]["lines"]] == [("紅茶", 2, "咖哩套餐")]
    assert o.prep_summary(floors, "bar") == [{"name": "紅茶", "new_qty": 2, "preparing_qty": 0, "total": 2}]
    od = o.open_orders(floors)[0]
    assert [(l["name"], l["unit_price"], len(l["components"])) for l in od["lines"]] == [("咖哩套餐", 170, 2)]
    assert od["total"] == 340                                                        # 只算套餐價
    # 廚房做好主餐、吧台還沒做飲料 → 套餐狀態跟著最慢的走
    o.advance_ticket(floors, r["order_id"], 1, "kitchen"); o.advance_ticket(floors, r["order_id"], 1, "kitchen")
    assert o.open_orders(floors)[0]["lines"][0]["status"] == "new"
    o.advance_ticket(floors, r["order_id"], 1, "bar")
    assert o.open_orders(floors)[0]["lines"][0]["status"] == "preparing"
    o.serve_line(floors, od["lines"][0]["id"])                                      # 先上已完成的主餐
    o.advance_ticket(floors, r["order_id"], 1, "bar")
    o.serve_all_ready(floors, r["order_id"])
    assert o.open_orders(floors)[0]["lines"][0]["status"] == "served"
    o.checkout_order(floors, r["order_id"], "現金")
    assert s.report(floors, date.today(), date.today())["income"] == 340
    o.close_order(floors, r["order_id"])


def test_combo_cancel_rules_and_validation(floors, combo):
    r = o.place_order(floors, "dine_in", [{"item_id": combo, "qty": 1}], table_no="101")
    od = o.open_orders(floors)[0]
    parent = od["lines"][0]
    with pytest.raises(s.OpsError, match="整份套餐"):
        o.cancel_line(floors, parent["components"][0]["id"])
    o.cancel_line(floors, parent["id"])                                              # 都還沒做 → 可整份取消
    assert o.kitchen_tickets(floors, "kitchen") == [] and o.kitchen_tickets(floors, "bar") == []
    r2 = o.place_order(floors, "dine_in", [{"item_id": combo, "qty": 1}], table_no="102")
    o.advance_ticket(floors, r2["order_id"], 1, "bar")
    p2 = next(x for x in o.open_orders(floors) if x["id"] == r2["order_id"])["lines"][0]
    with pytest.raises(s.OpsError, match="開始製作"):
        o.cancel_line(floors, p2["id"])
    with pytest.raises(s.OpsError):
        o.set_combo(floors, combo, [(combo, 1)])                                     # 不能包含自己
    with pytest.raises(s.OpsError):
        o.set_combo(floors, 1, [(combo, 1)])                                         # 套餐不能放進套餐
    with pytest.raises(s.OpsError):
        o.set_station(floors, 1, "外太空")
    o.set_combo(floors, combo, [])                                                   # 清空＝變回單點
    assert o.combo_components(floors, combo) == []


def test_floor_can_notify_bar(floors):
    o.call_kitchen(floors, "飲料先上", "桌 101", station="bar")
    assert [c["message"] for c in o.open_calls(floors, "to_bar")] == ["飲料先上"]
    assert o.open_calls(floors, "to_kitchen") == []


# ───────────── 結帳：折扣、優惠備註、招待（贈品） ─────────────
def test_checkout_with_discount_and_note_records_net_income(conn):
    r = o.place_order(conn, "dine_in", [L(1, 2), L(2, 1)], table_no="A3")          # 150×2 + 40 = 340
    sid = o.checkout_order(conn, r["order_id"], "現金", discount=34, note="生日 9 折")
    sale = conn.execute("SELECT * FROM sale WHERE id=?", (sid,)).fetchone()
    assert (sale["total"], sale["discount"], sale["note"]) == (306, 34, "生日 9 折")
    assert s.report(conn, date.today(), date.today())["income"] == 306               # 帳上收入是折扣後
    led = conn.execute("SELECT note FROM ledger WHERE ref_id=? AND kind='income'", (sid,)).fetchone()[0]
    assert "折扣 $34" in led and "生日 9 折" in led
    assert o.open_orders(conn)[0]["paid_all"]                                       # 已結清（份數全付）
    s.void_sale(conn, sid)                                                            # 作廢以實收金額沖回
    assert s.report(conn, date.today(), date.today())["income"] == 0


def test_discount_bounds_and_presets(conn):
    r = o.place_order(conn, "takeout", [L(1)])
    with pytest.raises(s.OpsError):
        o.checkout_order(conn, r["order_id"], "現金", discount=151)
    with pytest.raises(s.OpsError):
        o.checkout_order(conn, r["order_id"], "現金", discount=-1)
    assert conn.execute("SELECT COUNT(*) FROM sale").fetchone()[0] == 0              # 失敗不入帳
    assert o.discount_amount(340, "p9") == 34 and o.discount_amount(340, "p85") == 51
    assert o.discount_amount(345, "p95") == 17                                       # 345×0.95=327.75→328
    assert o.discount_amount(340, "amount", 50) == 50 and o.discount_amount(340, "amount", 999) == 340
    assert o.discount_amount(340, "none") == 0
    with pytest.raises(s.OpsError):
        o.discount_amount(340, "half")


def test_gift_items_are_free_but_still_made_and_recorded(conn):
    r = o.place_order(conn, "dine_in", [L(1), {"item_id": 2, "qty": 1, "gift": True}], table_no="A3")
    od = o.open_orders(conn)[0]
    tea = next(l for l in od["lines"] if l["name"] == "紅茶")
    assert tea["gift"] == 1 and tea["unit_price"] == 0 and od["total"] == 150        # 招待不收錢
    assert len(o.kitchen_tickets(conn)[0]["lines"]) == 2                             # 但廚房照做
    curry = next(l for l in od["lines"] if l["name"] == "咖哩飯")
    o.set_gift(conn, curry["id"], "熟客招待")                                         # 結帳前改成招待
    od = o.open_orders(conn)[0]
    assert od["total"] == 0 and od["unpaid_total"] == 0
    assert "招待：熟客招待" in next(l for l in od["lines"] if l["name"] == "咖哩飯")["note"]
    _serve_and_close(conn, r["order_id"])                                            # 全部招待也能完成
    assert o.open_orders(conn) == []


def test_gift_rules(conn):
    r = o.place_order(conn, "dine_in", [L(1), L(2)], table_no="A3")
    ids = {l["name"]: l["id"] for l in o.open_orders(conn)[0]["lines"]}
    o.checkout_lines(conn, r["order_id"], [(ids["咖哩飯"], 1)], "現金")
    with pytest.raises(s.OpsError, match="已結帳"):
        o.set_gift(conn, ids["咖哩飯"])
    o.cancel_line(conn, ids["紅茶"])
    with pytest.raises(s.OpsError):
        o.set_gift(conn, ids["紅茶"])                                                # 已取消的不行


# ───────────── 一鍵售完、剩餘份數 ─────────────
def test_sold_out_blocks_ordering_and_restores(floors):
    o.set_sold_out(floors, 1)
    assert o.availability(floors)[1] == {"ok": False, "left": None, "why": "售完"}
    with pytest.raises(s.OpsError, match="已售完"):
        o.place_order(floors, "dine_in", [L(1)], table_no="101")
    assert o.open_orders(floors) == []                                              # 整筆不成立
    o.set_sold_out(floors, 1, False)
    o.place_order(floors, "dine_in", [L(1)], table_no="101")


def test_portions_count_down_auto_sell_out_and_cancel_gives_back(floors):
    o.set_portions(floors, 1, 3)
    r = o.place_order(floors, "dine_in", [L(1, 2)], table_no="101")
    assert o.availability(floors)[1]["left"] == 1
    with pytest.raises(s.OpsError, match="只剩 1 份"):
        o.place_order(floors, "dine_in", [L(1, 2)], table_no="102")
    o.place_order(floors, "dine_in", [L(1, 1)], table_no="102")
    assert o.availability(floors)[1] == {"ok": False, "left": 0, "why": "售完"}      # 賣到 0 自動售完
    o.cancel_line(floors, o.open_orders(floors)[0]["lines"][0]["id"])               # 取消 2 份 → 還回去
    assert o.availability(floors)[1] == {"ok": True, "left": 2, "why": ""}
    o.set_portions(floors, 1, None)
    assert o.availability(floors)[1]["left"] is None
    with pytest.raises(s.OpsError):
        o.set_portions(floors, 1, -1)


def test_combo_unavailable_when_component_sold_out_and_consumes_components(floors):
    combo = s.add_item(floors, "咖哩套餐", "meal", 170)
    o.set_combo(floors, combo, [(1, 1), (2, 1)])
    o.set_portions(floors, 2, 1)                                                     # 紅茶只剩 1 杯
    assert o.availability(floors)[combo]["left"] == 1
    o.place_order(floors, "dine_in", [{"item_id": combo, "qty": 1}], table_no="101")
    assert o.availability(floors)[combo] == {"ok": False, "left": 0, "why": "紅茶售完"}
    with pytest.raises(s.OpsError, match="紅茶"):
        o.place_order(floors, "dine_in", [{"item_id": combo, "qty": 1}], table_no="102")


# ───────────── 加價選項 ─────────────
def test_parse_choices():
    assert o.parse_choices("大份+20, 小份-10，正常、加蛋 + 15") == [("大份", 20), ("小份", -10), ("正常", 0), ("加蛋", 15)]
    assert o.parse_choices("  ") == []


def test_options_change_price_and_reach_the_kitchen(floors):
    size = o.add_option_group(floors, 1, "份量", "小份-10, 正常, 大份+20", required=True)
    o.add_option_group(floors, 1, "加料", "加蛋+15, 加起司+20", multi=True)
    g = {x["name"]: x for x in o.item_options(floors, 1)}
    cid = {c["name"]: c["id"] for grp in g.values() for c in grp["choices"]}
    with pytest.raises(s.OpsError, match="請選擇份量"):
        o.place_order(floors, "dine_in", [L(1)], table_no="101")
    with pytest.raises(s.OpsError, match="只能選一個"):
        o.place_order(floors, "dine_in", [{"item_id": 1, "qty": 1, "options": [cid["大份"], cid["小份"]]}], table_no="101")
    o.place_order(floors, "dine_in", [{"item_id": 1, "qty": 2, "options": [cid["大份"], cid["加蛋"], cid["加起司"]]}],
                  table_no="101")
    line = o.open_orders(floors)[0]["lines"][0]
    assert line["unit_price"] == 150 + 20 + 15 + 20 and line["options"] == "大份(+20)、加蛋(+15)、加起司(+20)"
    assert o.open_orders(floors)[0]["total"] == 2 * 205
    assert o.kitchen_tickets(floors)[0]["lines"][0]["options"] == "大份(+20)、加蛋(+15)、加起司(+20)"
    with pytest.raises(s.OpsError, match="選項不正確"):                              # 別的品項的選項
        o.place_order(floors, "dine_in", [{"item_id": 2, "qty": 1, "options": [cid["大份"]]}], table_no="102")
    o.delete_option_group(floors, size)
    assert [x["name"] for x in o.item_options(floors, 1)] == ["加料"]
    with pytest.raises(s.OpsError):
        o.add_option_group(floors, 1, "冰量", "")


def test_gift_ignores_option_prices(floors):
    o.add_option_group(floors, 1, "份量", "大份+20", required=True)
    big = o.item_options(floors, 1)[0]["choices"][0]["id"]
    o.place_order(floors, "dine_in", [{"item_id": 1, "qty": 1, "options": [big], "gift": True}], table_no="101")
    assert o.open_orders(floors)[0]["lines"][0]["unit_price"] == 0


# ───────────── 報表 ─────────────
def test_sales_report(floors):
    a = o.place_order(floors, "dine_in", [L(1, 2), L(2, 1)], table_no="101", guests=3)     # 340
    b = o.place_order(floors, "takeout", [L(1, 1), {"item_id": 2, "qty": 1, "gift": True}])  # 150 + 招待紅茶
    o.checkout_order(floors, a["order_id"], "現金", discount=40, note="熟客")
    o.checkout_order(floors, b["order_id"], "行動支付")
    r = o.sales_report(floors, date.today(), date.today())
    assert (r["revenue"], r["discount"], r["sales"], r["orders"], r["guests"]) == (450, 40, 2, 2, 3)
    assert r["avg_order"] == 225 and r["gift_value"] == 40 and r["paid_orders"] == 2
    o.place_order(floors, "dine_in", [L(1)], table_no="102")                       # 還沒結帳的單
    r2 = o.sales_report(floors, date.today(), date.today())
    assert r2["orders"] == 3 and r2["avg_order"] == 225                             # 不會把客單價拉低
    assert r["ranking"][0] == {"name": "咖哩飯", "qty": 3, "amount": 450, "gift_qty": 0}
    assert {"name": "紅茶", "qty": 2, "amount": 40, "gift_qty": 1} in r["ranking"]
    assert r["by_pay"] == {"現金": 300, "行動支付": 150}
    assert sum(r["by_hour"].values()) == 450


# ───────────── 交班對帳 ─────────────
def test_shift_open_close_and_cash_difference(floors):
    sid = o.open_shift(floors, 2000, user_id=1, now="2026-01-01 00:00:00")
    with pytest.raises(s.OpsError, match="還沒關"):
        o.open_shift(floors, 1000)
    a = o.place_order(floors, "takeout", [L(1, 2)])
    b = o.place_order(floors, "takeout", [L(2, 1)])
    o.checkout_order(floors, a["order_id"], "現金")                                  # 300 現金
    sale_b = o.checkout_order(floors, b["order_id"], "信用卡")                        # 40 刷卡
    summ = o.shift_summary(floors, sid)
    assert summ["by_pay"] == {"現金": {"count": 1, "amount": 300}, "信用卡": {"count": 1, "amount": 40}}
    assert summ["expected_cash"] == 2300 and summ["total"] == 340
    s.void_sale(floors, sale_b)                                                      # 作廢的不算
    assert "信用卡" not in o.shift_summary(floors, sid)["by_pay"]
    res = o.close_shift(floors, 2280, user_id=1, note="找錯零錢")
    assert (res["expected_cash"], res["counted_cash"], res["diff"]) == (2300, 2280, -20)
    assert o.current_shift(floors) is None
    last = o.recent_shifts(floors)[0]
    assert last["expected_cash"] == 2300 and last["counted_cash"] == 2280 and last["note"] == "找錯零錢"
    with pytest.raises(s.OpsError):
        o.close_shift(floors, 0)
