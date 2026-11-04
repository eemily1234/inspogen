"""meal_app.py 畫面冒煙測試：各頁面（老闆／店員）能正常載入、不丟例外。"""
import pytest
from streamlit.testing.v1 import AppTest

from ops import db, orders as o, services as s

APP = __file__.replace("tests/test_meal_app_smoke.py", "meal_app.py")


@pytest.fixture
def seeded(tmp_path, monkeypatch):
    path = str(tmp_path / "app.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    c = db.connect(path)
    s.create_user(c, "老闆", s.OWNER, "1234")
    s.create_user(c, "小明", s.STAFF, "1111")
    s.add_item(c, "咖哩飯", "meal", 150)
    s.add_item(c, "紅茶", "meal", 40)
    o.setup_tables(c, {1: 3, 2: 3})
    a = o.place_order(c, "dine_in", [{"item_id": 1, "qty": 2, "note": "少辣"}], "101", guests=4)
    b = o.place_order(c, "dine_in", [{"item_id": 2, "qty": 1}], "201", guests=2)
    o.advance_ticket(c, b["order_id"], 1); o.advance_ticket(c, b["order_id"], 1)
    o.place_order(c, "takeout", [{"item_id": 1, "qty": 1}])
    return c


def run(role="owner"):
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["user"] = {"id": 1 if role == "owner" else 2, "name": "x", "role": role}
    return at.run()


@pytest.mark.parametrize("role", ["owner", "staff"])
def test_floor_page_renders_with_flow_and_tables(seeded, role):
    at = run(role)
    assert not at.exception, [e.value for e in at.exception]
    text = " ".join(m.value for m in at.markdown)
    assert "廚房接單中" in text and "待上菜" in text and "flow" in text
    labels = [b.label for b in at.button]
    assert any("101" in l and "待接單" in l for l in labels)          # 桌位格子
    assert any("外帶" in m.value for m in at.markdown)


def test_kitchen_pause_is_shown_on_floor(seeded):
    o.set_kitchen_accepting(seeded, False, "客滿")
    at = run()
    assert not at.exception
    assert any("暫停接單" in e.value and "客滿" in e.value for e in at.error)


def _fresh(tmp_path, monkeypatch, name="fresh.db"):
    path = str(tmp_path / name)
    monkeypatch.setattr(db, "DB_PATH", path)
    c = db.connect(path)
    s.create_user(c, "老闆", s.OWNER, "1234")                      # id 1
    s.create_user(c, "小明", s.STAFF, "1111")                      # id 2
    s.create_user(c, "廚房", s.KITCHEN, "2222")                    # id 3
    return c


def test_first_run_auto_creates_4_tables_per_floor_with_4_seats(tmp_path, monkeypatch):
    c = _fresh(tmp_path, monkeypatch)
    at = run("owner")
    assert not at.exception, [e.value for e in at.exception]
    ts = o.list_tables(c)
    assert [t["name"] for t in ts] == ["101", "102", "103", "104", "201", "202", "203", "204"]
    assert {t["seats"] for t in ts} == {4}
    assert not any("建立桌位" in b.label for b in at.button)


def test_owner_who_removed_all_tables_gets_setup_prompt_not_auto_recreate(tmp_path, monkeypatch):
    c = _fresh(tmp_path, monkeypatch)
    run("owner")                                                    # 觸發自動建立
    o.setup_tables(c, {1: 0, 2: 0})                                 # 老闆全部刪掉
    at = run("owner")
    assert not at.exception and o.list_tables(c) == []
    assert any("建立桌位" in b.label for b in at.button)
    at = run("staff")
    assert not at.exception and not any("建立桌位" in b.label for b in at.button)


def _headers(at):
    return [h.value for h in at.header]


def test_each_role_lands_on_its_own_home_page(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    assert any("外場" in h for h in _headers(run("owner")))
    assert any("外場" in h for h in _headers(run("staff")))
    at = AppTest.from_file(APP, default_timeout=30)
    at.session_state["user"] = {"id": 3, "name": "廚房", "role": "staff", "kitchen": 1}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("廚房" in h for h in _headers(at)) and not any("外場" in h for h in _headers(at))


def test_ops_backoffice_blocks_kitchen_account(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    at = AppTest.from_file(APP.replace("meal_app.py", "ops_app.py"), default_timeout=30)
    at.session_state["user"] = {"id": 3, "name": "廚房", "role": "staff", "kitchen": 1}
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("只能使用" in e.value for e in at.error)
    assert not at.header                                            # 沒有任何後台頁面被渲染


def test_device_link_logs_in_kitchen_tablet_without_pin(tmp_path, monkeypatch):
    c = _fresh(tmp_path, monkeypatch)
    owner = s.authenticate(c, "老闆", "1234")
    token = s.create_device_token(c, owner, 3, "廚房平板")
    at = AppTest.from_file(APP, default_timeout=30)
    at.query_params["device"] = token
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("廚房" in h for h in _headers(at)) and not any("外場" in h for h in _headers(at))
    assert at.session_state["user"]["kitchen"] == 1


def test_bad_or_revoked_device_link_shows_login(tmp_path, monkeypatch):
    c = _fresh(tmp_path, monkeypatch)
    owner = s.authenticate(c, "老闆", "1234")
    token = s.create_device_token(c, owner, 3, "廚房平板")
    s.revoke_device_token(c, s.list_device_tokens(c)[0]["id"])
    for tok in ("亂猜", token):
        at = AppTest.from_file(APP, default_timeout=30)
        at.query_params["device"] = tok
        at.run()
        assert not at.exception and "user" not in at.session_state
        assert any(b.label == "登入" for b in at.button) or at.text_input


def test_failed_uber_order_shows_warning_on_floor_and_kitchen(seeded):
    o.record_external_event(seeded, "uber", "ord-1", "orders.notification", "failed", "RuntimeError: x")
    at = run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Uber 訂單處理失敗" in w.value for w in at.warning)


def test_uber_eats_is_a_payment_option():
    from ops.ui import PAY
    assert "Uber Eats" in PAY
