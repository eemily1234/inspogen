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


def test_no_tables_prompts_owner_setup(tmp_path, monkeypatch):
    path = str(tmp_path / "empty.db")
    monkeypatch.setattr(db, "DB_PATH", path)
    c = db.connect(path)
    s.create_user(c, "老闆", s.OWNER, "1234")
    s.create_user(c, "小明", s.STAFF, "1111")
    at = run("owner")
    assert not at.exception
    assert any("建立桌位" in b.label for b in at.button)
    at = run("staff")
    assert not at.exception and not any("建立桌位" in b.label for b in at.button)
