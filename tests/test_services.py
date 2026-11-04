from datetime import date

import pytest

from ops import db, services as s


@pytest.fixture
def conn(tmp_path):
    return db.connect(str(tmp_path / "t.db"))


@pytest.fixture
def goods(conn):
    health = s.add_item(conn, "魚油", "health", price=1000, safety_stock=2)
    organic = s.add_item(conn, "有機糙米", "organic", price=300, safety_stock=1)
    meal = s.add_item(conn, "咖哩飯", "meal", price=150, cost=60)
    return health, organic, meal


def test_purchase_adds_stock_and_cost(conn, goods):
    health, _, _ = goods
    s.purchase(conn, health, 10, 4000, batch_no="A1", expiry="2027-01-01")
    assert s.stock_level(conn, health) == 10
    assert s.get_item(conn, health)["cost"] == 400
    r = s.report(conn, date.today(), date.today())
    assert r["purchase_cost"] == 4000


def test_health_requires_batch_and_expiry(conn, goods):
    health, organic, _ = goods
    with pytest.raises(s.OpsError):
        s.purchase(conn, health, 5, 100)
    with pytest.raises(s.OpsError):
        s.purchase(conn, health, 5, 100, batch_no="A1")
    s.purchase(conn, organic, 5, 100)  # 有機不強制


def test_sale_syncs_money_and_stock(conn, goods):
    health, organic, meal = goods
    s.purchase(conn, health, 5, 2000, batch_no="A1", expiry="2027-01-01")
    sid = s.sell(conn, [(health, 2), (meal, 3)], "cash")
    assert s.stock_level(conn, health) == 3
    r = s.report(conn, date.today(), date.today())
    assert r["income"] == 2 * 1000 + 3 * 150
    assert conn.execute("SELECT total FROM sale WHERE id=?", (sid,)).fetchone()[0] == 2450


def test_meal_does_not_touch_stock(conn, goods):
    _, _, meal = goods
    s.sell(conn, [(meal, 2)], "cash")
    assert conn.execute("SELECT COUNT(*) FROM stock_move").fetchone()[0] == 0
    with pytest.raises(s.OpsError):
        s.purchase(conn, meal, 1, 10)


def test_fefo_sells_earliest_expiry_first(conn, goods):
    health, _, _ = goods
    s.purchase(conn, health, 5, 1000, batch_no="LATE", expiry="2028-01-01")
    s.purchase(conn, health, 5, 1000, batch_no="EARLY", expiry="2026-12-01")
    s.sell(conn, [(health, 7)], "cash")
    left = {b["batch_no"]: b["qty"] for b in s.batches(conn, health)}
    assert left == {"LATE": 3}


def test_insufficient_stock_rolls_back_everything(conn, goods):
    health, _, meal = goods
    s.purchase(conn, health, 2, 800, batch_no="A", expiry="2027-01-01")
    with pytest.raises(s.OpsError):
        s.sell(conn, [(meal, 1), (health, 3)], "cash")
    assert conn.execute("SELECT COUNT(*) FROM sale").fetchone()[0] == 0
    assert s.stock_level(conn, health) == 2
    assert s.report(conn, date.today(), date.today())["income"] == 0


def test_void_reverses_stock_and_income(conn, goods):
    health, _, _ = goods
    s.purchase(conn, health, 5, 2000, batch_no="A", expiry="2027-01-01")
    sid = s.sell(conn, [(health, 4)], "cash")
    s.void_sale(conn, sid)
    assert s.stock_level(conn, health) == 5
    assert s.batches(conn, health)[0]["batch_no"] == "A"
    assert s.report(conn, date.today(), date.today())["income"] == 0
    with pytest.raises(s.OpsError):
        s.void_sale(conn, sid)


def test_low_stock_and_expiring(conn, goods):
    health, _, _ = goods
    s.purchase(conn, health, 2, 800, batch_no="A", expiry="2026-11-20")
    assert [r["name"] for r in s.low_stock(conn)] == ["魚油", "有機糙米"]
    exp = s.expiring(conn, days=30, today=date(2026, 11, 4))
    assert exp[0]["batch_no"] == "A"
    assert s.expiring(conn, days=5, today=date(2026, 11, 4)) == []


def test_scrap_and_adjust(conn, goods):
    health, _, _ = goods
    s.purchase(conn, health, 5, 1000, batch_no="A", expiry="2027-01-01")
    s.adjust_stock(conn, health, -2, "過期報廢", batch_no="A", expiry="2027-01-01", scrap=True)
    assert s.stock_level(conn, health) == 3
    with pytest.raises(s.OpsError):
        s.adjust_stock(conn, health, -10, "盤虧")


def test_users_roles_and_limit(conn):
    s.create_user(conn, "老闆", s.OWNER, "1234")
    s.create_user(conn, "小明", s.STAFF, "1111")
    boss = s.authenticate(conn, "老闆", "1234")
    staff = s.authenticate(conn, "小明", "1111")
    assert s.authenticate(conn, "小明", "0000") is None
    assert s.can(boss, "report") and not s.can(staff, "report")
    assert s.can(staff, "pos") and s.can(staff, "view_stock")
    assert not s.can(staff, "view_cost")
    s.create_user(conn, "A", s.STAFF, "2222")
    s.create_user(conn, "B", s.STAFF, "3333")
    with pytest.raises(s.OpsError):
        s.create_user(conn, "C", s.STAFF, "4444")


def test_report_gross_profit(conn, goods):
    health, _, meal = goods
    s.purchase(conn, health, 10, 4000, batch_no="A", expiry="2027-01-01")  # 成本 400/件
    s.sell(conn, [(health, 2), (meal, 1)], "cash")
    s.add_expense(conn, 500, "水電")
    r = s.report(conn, date.today(), date.today())
    assert r["income"] == 2150
    assert r["cogs"] == 800
    assert r["gross_profit"] == 1350
    assert r["net"] == 2150 - 4000 - 500
