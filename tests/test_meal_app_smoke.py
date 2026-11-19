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


def press(at, prefix, suffix=""):
    """找到 key 以 prefix 開頭、suffix 結尾的按鈕（座位、桌子的 key 會帶著狀態）。"""
    hits = [b for b in at.button if b.key and b.key.startswith(prefix) and b.key.endswith(suffix)]
    assert hits, f"找不到按鈕 {prefix}…{suffix}"
    return hits[0]


@pytest.mark.parametrize("role", ["owner", "staff"])
def test_floor_page_renders_with_flow_and_tables(seeded, role):
    at = run(role)
    assert not at.exception, [e.value for e in at.exception]
    text = " ".join(m.value for m in at.markdown)
    assert "廚房接單中" in text and "class='qboard'" in text
    t101 = press(at, "tbl_new_off_", "_101")                                     # 桌子按鈕：顏色＝狀態
    assert "101" in t101.label and "待接單" in t101.label
    seats101 = [b for b in at.button if b.key and b.key.startswith("seat") and "_101_" in b.key]
    assert len(seats101) == 4                                                    # 一邊兩人，四個座位都能點
    assert press(at, "tbl_empty_off_", "_102")


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
    press(at, "tbl_empty_", "_102").click().run()                               # 102 是空桌
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["sel"] == {"kind": "table", "key": "102"}
    assert any("桌 102" in m.value and "開桌點餐" in m.value for m in at.markdown)
    press(at, "padk_ocart_102_1").click().run()                                 # 點咖哩飯（共用）
    press(at, "send_ocart_102").click().run()                                   # 送出
    assert not at.exception, [e.value for e in at.exception]
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "102")
    assert [l["name"] for l in od["lines"]] == ["咖哩飯"] and od["guests"] is None   # 沒指定座位 → 不亂算人數
    assert at.session_state["sel"] == {"kind": "table", "key": "102"}           # 留在外場同一桌
    assert any("已送出" in m.value and "桌 102" in m.value and "#00" in m.value for m in at.success)


def test_tapping_a_seat_orders_for_that_guest_and_counts_people(seeded):
    at = run()
    press(at, "seate_", "_102_3").click().run()                                 # 直接點 102 的座 3
    assert at.session_state["sel"] == {"kind": "table", "key": "102"} and at.session_state["sel_seat"] == 3
    assert any("算座 3 的" in m.value for m in at.markdown)
    press(at, "padk_ocart_102_1").click().run()                                 # 點的菜自動算座 3
    press(at, "send_ocart_102").click().run()
    assert not at.exception, [e.value for e in at.exception]
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "102")
    assert od["lines"][0]["seat_no"] == 3 and od["guests"] == 1                # 人數＝有點餐的座位數
    at.run()
    assert "1道" in press(at, "seats_", "_102_3").label                         # 座位圖馬上顯示座 3 點了 1 道（選中＝藍色）
    press(at, "ws_close").click().run()                                          # 收起後，座 3 顯示為「有人坐」
    assert "1道" in press(at, "seatf_", "_102_3").label


def test_dashboard_handles_dishes_checkout_and_takeout_in_one_page(seeded):
    at = run()
    board = next(m.value for m in at.markdown if "class='qboard'" in m.value)  # 叫號看板
    assert "準備中" in board and "#001" in board and "外帶" in board
    press(at, "tbl_", "_101").click().run()
    assert not at.exception, [e.value for e in at.exception]
    md = " ".join(m.value for m in at.markdown)
    assert "第 1 輪" in md and "🕒" in md                                      # 每輪都有時間
    exp = [e.label for e in at.expander]
    assert any(x.startswith("💵 結帳") for x in exp) and "📣 通知廚房／吧台" in exp and "🔀 換桌／併桌" in exp
    assert any("製作中" in i.value for i in at.info)                            # 下一步：等廚房
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "101")
    press(at, f"payall{od['id']}").click().run()                                # 先結帳
    assert not at.exception, [e.value for e in at.exception]
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "101")
    assert od["paid_all"] and at.session_state["sel"]["key"] == "101"           # 結清了，但客人還在
    for _ in range(2):
        o.advance_ticket(seeded, od["id"], 1)
    at.run()
    press(at, f"next_serve{od['id']}").click().run()                            # 下一步：上菜
    press(at, f"close{od['id']}").click().run()                                 # 下一步：客人離開
    assert not at.exception, [e.value for e in at.exception]
    assert not any(x["table_no"] == "101" for x in o.open_orders(seeded))
    assert "sel" not in at.session_state
    # 外帶：開新外帶單
    press(at, "new_takeout").click().run()
    press(at, "padk_ocart_takeout_2").click().run()
    press(at, "send_ocart_takeout").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["sel"]["kind"] == "order"                           # 送出後直接切到這張外帶單


def test_seats_and_who_ordered_what_reach_the_kitchen_screen(seeded):
    cid = o.add_customer(seeded, "王小姐", avoid="不吃香菜")
    at = run()
    press(at, "seate_", "_102_1").click().run()                                 # 點空桌的座 1
    at.selectbox(key="sp_c_102_1").set_value(cid).run()                         # 這位是熟客
    press(at, "sp_s_102_1").click().run()
    press(at, "sel_seat__2").click().run()                                      # 換到座 2，登記名字
    at.text_input(key="sp_n_102_2").set_value("陳先生").run()
    press(at, "sp_s_102_2").click().run()
    press(at, "sel_seat__1").click().run()                                      # 回到座 1 點餐
    press(at, "padk_ocart_102_1").click().run()
    press(at, "send_ocart_102").click().run()
    assert not at.exception, [e.value for e in at.exception]
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "102")
    line = [t for t in o.kitchen_tickets(seeded) if t["where"] == "桌 102"][0]["lines"][0]
    assert (line["seat_no"], line["guest"], line["avoid"], line["regular"]) == (1, "王小姐", "不吃香菜", True)
    assert {x["seat_no"]: x["guest_name"] for x in od["seats"]} == {1: "王小姐", 2: "陳先生"}
    assert od["guests"] == 2                                                     # 兩個登記的座位
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
    press(at, "tbl_", "_101").click().run()
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "101")
    press(at, f"nk{od['id']}_0").click().run()
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


def _page(fn):
    return AppTest.from_string(HEAD + "\nst.session_state.setdefault('user', {'id': 1, 'name': 'x', 'role': 'owner'})"
                               f"\n{fn}()\n", default_timeout=30).run()


def test_sold_out_pad_is_disabled_and_shows_why(seeded):
    o.set_sold_out(seeded, 1)
    at = run()
    press(at, "tbl_empty_", "_102").click().run()
    pad = press(at, "padk_ocart_102_1")
    assert pad.disabled and "售完" in pad.label
    o.set_portions(seeded, 2, 3)
    at.run()
    assert "剩 3" in press(at, "padk_ocart_102_2").label


def test_kitchen_one_tap_sold_out(seeded):
    k = _page("kitchen_page")
    assert not k.exception, [e.value for e in k.exception]
    press(k, "so_kitchen_1").click().run()
    assert o.availability(seeded)[1]["ok"] is False


def test_shift_and_report_pages(seeded):
    sh = _page("shift_page")
    assert not sh.exception, [e.value for e in sh.exception]
    [b for b in sh.button if "開班" in b.label][0].click().run()
    assert o.current_shift(seeded)
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "101")
    o.checkout_order(seeded, od["id"], "現金")
    sh = _page("shift_page")
    assert any("抽屜應有現金" in m.value and "＄2300" in m.value for m in sh.markdown)    # 2000 + 300
    sh.number_input(key="close_cash").set_value(2300).run()
    [b for b in sh.button if "關班" in b.label][0].click().run()
    assert not sh.exception and o.current_shift(seeded) is None
    rp = _page("report_page")
    assert not rp.exception, [e.value for e in rp.exception]
    assert any("營收（實收）" in m.value and "＄300" in m.value for m in rp.markdown)


def test_settings_option_editor_renders(seeded):
    o.add_option_group(seeded, 1, "份量", "小份-10, 大份+20", required=True)
    st_ = _page("settings_page")
    assert not st_.exception, [e.value for e in st_.exception]
    assert any("份量" in m.value and "大份+20" in m.value for m in st_.markdown)


def test_report_lives_in_the_sidebar_for_owner_only(seeded):
    od = next(x for x in o.open_orders(seeded) if x["table_no"] == "101")
    o.checkout_order(seeded, od["id"], "現金")
    at = run("owner")
    assert not at.exception, [e.value for e in at.exception]
    assert any(m.label == "營收（實收）" and "300" in m.value for m in at.sidebar.metric)
    staff = run("staff")
    assert not staff.exception and not list(staff.sidebar.metric)                 # 店員看不到報表


def test_shift_lives_in_the_sidebar_for_staff_and_owner(seeded):
    o.open_shift(seeded, 1500, user_id=1)
    for role in ("owner", "staff"):
        at = run(role)
        assert not at.exception, [e.value for e in at.exception]
        assert any(m.label == "抽屜應有現金" and "1500" in m.value for m in at.sidebar.metric)


def test_settings_lives_in_the_sidebar_for_owner_only(seeded):
    at = run("owner")
    assert not at.exception, [e.value for e in at.exception]
    assert any("⚙️ 設定" in m.value for m in at.sidebar.markdown)
    staff = run("staff")
    assert not staff.exception and not any("⚙️ 設定" in m.value for m in staff.sidebar.markdown)
