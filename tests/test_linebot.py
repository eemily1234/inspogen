from datetime import date, datetime, timedelta

import pytest

from ops import db, linebot as lb, services as s

LINE = "Uboss"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(str(tmp_path / "t.db"))
    s.create_user(c, "老闆", s.OWNER, "1234")
    s.create_user(c, "小明", s.STAFF, "1111")
    s.add_item(c, "魚油", "health", 1000, safety_stock=2)
    s.add_item(c, "有機糙米", "organic", 300)
    s.add_item(c, "有機燕麥", "organic", 200)
    return c


@pytest.fixture
def bound(conn):
    code = lb.create_bind_code(conn, 1)
    assert "成功" in lb.handle_text(conn, LINE, f"綁定 {code}")
    return conn


def test_unbound_gets_only_bind_help_and_no_data(conn):
    for text in ("支出 午餐 120", "庫存", "今日"):
        assert lb.handle_text(conn, "Ustranger", text) == lb.BIND_HELP
    assert conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0] == 0


def test_bind_code_rules(conn):
    with pytest.raises(s.OpsError):
        lb.create_bind_code(conn, 2)  # 店員不能綁定
    now = datetime(2026, 11, 4, 12, 0)
    code = lb.create_bind_code(conn, 1, now=now)
    assert "無效" in lb.handle_text(conn, LINE, "綁定 000000")
    assert "無效" in lb.handle_text(conn, LINE, f"綁定 {code}", now=now + timedelta(minutes=11))
    assert "成功" in lb.handle_text(conn, LINE, f"綁定 {code}", now=now + timedelta(minutes=1))
    assert "無效" in lb.handle_text(conn, LINE, f"綁定 {code}")  # 一次性


def test_expense_variants(bound):
    assert "已記支出" in lb.handle_text(bound, LINE, "支出 午餐 120")
    assert "已記支出" in lb.handle_text(bound, LINE, "支出 計程車 回家 $1,200元")
    assert "已記支出" in lb.handle_text(bound, LINE, "支出 ５０ 停車")  # 全形數字、金額在前
    r = s.report(bound, date.today(), date.today())
    assert r["expense"] == 120 + 1200 + 50
    assert "看不出金額" in lb.handle_text(bound, LINE, "支出 午餐")
    assert bound.execute("SELECT source FROM ledger LIMIT 1").fetchone()[0] == "line"


def test_purchase_updates_stock_and_cost(bound):
    out = lb.handle_text(bound, LINE, "進貨 魚油 10 4000 A1 2027-01-01")
    assert "已進貨" in out and "庫存 10" in out
    assert s.stock_level(bound, 1) == 10
    assert s.report(bound, date.today(), date.today())["purchase_cost"] == 4000
    assert "已進貨" in lb.handle_text(bound, LINE, "進貨 糙米 5包 1000")  # 部分品名、單位


def test_purchase_errors_are_friendly(bound):
    assert "批號" in lb.handle_text(bound, LINE, "進貨 魚油 10 4000")        # 保健品需批號
    assert "找不到" in lb.handle_text(bound, LINE, "進貨 鈣片 1 100")
    assert "多個符合" in lb.handle_text(bound, LINE, "進貨 有機 1 100")
    assert "格式" in lb.handle_text(bound, LINE, "進貨 魚油")
    assert s.stock_level(bound, 1) == 0


def test_undo_expense_and_purchase(bound):
    lb.handle_text(bound, LINE, "支出 午餐 120")
    assert "已復原上一筆支出" in lb.handle_text(bound, LINE, "復原")
    assert s.report(bound, date.today(), date.today())["expense"] == 0
    lb.handle_text(bound, LINE, "進貨 魚油 10 4000 A1 2027-01-01")
    assert "已復原上一筆進貨" in lb.handle_text(bound, LINE, "復原")
    assert s.stock_level(bound, 1) == 0
    assert s.report(bound, date.today(), date.today())["purchase_cost"] == 0
    assert "沒有可復原" in lb.handle_text(bound, LINE, "復原")


def test_undo_purchase_blocked_after_sale(bound):
    lb.handle_text(bound, LINE, "進貨 魚油 3 900 A1 2027-01-01")
    s.sell(bound, [(1, 2)], "現金")
    out = lb.handle_text(bound, LINE, "復原")
    assert "已有賣出" in out
    assert s.stock_level(bound, 1) == 1  # 庫存未被動到


def test_stock_and_reports(bound):
    lb.handle_text(bound, LINE, "進貨 魚油 1 400 A1 2026-11-20")
    out = lb.handle_text(bound, LINE, "庫存")
    assert "⚠️魚油 1" in out and "30 天內到期" in out
    assert "批號 A1" in lb.handle_text(bound, LINE, "庫存 魚油")
    assert "進貨成本 $400" in lb.handle_text(bound, LINE, "今日")
    assert "本月" in lb.handle_text(bound, LINE, "本月")


def test_unknown_and_help(bound):
    assert "看不懂" in lb.handle_text(bound, LINE, "你好")
    assert lb.handle_text(bound, LINE, "說明") == lb.HELP
