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


def test_kitchen_account_is_locked_to_meal_pages(conn):
    s.create_user(conn, "廚房", s.KITCHEN, "2222")
    k = s.authenticate(conn, "廚房", "2222")
    assert k["role"] == s.STAFF and k["kitchen"] == 1 and s.is_kitchen(k)
    assert s.meal_pages(k) == ["kitchen", "order"]
    for action in ("pos", "view_stock", "report", "void", "view_cost"):
        assert not s.can(k, action)
    staff = s.create_user(conn, "小明", s.STAFF, "1111") and s.authenticate(conn, "小明", "1111")
    assert s.meal_pages(staff) == ["floor", "order", "kitchen", "orders"]
    assert s.can(staff, "pos") and not s.is_kitchen(staff)
    assert s.meal_pages({"role": s.OWNER}) == ["floor", "order", "kitchen", "orders", "settings"]


def test_kitchen_account_counts_toward_four_user_limit(conn):
    for name, role in (("老闆", s.OWNER), ("服務生", s.STAFF), ("廚房", s.KITCHEN), ("店員2", s.STAFF)):
        s.create_user(conn, name, role, "1234")
    with pytest.raises(s.OpsError):
        s.create_user(conn, "第五人", s.STAFF, "1234")


def test_old_user_table_gets_kitchen_column(tmp_path):
    import sqlite3
    path = str(tmp_path / "old.db")
    raw = sqlite3.connect(path)
    raw.executescript("""CREATE TABLE user (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE,
        role TEXT NOT NULL CHECK (role IN ('owner','staff')), salt TEXT NOT NULL, pin_hash TEXT NOT NULL,
        active INTEGER NOT NULL DEFAULT 1);
        INSERT INTO user(name, role, salt, pin_hash) VALUES ('老闆','owner','x','y');""")
    raw.commit(); raw.close()
    c = db.connect(path)
    assert "kitchen" in {r["name"] for r in c.execute("PRAGMA table_info(user)")}
    assert c.execute("SELECT kitchen FROM user").fetchone()[0] == 0          # 舊帳號不受影響
    s.create_user(c, "廚房", s.KITCHEN, "2222")
    assert s.authenticate(c, "廚房", "2222")["kitchen"] == 1


# ───────────── 登入鎖定、裝置登入連結 ─────────────
from datetime import datetime, timedelta  # noqa: E402


def _users(conn):
    s.create_user(conn, "老闆", s.OWNER, "1234")
    s.create_user(conn, "小明", s.STAFF, "1111")
    s.create_user(conn, "廚房", s.KITCHEN, "2222")


def test_login_locks_after_5_failures_even_for_correct_pin(conn):
    _users(conn)
    t0 = datetime(2026, 11, 4, 12, 0, 0)
    for i in range(5):
        assert s.authenticate(conn, "小明", "0000", now=t0 + timedelta(seconds=i)) is None
    with pytest.raises(s.OpsError, match="次數過多"):
        s.authenticate(conn, "小明", "1111", now=t0 + timedelta(minutes=1))      # 對的 PIN 也不行
    assert s.authenticate(conn, "老闆", "1234", now=t0 + timedelta(minutes=1))   # 其他帳號不受影響
    assert s.authenticate(conn, "小明", "1111", now=t0 + timedelta(minutes=11))  # 10 分鐘後解鎖


def test_success_resets_failure_count(conn):
    _users(conn)
    t0 = datetime(2026, 11, 4, 12, 0, 0)
    for i in range(4):
        s.authenticate(conn, "小明", "0000", now=t0 + timedelta(seconds=i))
    assert s.authenticate(conn, "小明", "1111", now=t0 + timedelta(seconds=10))
    for i in range(4):                                                            # 重新計算，不會被舊的失敗累積
        s.authenticate(conn, "小明", "0000", now=t0 + timedelta(seconds=20 + i))
    assert s.authenticate(conn, "小明", "1111", now=t0 + timedelta(seconds=30))


def test_unknown_names_are_counted_and_global_throttle(conn):
    _users(conn)
    t0 = datetime(2026, 11, 4, 12, 0, 0)
    for i in range(s.GLOBAL_LOGIN_FAILS):
        s.authenticate(conn, f"猜{i}", "0000", now=t0 + timedelta(seconds=i))
    with pytest.raises(s.OpsError):
        s.authenticate(conn, "老闆", "1234", now=t0 + timedelta(seconds=40))      # 全域鎖定
    assert s.authenticate(conn, "老闆", "1234", now=t0 + timedelta(minutes=11))


def test_min_pin_length_is_configurable(conn, monkeypatch):
    monkeypatch.setattr(s, "MIN_PIN_LENGTH", 6)
    with pytest.raises(s.OpsError, match="6"):
        s.create_user(conn, "老闆", s.OWNER, "1234")
    s.create_user(conn, "老闆", s.OWNER, "123456")


def test_device_token_lifecycle(conn):
    _users(conn)
    owner = s.authenticate(conn, "老闆", "1234")
    kitchen_id = conn.execute("SELECT id FROM user WHERE name='廚房'").fetchone()[0]
    owner_id = owner["id"]
    token = s.create_device_token(conn, owner, kitchen_id, "廚房平板")
    assert len(token) >= 30
    stored = conn.execute("SELECT token_hash FROM device_token").fetchone()[0]
    assert token not in stored and len(stored) == 64                              # 只存雜湊
    u = s.user_from_device_token(conn, token)
    assert u["name"] == "廚房" and u["kitchen"] == 1 and "pin_hash" in u
    assert s.user_from_device_token(conn, "亂猜的") is None and s.user_from_device_token(conn, None) is None
    assert s.list_device_tokens(conn)[0]["last_used_at"] is not None
    s.revoke_device_token(conn, s.list_device_tokens(conn)[0]["id"])
    assert s.user_from_device_token(conn, token) is None                          # 撤銷後失效
    # 老闆不能被綁、只有老闆能建立
    with pytest.raises(s.OpsError):
        s.create_device_token(conn, owner, owner_id, "x")
    staff = s.authenticate(conn, "小明", "1111")
    with pytest.raises(s.OpsError):
        s.create_device_token(conn, staff, kitchen_id, "x")


def test_device_token_stops_working_when_user_deactivated(conn):
    _users(conn)
    owner = s.authenticate(conn, "老闆", "1234")
    sid = conn.execute("SELECT id FROM user WHERE name='小明'").fetchone()[0]
    token = s.create_device_token(conn, owner, sid)
    assert s.user_from_device_token(conn, token)
    with conn:
        conn.execute("UPDATE user SET active=0 WHERE id=?", (sid,))
    assert s.user_from_device_token(conn, token) is None
