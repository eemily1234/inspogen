from datetime import date

import pytest

from ops import db, einvoice as ei, orders as o, services as s

DAY = date(2026, 11, 19)          # 期別 11512（11–12 月）


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    s.create_user(c, "老闆", s.OWNER, "1234")
    s.add_item(c, "咖哩飯", "meal", 150)
    s.add_item(c, "紅茶", "meal", 40)
    s.add_item(c, "有機糙米", "organic", 300)
    s.purchase(c, 3, 10, 1000)
    ei.set_enabled(c, True)
    ei.add_track(c, "11512", "AB", 12345000, 12345002)
    return c


def L(item_id, qty=1):
    return {"item_id": item_id, "qty": qty, "note": None, "seat": None}


def test_validators():
    assert ei.valid_ban("04595257") and ei.valid_ban("10458575")       # 第 7 碼為 7 的特例
    assert not ei.valid_ban("12345678") and not ei.valid_ban("1234567")
    assert ei.valid_mobile_barcode("/ABC+123") and not ei.valid_mobile_barcode("ABC+1234")
    assert ei.valid_cdc("AB12345678901234") and not ei.valid_cdc("A123")
    assert ei.valid_donate_code("919") and not ei.valid_donate_code("12")
    assert ei.period_of(date(2026, 1, 5)) == "11502" and ei.period_of(DAY) == "11512"
    with pytest.raises(s.OpsError, match="統一編號"):
        ei.normalize_buyer({"kind": "ban", "value": "12345678"})
    assert ei.normalize_buyer({"kind": "mobile", "value": "/abc+123"}) == {"kind": "mobile", "value": "/ABC+123"}


def test_meal_checkout_issues_invoice_in_sequence_with_tax(conn):
    r = o.place_order(conn, "dine_in", [L(1, 2), L(2)], table_no="101")
    sid = o.checkout_order(conn, r["order_id"], "現金", today=DAY, invoice={"kind": "ban", "value": "04595257"})
    inv = ei.for_sale(conn, sid)
    assert inv["number"] == "AB12345000" and inv["amount"] == 340
    assert inv["sales_amount"] + inv["tax"] == 340 and inv["tax"] == 16
    assert inv["buyer_ban"] == "04595257" and len(inv["random_code"]) == 4
    assert conn.execute("SELECT invoice_no FROM sale WHERE id=?", (sid,)).fetchone()[0] == "AB12345000"
    assert ei.queue_counts(conn)["pending"] == 1
    # 第二張接著號碼；零售 POS 也一樣
    sid2 = s.sell(conn, [(3, 1)], "現金", today=DAY, invoice={"kind": "mobile", "value": "/ABC+123"})
    assert ei.for_sale(conn, sid2)["number"] == "AB12345001"
    assert ei.remaining(conn, DAY) == 1


def test_bad_carrier_blocks_checkout_without_writing_anything(conn):
    r = o.place_order(conn, "dine_in", [L(1)], table_no="101")
    with pytest.raises(s.OpsError, match="手機條碼"):
        o.checkout_order(conn, r["order_id"], "現金", today=DAY, invoice={"kind": "mobile", "value": "123"})
    assert conn.execute("SELECT COUNT(*) FROM sale").fetchone()[0] == 0


def test_numbers_run_out_rolls_back_the_sale(conn):
    for _ in range(3):
        s.sell(conn, [(3, 1)], "現金", today=DAY)
    with pytest.raises(s.OpsError, match="號碼用完"):
        s.sell(conn, [(3, 1)], "現金", today=DAY)
    assert conn.execute("SELECT COUNT(*) FROM sale").fetchone()[0] == 3
    assert s.stock_level(conn, 3) == 7


def test_uber_disabled_and_free_orders_skip_invoice(conn):
    sid = s.sell(conn, [(1, 1)], "Uber Eats", today=DAY)
    assert ei.for_sale(conn, sid) is None
    ei.set_enabled(conn, False)
    assert ei.for_sale(conn, s.sell(conn, [(1, 1)], "現金", today=DAY)) is None


def test_void_sale_voids_invoice_same_period_only(conn):
    sid = s.sell(conn, [(3, 1)], "現金", today=DAY)
    s.void_sale(conn, sid, today=DAY)
    inv = ei.for_sale(conn, sid)
    assert inv["status"] == "void"
    assert [e["kind"] for e in ei.pending_events(conn)] == ["issue", "void"]
    sid2 = s.sell(conn, [(3, 1)], "現金", today=DAY)
    with pytest.raises(s.OpsError, match="跨期"):
        s.void_sale(conn, sid2, today=date(2027, 1, 3))
    assert conn.execute("SELECT status FROM sale WHERE id=?", (sid2,)).fetchone()[0] == "done"


def test_track_validation_and_overlap(conn):
    with pytest.raises(s.OpsError, match="期別"):
        ei.add_track(conn, "11511", "AB", 1, 10)
    with pytest.raises(s.OpsError, match="重疊"):
        ei.add_track(conn, "11512", "AB", 12345002, 12345100)
    ei.add_track(conn, "11512", "AC", 12345002, 12345100)


def test_sync_sends_in_order_and_retries_failures(conn):
    a = s.sell(conn, [(3, 1)], "現金", today=DAY)
    s.void_sale(conn, a, today=DAY)
    s.sell(conn, [(3, 1)], "現金", today=DAY)

    class Flaky:
        def __init__(self):
            self.sent = []

        def send(self, ev):
            if ev["number"] == "AB12345000" and not getattr(self, "ok", False):
                raise RuntimeError("timeout")
            self.sent.append((ev["number"], ev["kind"]))

    p = Flaky()
    assert ei.sync(conn, p) == {"sent": 1, "failed": 1}       # 開立失敗 → 同一張的作廢先不送
    assert p.sent == [("AB12345001", "issue")]
    p.ok = True
    assert ei.sync(conn, p) == {"sent": 2, "failed": 0}
    assert p.sent[1:] == [("AB12345000", "issue"), ("AB12345000", "void")]
    assert ei.queue_counts(conn)["pending"] == 0
