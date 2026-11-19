"""meal_app.py 畫面冒煙測試：各頁面（老闆／店員）能正常載入、不丟例外。"""
import pytest
from streamlit.testing.v1 import AppTest

from ops import db, orders as o, services as s

APP = __file__.replace("tests/test_meal_app_smoke.py", "meal_app.py")
HEAD = open(APP, encoding="utf-8").read().split("# ───────────────────────── 導覽")[0]


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
    cards = [m.value for m in at.markdown if "tcard" in m.value]
    card101 = next(c for c in cards if "aria-label='桌 101'" in c)
    assert "待接單" in card101 and "2× 咖哩飯" in card101             # 桌子圖示上直接看到這桌的餐點
    labels = [b.label for b in at.button]
    assert "點餐・結帳" in labels and "🍽️ 開桌點餐" in labels
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



def test_selecting_a_table_opens_ordering_on_the_same_page(seeded):
    at = run()
    [b for b in at.button if b.key == "pick_102"][0].click().run()          # 102 是空桌
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["sel"] == {"kind": "table", "key": "102"}
    assert any("桌 102" in m.value and "開桌點餐" in m.value for m in at.markdown)
    [b for b in at.button if b.key == "padk_ocart_102_1"][0].click().run()   # 點咖哩飯
    [b for b in at.button if b.key == "send_ocart_102"][0].click().run()    # 送出
    assert not at.exception, [e.value for e in at.exception]
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "102")
    assert od["guests"] == 2 and [l["name"] for l in od["lines"]] == ["咖哩飯"]
    assert at.session_state["sel"] == {"kind": "table", "key": "102"}       # 留在外場同一桌
    assert any("已送出" in m.value and "桌 102" in m.value and "#00" in m.value for m in at.success)


def test_dashboard_handles_dishes_checkout_and_takeout_in_one_page(seeded):
    at = run()
    board = next(m.value for m in at.markdown if "class='qboard'" in m.value)             # 叫號看板（取代數字方塊）
    assert "準備中" in board and "#001" in board and "外帶" in board
    [b for b in at.button if b.key == "pick_101"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    md = " ".join(m.value for m in at.markdown)
    assert "第 1 輪" in md and "🕒" in md                                      # 每輪都有時間
    tabs = [t.label for t in at.tabs]
    for name in ("🍽️ 餐點", "➕ 加點", "👥 座位", "💵 結帳", "📣 通知", "🔀 換桌"):
        assert name in tabs
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "101")
    [b for b in at.button if b.key == f"payall{od['id']}"][0].click().run()  # 同一頁直接結清
    assert not at.exception, [e.value for e in at.exception]
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "101")
    assert od["paid_all"] and at.session_state["sel"]["key"] == "101"          # 結清了，但客人還在 → 訂單仍在
    assert not [b for b in at.button if b.key == f"close{od['id']}"]           # 餐還沒上完 → 不能按完成
    assert any("客人離開前需要" in c.value and "上完" in c.value for c in at.caption)
    for _ in range(2):
        o.advance_ticket(seeded, od["id"], 1)
    o.serve_all_ready(seeded, od["id"])
    at.run()
    [b for b in at.button if b.key == f"close{od['id']}"][0].click().run()     # 👋 客人離開・完成
    assert not at.exception, [e.value for e in at.exception]
    assert not any(x["table_no"] == "101" for x in o.open_orders(seeded))
    assert "sel" not in at.session_state
    # 外帶：在接單分頁開新外帶單
    [b for b in at.button if b.key == "new_takeout"][0].click().run()
    [b for b in at.button if b.key == "padk_ocart_takeout_2"][0].click().run()
    [b for b in at.button if b.key == "send_ocart_takeout"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["sel"]["kind"] == "order"                         # 送出後直接切到這張外帶單


def test_seats_and_who_ordered_what_reach_the_kitchen_screen(seeded):
    cid = o.add_customer(seeded, "王小姐", avoid="不吃香菜")
    at = run()
    [b for b in at.button if b.key == "pick_102"][0].click().run()          # 空桌開桌
    at.selectbox(key="nseat_c_102_1").set_value(cid).run()                   # 座 1 是熟客
    at.text_input(key="nseat_n_102_2").set_value("陳先生").run()
    [b for b in at.button if b.key == "padk_ocart_102_1"][0].click().run()   # 咖哩飯
    uid = at.session_state["ocart_102"][0]["uid"]
    [b for b in at.button if b.key == f"os{uid}__1"][0].click().run()        # 這道是座 1 點的
    [b for b in at.button if b.key == "send_ocart_102"][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    line = [t for t in o.kitchen_tickets(seeded) if t["where"] == "桌 102"][0]["lines"][0]
    assert (line["seat_no"], line["guest"], line["avoid"], line["regular"]) == (1, "王小姐", "不吃香菜", True)
    seats = {x["seat_no"]: x["guest_name"] for x in o.order_seats(seeded, line and o.open_orders(seeded)[-1]["id"])}
    assert seats == {1: "王小姐", 2: "陳先生"}
    k = AppTest.from_string(HEAD + "\nst.session_state.setdefault('user', {'id': 1, 'name': 'x', 'role': 'owner'})"
                            "\nkitchen_page()\n", default_timeout=30).run()
    assert not k.exception, [e.value for e in k.exception]
    md = " ".join(m.value for m in k.markdown)
    assert "座1 王小姐 ⭐熟客" in md and "不吃：不吃香菜" in md


def test_combo_drink_goes_to_bar_page(seeded):
    o.set_station(seeded, 2, "bar")                                          # 紅茶 → 吧台
    combo = s.add_item(seeded, "咖哩套餐", "meal", 170)
    o.set_combo(seeded, combo, [(1, 1), (2, 1)])
    o.place_order(seeded, "dine_in", [{"item_id": combo, "qty": 1}], table_no="102")
    bar = AppTest.from_string(HEAD + "\nst.session_state.setdefault('user', {'id': 1, 'name': 'x', 'role': 'owner'})"
                              "\nbar_page()\n", default_timeout=30).run()
    assert not bar.exception, [e.value for e in bar.exception]
    md = " ".join(m.value for m in bar.markdown)
    assert "1 × 紅茶" in md and "🍱 咖哩套餐" in md and "咖哩飯" not in md.split("目前要準備")[1].split("呼叫外場")[0]


def test_settings_page_renders_menu_combos_and_regulars(seeded):
    o.add_customer(seeded, "王小姐", avoid="不吃香菜")
    at = AppTest.from_string(HEAD + "\nst.session_state.setdefault('user', {'id': 1, 'name': 'x', 'role': 'owner'})"
                             "\nsettings_page()\n", default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    assert [t.label for t in at.tabs][:3] == ["桌位", "菜單・套餐", "熟客名單"]
    assert any("王小姐" in m.value for m in at.markdown)


def test_floor_notifies_kitchen_from_the_dashboard(seeded):
    at = run()
    [b for b in at.button if b.key == "pick_101"][0].click().run()
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "101")
    [b for b in at.button if b.key == f"nk{od['id']}_0"][0].click().run()
    assert [(c["message"], c["table_no"]) for c in o.open_calls(seeded, "to_kitchen")] == [("請加快這桌", "桌 101")]


def test_kitchen_shows_prep_summary_and_can_call_floor(seeded):
    at = AppTest.from_string(open(APP, encoding="utf-8").read().split("# ───────────────────────── 導覽")[0]
                             + "\nst.session_state.setdefault('user', {'id': 1, 'name': 'x', 'role': 'owner'})\nkitchen_page()\n",
                             default_timeout=30).run()
    assert not at.exception, [e.value for e in at.exception]
    prep = next(m.value for m in at.markdown if "class='prep'" in m.value)
    assert "咖哩飯" in prep and "<b>3</b>" in prep                            # 101 兩份 + 外帶一份
    [b for b in at.button if b.label == "請來取餐"][0].click().run()
    assert [c["message"] for c in o.open_calls(seeded)] == ["請來取餐（廚房）"]
    floor = run()                                                            # 外場看到呼叫並按收到
    assert any("呼叫外場：請來取餐（廚房）" in e.value for e in floor.error)
    [b for b in floor.button if b.label.startswith("✅ 收到")][0].click().run()
    assert o.open_calls(seeded) == []
