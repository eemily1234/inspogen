"""營運系統：POS、點餐／廚房、庫存、營運主檔、帳務。啟動：streamlit run ops_app.py"""
from datetime import date, timedelta
from uuid import uuid4

import streamlit as st

from ops import db, linebot, orders, services as s

st.set_page_config(page_title="營運系統", page_icon="🧾", layout="centered")

CAT = {"health": "保健品", "organic": "有機商品", "meal": "餐點"}
PAY = ["現金", "轉帳", "行動支付", "信用卡"]


# 每次執行畫面都開新連線：SQLite 開啟很快，也避免多使用者共用連線的執行緒問題
conn = db.connect()


# ───────────────────────── 登入 ─────────────────────────
def login_view():
    st.title("🧾 營運系統")
    if not s.has_users(conn):
        st.info("第一次使用：請設定老闆帳號")
        with st.form("first"):
            name = st.text_input("姓名")
            pin = st.text_input("PIN（至少 4 位數字）", type="password")
            if st.form_submit_button("建立老闆帳號", use_container_width=True):
                try:
                    s.create_user(conn, name, s.OWNER, pin)
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))
        return
    with st.form("login"):
        name = st.text_input("姓名")
        pin = st.text_input("PIN", type="password")
        if st.form_submit_button("登入", use_container_width=True):
            user = s.authenticate(conn, name, pin)
            if user:
                st.session_state.user = user
                st.rerun()
            st.error("姓名或 PIN 錯誤")


# ───────────────────────── POS ─────────────────────────
def pos_page():
    user = st.session_state.user
    st.header("🛒 POS（保健品／有機商品）")
    cart = st.session_state.setdefault("cart", {})
    items = {i["id"]: i for i in s.list_items(conn)}

    retail = [c for c in CAT if c != "meal"]   # 餐點走「點餐」流程
    tabs = st.tabs([CAT[c] for c in retail])
    for tab, cat in zip(tabs, retail):
        with tab:
            for it in (i for i in items.values() if i["category"] == cat):
                label = f"{it['name']}　${it['price']}"
                if it["type"] == "product":
                    label += f"　（庫存 {s.stock_level(conn, it['id'])}）"
                if st.button(label, key=f"add{it['id']}", use_container_width=True):
                    cart[it["id"]] = cart.get(it["id"], 0) + 1
                    st.rerun()

    st.subheader("購物車")
    if not cart:
        st.caption("尚未選擇品項")
    total = 0
    for iid in list(cart):
        it = items.get(iid)
        if not it:
            cart.pop(iid)
            continue
        c1, c2 = st.columns([3, 2])
        c1.write(f"**{it['name']}**　${it['price']}")
        qty = c2.number_input("數量", 0, 999, cart[iid], key=f"q{iid}", label_visibility="collapsed")
        if qty == 0:
            cart.pop(iid)
            st.rerun()
        cart[iid] = qty
        total += it["price"] * qty
    if cart:
        st.markdown(f"### 合計　${total}")
        pay = st.radio("付款方式", PAY, horizontal=True)
        c1, c2 = st.columns(2)
        if c1.button("✅ 結帳", type="primary", use_container_width=True):
            try:
                sid = s.sell(conn, list(cart.items()), pay, user["id"])
                st.session_state.cart = {}
                st.session_state.last_sale = f"已結帳，單號 #{sid}，合計 ${total}"
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
        if c2.button("清空", use_container_width=True):
            st.session_state.cart = {}
            st.rerun()
    if msg := st.session_state.pop("last_sale", None):
        st.success(msg)

    if s.can(user, "void"):
        with st.expander("最近銷貨 / 作廢"):
            for r in s.recent_sales(conn, 10):
                c1, c2 = st.columns([4, 1])
                c1.write(f"#{r['id']}　{r['created_at']}　${r['total']}　{r['pay_method']}　"
                         + ("🚫作廢" if r["status"] == "void" else ""))
                if r["status"] == "done" and c2.button("作廢", key=f"v{r['id']}"):
                    s.void_sale(conn, r["id"], user["id"])
                    st.rerun()


# ───────────────────────── 庫存 ─────────────────────────
def stock_page():
    user = st.session_state.user
    owner = s.can(user, "view_cost")
    st.header("📦 庫存")
    low = s.low_stock(conn)
    if low:
        st.warning("低庫存：" + "、".join(f"{r['name']}（{r['qty']}）" for r in low))
    exp = s.expiring(conn, days=30)
    if exp:
        st.error("即將到期（30 天內）：" + "、".join(
            f"{r['name']} 批號{r['batch_no']} {r['expiry']}（{r['qty']}）" for r in exp))
    rows = s.stock_overview(conn, with_cost=owner)
    for r in rows:
        r["分類"] = CAT[r["category"]]
    cols = ["name", "分類", "qty", "unit", "safety_stock"] + (["cost", "value"] if owner else [])
    names = {"name": "品名", "qty": "庫存", "unit": "單位", "safety_stock": "安全庫存",
             "cost": "成本", "value": "庫存金額"}
    st.dataframe([{names.get(c, c): r[c] for c in cols} for r in rows],
                 use_container_width=True, hide_index=True)
    st.subheader("批號與效期")
    for r in rows:
        bs = s.batches(conn, r["id"])
        if bs:
            st.caption(f"**{r['name']}**： " + "；".join(
                f"{b['batch_no'] or '無批號'} / {b['expiry'] or '無效期'} × {b['qty']}" for b in bs))


# ───────────────────────── 營運主檔 + 進貨（老闆） ─────────────────────────
def items_page():
    user = st.session_state.user
    st.header("🏷️ 營運主檔與進貨")
    t1, t2, t3 = st.tabs(["新增品項", "進貨", "盤點 / 報廢"])
    all_items = s.list_items(conn)
    products = [i for i in all_items if i["type"] == "product"]

    with t1:
        with st.form("additem", clear_on_submit=True):
            name = st.text_input("品名")
            cat = st.selectbox("分類", list(CAT), format_func=CAT.get)
            price = st.number_input("售價", 0, step=10)
            cost = st.number_input("成本（餐點可填單份成本；商品進貨時自動更新）", 0, step=10)
            unit = st.text_input("單位", "件")
            safety = st.number_input("安全庫存（僅商品）", 0)
            if st.form_submit_button("新增", use_container_width=True):
                try:
                    s.add_item(conn, name, cat, price, cost, unit, safety if cat != "meal" else 0)
                    st.success("已新增")
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))
        st.caption("目前品項")
        st.dataframe([{"品名": i["name"], "分類": CAT[i["category"]], "售價": i["price"],
                       "成本": i["cost"], "安全庫存": i["safety_stock"]} for i in all_items],
                     use_container_width=True, hide_index=True)

    with t2:
        if not products:
            st.info("請先新增商品（保健品或有機商品）")
        else:
            with st.form("purchase", clear_on_submit=True):
                it = st.selectbox("商品", products, format_func=lambda i: f"{i['name']}（{CAT[i['category']]}）")
                qty = st.number_input("數量", 1)
                total = st.number_input("進貨總金額", 0, step=10)
                batch = st.text_input("批號（保健品必填）")
                has_exp = st.checkbox("填寫有效期限", value=True)
                exp = st.date_input("有效期限（保健品必填）", date.today() + timedelta(days=365))
                if st.form_submit_button("進貨入庫", use_container_width=True):
                    try:
                        s.purchase(conn, it["id"], qty, total, batch,
                                   exp.isoformat() if has_exp else None, user["id"])
                        st.success("已入庫並記成本")
                    except s.OpsError as e:
                        st.error(str(e))

    with t3:
        if products:
            with st.form("adjust", clear_on_submit=True):
                it = st.selectbox("商品", products, format_func=lambda i: i["name"], key="adj_it")
                kind = st.radio("類型", ["盤點調整", "報廢"], horizontal=True)
                delta = st.number_input("數量（調降填負數）", value=-1, step=1)
                batch = st.text_input("批號（調升保健品必填 / 報廢請填原批號）")
                exp = st.text_input("有效期限 YYYY-MM-DD（調升保健品必填）")
                reason = st.text_input("原因")
                if st.form_submit_button("送出", use_container_width=True):
                    try:
                        s.adjust_stock(conn, it["id"], delta, reason, batch, exp or None,
                                       user["id"], scrap=(kind == "報廢"))
                        st.success("已記錄")
                    except s.OpsError as e:
                        st.error(str(e))


# ───────────────────────── 帳務報表（老闆） ─────────────────────────
def ledger_page():
    user = st.session_state.user
    st.header("📒 帳務報表")
    c1, c2 = st.columns(2)
    start = c1.date_input("起", date.today().replace(day=1))
    end = c2.date_input("迄", date.today())
    r = s.report(conn, start, end)
    a, b, c = st.columns(3)
    a.metric("收入", f"${r['income']:,}")
    b.metric("毛利", f"${r['gross_profit']:,}")
    c.metric("現金淨額", f"${r['net']:,}")
    st.caption(f"進貨成本 ${r['purchase_cost']:,}　支出 ${r['expense']:,}　"
               f"已售商品成本 ${r['cogs']:,}（毛利 = 收入 − 已售商品成本；現金淨額 = 收入 − 進貨成本 − 支出）")
    if r["by_category"]:
        st.dataframe([{"分類": CAT[x["category"]], "營收": x["revenue"], "數量": x["qty"]}
                      for x in r["by_category"]], use_container_width=True, hide_index=True)
    with st.expander("登錄支出"):
        with st.form("exp", clear_on_submit=True):
            amt = st.number_input("金額", 0, step=10)
            note = st.text_input("說明（例：水電、租金）")
            if st.form_submit_button("記一筆支出", use_container_width=True):
                try:
                    s.add_expense(conn, amt, note, user["id"])
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))
    rows = conn.execute(
        "SELECT date, kind, amount, source, note FROM ledger WHERE date BETWEEN ? AND ?"
        " ORDER BY id DESC", (start.isoformat(), end.isoformat())).fetchall()
    kinds = {"income": "收入", "cost": "進貨成本", "expense": "支出"}
    data = [{"日期": x["date"], "類型": kinds[x["kind"]], "金額": x["amount"], "說明": x["note"]}
            for x in rows]
    st.dataframe(data, use_container_width=True, hide_index=True)
    if data:
        import csv, io
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=list(data[0]))
        w.writeheader(); w.writerows(data)
        st.download_button("⬇️ 匯出 CSV（給會計）", "﻿" + buf.getvalue(),
                           file_name="ledger.csv", mime="text/csv")


# ───────────────────────── 使用者（老闆） ─────────────────────────
def users_page():
    st.header("👥 使用者（最多 4 人）")
    for u in conn.execute("SELECT name, role FROM user WHERE active=1"):
        st.write(f"{u['name']}　{'老闆' if u['role'] == s.OWNER else '店員'}")
    st.subheader("Line 記帳綁定")
    if st.button("產生 Line 綁定碼", use_container_width=True):
        try:
            code = linebot.create_bind_code(conn, st.session_state.user["id"])
            st.success(f"綁定碼：**{code}**（10 分鐘內有效）。到 Line 官方帳號傳：綁定 {code}")
        except s.OpsError as e:
            st.error(str(e))
    bound = conn.execute(
        "SELECT u.name, l.bound_at FROM line_user l JOIN user u ON u.id=l.user_id").fetchall()
    for b in bound:
        st.caption(f"已綁定：{b['name']}（{b['bound_at']}）")
    st.divider()
    st.subheader("新增使用者")
    with st.form("newuser", clear_on_submit=True):
        name = st.text_input("姓名")
        role = st.selectbox("角色", [s.STAFF, s.OWNER], format_func=lambda r: "店員" if r == s.STAFF else "老闆")
        pin = st.text_input("PIN（至少 4 位數字）", type="password")
        if st.form_submit_button("新增使用者", use_container_width=True):
            try:
                s.create_user(conn, name, role, pin)
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))


# ───────────────────────── 餐點：點餐 / 廚房 / 訂單結帳 ─────────────────────────
QUICK_NOTES = ["少辣", "不辣", "加辣", "不要香菜", "不要蔥", "少冰", "去冰", "少糖", "無糖", "打包"]
ORDER_ICON = {"new": "🆕", "preparing": "🔥", "ready": "🛎️", "served": "✅", "cancelled": "🚫"}


def order_page():
    user = st.session_state.user
    st.header("🍽️ 點餐")
    if msg := st.session_state.pop("last_order", None):
        st.success(msg)
    cart = st.session_state.setdefault("ocart", [])
    otype = st.radio("類型", ["dine_in", "takeout"], horizontal=True,
                     format_func=lambda x: "內用" if x == "dine_in" else "外帶")
    table = ""
    if otype == "dine_in":
        table = st.text_input("桌號", key="o_table", placeholder="例：A3")
        if table.strip() and orders.open_order_for_table(conn, table):
            st.info(f"桌 {table.strip()} 已有未結帳訂單，本次會以「加點」送給廚房。")

    meals = [i for i in s.list_items(conn) if i["type"] == "meal"]
    if not meals:
        st.info("還沒有餐點，請老闆先到「營運主檔/進貨」新增分類為「餐點」的品項。")
    st.caption("點一下餐點加入訂單")
    for it in meals:
        if st.button(f"{it['name']}　${it['price']}", key=f"om{it['id']}", use_container_width=True):
            cart.append({"uid": uuid4().hex[:8], "item_id": it["id"]})
            st.rerun()

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
            e["quick"] = st.multiselect("特殊需求", QUICK_NOTES, key=f"on{e['uid']}", placeholder="常用需求")
            e["text"] = st.text_input("其他需求", key=f"ot{e['uid']}", placeholder="例：醬分開、飯少一點")
            total += it["price"] * e["qty"]
    if not cart:
        st.caption("尚未選擇餐點")
        return
    order_note = st.text_input("整單備註（過敏、趕時間…）", key="o_note")
    st.markdown(f"### 合計　${total}")
    if st.button("📨 送出到廚房", type="primary", use_container_width=True):
        lines = [{"item_id": e["item_id"], "qty": e["qty"],
                  "note": "、".join(e.get("quick", []) + ([e["text"].strip()] if e.get("text", "").strip() else []))}
                 for e in cart]
        try:
            r = orders.place_order(conn, otype, lines, table, order_note, user["id"])
            where = f"桌 {r['table_no']}" if otype == "dine_in" else f"外帶取餐號 #{r['pickup_no']}"
            st.session_state.ocart = []
            for k in ("o_note", "o_table"):
                st.session_state.pop(k, None)
            st.session_state.last_order = f"已送出：{where}" + (f"（第 {r['round_no']} 輪加點）" if r["is_addon"] else "")
            st.rerun()
        except s.OpsError as e:
            st.error(str(e))


@st.fragment(run_every=5)
def _kitchen_board():
    c = db.connect()   # fragment 會在背景自動重跑，使用獨立連線
    try:
        tickets = orders.kitchen_tickets(c)
        seen = st.session_state.get("kitchen_seen", set())
        keys = {(t["order_id"], t["round_no"]) for t in tickets}
        fresh = keys - seen
        if fresh and "kitchen_seen" in st.session_state:
            st.toast(f"🔔 有 {len(fresh)} 張新單！")
        st.session_state.kitchen_seen = keys
        st.caption(f"待處理 {len(tickets)} 張　每 5 秒自動更新")
        if not tickets:
            st.success("目前沒有待處理的單 👍")
        for t in tickets:
            with st.container(border=True):
                wait = orders.wait_minutes(t["created_at"])
                head = f"{ORDER_ICON[t['status']]} **{t['where']}**"
                if t["is_addon"]:
                    head += f"　:red[加點（第 {t['round_no']} 輪）]"
                st.markdown(f"{head}　{orders.LABEL[t['status']]}　⏱ {wait} 分鐘")
                if t["order_note"]:
                    st.markdown(f":red[⚠️ 整單備註：{t['order_note']}]")
                for ln in t["lines"]:
                    st.markdown(f"### {ln['qty']} × {ln['name']}")
                    if ln["note"]:
                        st.markdown(f":red[　➜ {ln['note']}]")
                label = "🔥 開始製作" if t["status"] == "new" else "🛎️ 完成出餐"
                if st.button(label, key=f"k{t['order_id']}_{t['round_no']}", use_container_width=True,
                             type="primary" if t["status"] == "new" else "secondary"):
                    orders.advance_ticket(c, t["order_id"], t["round_no"])
                    st.rerun()
    finally:
        c.close()


def kitchen_page():
    st.header("👨‍🍳 廚房")
    _kitchen_board()


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
        labels = {o["id"]: f"{o['where']}　${o['total']}"
                  + (f"　🛎️待上菜 {o['to_serve']}" if o["to_serve"] else "")
                  + (f"　🔥製作中 {o['unfinished']}" if o["unfinished"] else "") for o in ods}
        sel = st.radio("未結帳訂單", list(labels), format_func=labels.get, key="sel_order")
        od = next(o for o in ods if o["id"] == sel)
        if od["note"]:
            st.markdown(f":red[⚠️ 整單備註：{od['note']}]")
        for ln in od["lines"]:
            c1, c2 = st.columns([4, 2])
            txt = f"{ORDER_ICON[ln['status']]} {ln['qty']} × {ln['name']}　${ln['qty'] * ln['unit_price']}"
            if ln["round_no"] > 1:
                txt += f"　(加點{ln['round_no']})"
            if ln["status"] == "cancelled":
                txt = f"~~{txt}~~"
            c1.markdown(txt + (f"  \n:red[➜ {ln['note']}]" if ln["note"] else ""))
            if ln["status"] == "ready" and c2.button("已上菜", key=f"sv{ln['id']}"):
                orders.serve_line(c, ln["id"])
                st.rerun()
            elif ln["status"] == "new" and c2.button("取消", key=f"cl{ln['id']}"):
                try:
                    orders.cancel_line(c, ln["id"])
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))
        st.markdown(f"### 合計　${od['total']}")
        if od["unfinished"]:
            st.caption(f"尚有 {od['unfinished']} 項未出餐（仍可先結帳）")
        pay = st.radio("付款方式", PAY, horizontal=True, key="pay_order")
        b1, b2 = st.columns(2)
        if b1.button("💵 結帳", type="primary", use_container_width=True):
            try:
                sid = orders.checkout_order(c, od["id"], pay, user["id"])
                st.session_state.last_order = f"已結帳：{od['where']}　${od['total']}（單號 #{sid}）"
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
        if s.can(user, "void") and b2.button("取消整張訂單", use_container_width=True):
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


# ───────────────────────── 導覽（依權限） ─────────────────────────
if "user" not in st.session_state:
    login_view()
else:
    user = st.session_state.user
    pages = [st.Page(order_page, title="點餐", icon="🍽️", url_path="order"),
             st.Page(kitchen_page, title="廚房", icon="👨‍🍳", url_path="kitchen"),
             st.Page(orders_page, title="訂單/結帳", icon="🧾", url_path="orders"),
             st.Page(pos_page, title="POS（商品）", icon="🛒", url_path="pos"),
             st.Page(stock_page, title="庫存", icon="📦", url_path="stock")]
    if s.can(user, "report"):
        pages += [st.Page(items_page, title="營運主檔/進貨", icon="🏷️", url_path="items"),
                  st.Page(ledger_page, title="帳務報表", icon="📒", url_path="ledger"),
                  st.Page(users_page, title="使用者", icon="👥", url_path="users")]
    st.navigation(pages).run()
    with st.sidebar:
        st.caption(f"{user['name']}（{'老闆' if user['role'] == s.OWNER else '店員'}）")
        if st.button("登出"):
            del st.session_state["user"]
            st.rerun()
