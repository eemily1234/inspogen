"""餐點點餐系統（獨立 App）：點餐 → 廚房 → 上菜 → 結帳（可分開結帳、併桌）。

啟動：streamlit run meal_app.py --server.port 8502
與 ops_app.py（營運後台）共用同一個資料庫與使用者（OPS_DB），結帳的收入會自動進帳務報表。
"""
from uuid import uuid4

import streamlit as st

from ops import db, orders, services as s
from ops.ui import PAY, login_view, sidebar_user

st.set_page_config(page_title="餐點點餐系統", page_icon="🍽️", layout="wide")

QUICK_NOTES = ["少辣", "不辣", "加辣", "不要香菜", "不要蔥", "少冰", "去冰", "少糖", "無糖", "打包"]
ICON = {"new": "🆕", "preparing": "🔥", "ready": "🛎️", "served": "✅", "cancelled": "🚫"}

conn = db.connect()


# ───────────────────────── 點餐 ─────────────────────────
def order_page():
    user = st.session_state.user
    st.header("🍽️ 點餐")
    if msg := st.session_state.pop("last_order", None):
        st.success(msg)
    cart = st.session_state.setdefault("ocart", [])

    left, right = st.columns([3, 2])
    with left:
        otype = st.radio("類型", ["dine_in", "takeout"], horizontal=True,
                         format_func=lambda x: "內用" if x == "dine_in" else "外帶")
        table = ""
        if otype == "dine_in":
            table = st.text_input("桌號", key="o_table", placeholder="例：A3")
            if table.strip() and orders.open_order_for_table(conn, table):
                st.info(f"桌 {table.strip()} 已有未結帳訂單，本次會以「加點」送給廚房。")
        meals = [i for i in s.list_items(conn) if i["type"] == "meal"]
        if not meals:
            st.info("還沒有餐點，請老闆到「菜單管理」新增。")
        st.caption("點一下餐點加入訂單")
        cols = st.columns(2)
        for n, it in enumerate(meals):
            if cols[n % 2].button(f"{it['name']}　${it['price']}", key=f"om{it['id']}",
                                  use_container_width=True):
                cart.append({"uid": uuid4().hex[:8], "item_id": it["id"]})
                st.rerun()

    with right:
        st.subheader("本次訂單")
        names = {i["id"]: i for i in meals}
        total = 0
        for e in list(cart):
            it = names.get(e["item_id"])
            if not it:
                cart.remove(e)
                continue
            with st.container(border=True):
                c1, c2, c3 = st.columns([3, 2, 1])
                c1.write(f"**{it['name']}**　${it['price']}")
                e["qty"] = c2.number_input("份數", 1, 99, e.get("qty", 1), key=f"oq{e['uid']}",
                                           label_visibility="collapsed")
                if c3.button("🗑", key=f"od{e['uid']}"):
                    cart.remove(e)
                    st.rerun()
                e["quick"] = st.multiselect("特殊需求", QUICK_NOTES, key=f"on{e['uid']}",
                                            placeholder="常用需求")
                e["text"] = st.text_input("其他需求", key=f"ot{e['uid']}", placeholder="例：醬分開、飯少一點")
                total += it["price"] * e["qty"]
        if not cart:
            st.caption("尚未選擇餐點")
            return
        order_note = st.text_input("整單備註（過敏、趕時間…）", key="o_note")
        st.markdown(f"### 合計　${total}")
        if st.button("📨 送出到廚房", type="primary", use_container_width=True):
            lines = [{"item_id": e["item_id"], "qty": e["qty"],
                      "note": "、".join(e.get("quick", [])
                                       + ([e["text"].strip()] if e.get("text", "").strip() else []))}
                     for e in cart]
            try:
                r = orders.place_order(conn, otype, lines, table, order_note, user["id"])
                where = f"桌 {r['table_no']}" if otype == "dine_in" else f"外帶取餐號 #{r['pickup_no']}"
                st.session_state.ocart = []
                for k in ("o_note", "o_table"):
                    st.session_state.pop(k, None)
                st.session_state.last_order = f"已送出：{where}" + (
                    f"（第 {r['round_no']} 輪加點）" if r["is_addon"] else "")
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))


# ───────────────────────── 廚房 ─────────────────────────
@st.fragment(run_every=5)
def _kitchen_board():
    c = db.connect()   # fragment 會在背景自動重跑，使用獨立連線
    try:
        tickets = orders.kitchen_tickets(c)
        keys = {(t["order_id"], t["round_no"]) for t in tickets}
        if "kitchen_seen" in st.session_state and keys - st.session_state.kitchen_seen:
            st.toast(f"🔔 有 {len(keys - st.session_state.kitchen_seen)} 張新單！")
        st.session_state.kitchen_seen = keys
        st.caption(f"待處理 {len(tickets)} 張　每 5 秒自動更新")
        if not tickets:
            st.success("目前沒有待處理的單 👍")
        cols = st.columns(2)
        for n, t in enumerate(tickets):
            with cols[n % 2].container(border=True):
                head = f"{ICON[t['status']]} **{t['where']}**"
                if t["is_addon"]:
                    head += f"　:red[加點（第 {t['round_no']} 輪）]"
                for origin in t["origins"]:
                    head += f"　:orange[{origin}]"
                st.markdown(f"{head}　{orders.LABEL[t['status']]}　⏱ {orders.wait_minutes(t['created_at'])} 分鐘")
                if t["order_note"]:
                    st.markdown(f":red[⚠️ 整單備註：{t['order_note']}]")
                for ln in t["lines"]:
                    st.markdown(f"### {ln['qty']} × {ln['name']}")
                    if ln["note"]:
                        st.markdown(f":red[　➜ {ln['note']}]")
                label = "🔥 開始製作" if t["status"] == "new" else "🛎️ 完成出餐"
                if st.button(label, key=f"k{t['order_id']}_{t['round_no']}", use_container_width=True,
                             type="primary" if t["status"] == "new" else "secondary"):
                    try:
                        orders.advance_ticket(c, t["order_id"], t["round_no"])
                    except s.OpsError as e:
                        st.error(str(e))
                    st.rerun()
    finally:
        c.close()


def kitchen_page():
    st.header("👨‍🍳 廚房")
    _kitchen_board()


# ───────────────────────── 訂單 / 結帳 / 併桌 ─────────────────────────
@st.fragment(run_every=5)
def _orders_board():
    user = st.session_state.user
    c = db.connect()
    try:
        ods = orders.open_orders(c)
        ready = sum(o["to_serve"] for o in ods)
        prev = st.session_state.get("ready_seen")
        if prev is not None and ready > prev:
            st.toast("🛎️ 有餐點完成，可以上菜了！")
        st.session_state.ready_seen = ready
        if not ods:
            st.info("目前沒有未結帳的訂單")
            return
        labels = {o["id"]: f"{o['where']}　待付 ${o['unpaid_total']}"
                  + (f"　🛎️待上菜 {o['to_serve']}" if o["to_serve"] else "")
                  + (f"　🔥製作中 {o['unfinished']}" if o["unfinished"] else "") for o in ods}
        sel = st.radio("未結帳訂單", list(labels), format_func=labels.get, key="sel_order")
        od = next(o for o in ods if o["id"] == sel)
        if od["note"]:
            st.markdown(f":red[⚠️ 整單備註：{od['note']}]")

        picks, pick_total = [], 0
        for ln in od["lines"]:
            paid = f"　已付 {ln['paid_qty']}/{ln['qty']}" if ln["paid_qty"] else ""
            txt = f"{ICON[ln['status']]} {ln['qty']} × {ln['name']}　${ln['qty'] * ln['unit_price']}{paid}"
            if ln["round_no"] > 1:
                txt += f"　(加點{ln['round_no']})"
            if ln["origin"]:
                txt += f"　{ln['origin']}"
            if ln["status"] == "cancelled":
                txt = f"~~{txt}~~"
            c1, c2 = st.columns([5, 2])
            c1.markdown(txt + (f"  \n:red[➜ {ln['note']}]" if ln["note"] else ""))
            if ln["remaining"] > 0:
                # key 帶上剩餘份數：付款後上限改變，輸入框自動重設，避免殘留舊值
                q = c2.number_input("本次結", 0, ln["remaining"], 0, key=f"pq{ln['id']}_{ln['remaining']}",
                                    label_visibility="collapsed", help="這次要結幾份（分開結帳）")
                if q:
                    picks.append((ln["id"], q))
                    pick_total += q * ln["unit_price"]
            b1, b2, _ = st.columns([1, 1, 4])
            if ln["status"] == "ready" and b1.button("已上菜", key=f"sv{ln['id']}"):
                orders.serve_line(c, ln["id"])
                st.rerun()
            if ln["status"] == "new" and ln["paid_qty"] == 0 and b2.button("取消", key=f"cl{ln['id']}"):
                try:
                    orders.cancel_line(c, ln["id"])
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))

        st.markdown(f"### 合計 ${od['total']}　已付 ${od['paid_total']}　**待付 ${od['unpaid_total']}**")
        if od["unfinished"]:
            st.caption(f"尚有 {od['unfinished']} 項未出餐（仍可先結帳）")
        pay = st.radio("付款方式", PAY, horizontal=True, key="pay_order")
        b1, b2 = st.columns(2)
        if b1.button(f"💵 結帳勾選品項　${pick_total}", disabled=not picks, use_container_width=True):
            try:
                sid = orders.checkout_lines(c, od["id"], picks, pay, user["id"])
                st.session_state.last_order = f"已結帳 ${pick_total}：{od['where']}（單號 #{sid}）"
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
        if b2.button(f"💵 全部結清　${od['unpaid_total']}", type="primary", use_container_width=True):
            try:
                sid = orders.checkout_order(c, od["id"], pay, user["id"])
                st.session_state.last_order = f"已結清：{od['where']}　${od['unpaid_total']}（單號 #{sid}）"
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
        st.caption("分開結帳：在要先付的品項右邊填「本次結」份數，再按「結帳勾選品項」，可分次、分付款方式。")

        with st.expander("🧮 均分試算（只算金額，不入帳）"):
            n = st.number_input("人數", 1, 30, 2, key=f"split_n_{od['id']}")
            shares = orders.split_evenly(od["unpaid_total"], n)
            st.write("　".join(f"第{i + 1}位 ${v}" for i, v in enumerate(shares)))
            st.caption("要各自付款，請用上面的分開結帳依品項記帳。")

        if od["order_type"] == "dine_in":
            with st.expander("🔀 換桌 / 併桌"):
                others = [o["table_no"] for o in ods if o["id"] != od["id"] and o["order_type"] == "dine_in"]
                st.caption(f"把「桌 {od['table_no']}」移到：選一個有人的桌子就是**併桌**，輸入新桌號就是**換桌**。")
                choice = st.selectbox("目標桌", ["（輸入新桌號）"] + others, key=f"mt_{od['id']}")
                target = choice if choice != "（輸入新桌號）" else st.text_input(
                    "新桌號", key=f"mtn_{od['id']}")
                if st.button("確認", key=f"mtok_{od['id']}"):
                    try:
                        r = orders.merge_tables(c, od["table_no"], target)
                        word = "換桌" if r["action"] == "move" else "併桌"
                        st.session_state.last_order = f"已{word}：桌 {od['table_no']} → 桌 {target.strip()}"
                        st.rerun()
                    except s.OpsError as e:
                        st.error(str(e))

        if s.can(user, "void") and st.button("取消整張訂單"):
            try:
                orders.cancel_order(c, od["id"])
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
    finally:
        c.close()


def orders_page():
    st.header("🧾 訂單 / 結帳")
    if msg := st.session_state.pop("last_order", None):
        st.success(msg)
    _orders_board()


# ───────────────────────── 菜單管理（老闆） ─────────────────────────
def menu_page():
    st.header("📋 菜單管理")
    with st.form("newmeal", clear_on_submit=True):
        name = st.text_input("餐點名稱")
        price = st.number_input("售價", 0, step=5)
        cost = st.number_input("單份成本（選填，供毛利參考）", 0, step=5)
        if st.form_submit_button("新增餐點", use_container_width=True):
            try:
                s.add_item(conn, name, "meal", price, cost)
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
    st.subheader("目前菜單")
    for it in (i for i in s.list_items(conn, active_only=False) if i["type"] == "meal"):
        c1, c2, c3 = st.columns([3, 2, 2])
        c1.write(it["name"] + ("" if it["active"] else "（停售）"))
        new_price = c2.number_input("售價", 0, value=it["price"], step=5, key=f"mp{it['id']}",
                                    label_visibility="collapsed")
        if new_price != it["price"]:
            s.update_item(conn, it["id"], price=new_price)
            st.rerun()
        if c3.button("恢復販售" if not it["active"] else "停售", key=f"ma{it['id']}"):
            s.update_item(conn, it["id"], active=0 if it["active"] else 1)
            st.rerun()


# ───────────────────────── 導覽 ─────────────────────────
if "user" not in st.session_state:
    login_view(conn, "🍽️ 餐點點餐系統")
else:
    user = st.session_state.user
    pages = [st.Page(order_page, title="點餐", icon="🍽️", url_path="order"),
             st.Page(kitchen_page, title="廚房", icon="👨‍🍳", url_path="kitchen"),
             st.Page(orders_page, title="訂單/結帳", icon="🧾", url_path="orders")]
    if s.can(user, "report"):
        pages.append(st.Page(menu_page, title="菜單管理", icon="📋", url_path="menu"))
    st.navigation(pages).run()
    sidebar_user(user)
