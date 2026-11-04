"""營運後台：POS（保健品／有機商品）、庫存、營運主檔、帳務。啟動：streamlit run ops_app.py
餐點的點餐／廚房／結帳是獨立系統，見 meal_app.py。
"""
from datetime import date, timedelta

import streamlit as st

from ops import db, linebot, services as s
from ops.ui import CAT, PAY, login_view, sidebar_user

st.set_page_config(page_title="營運系統", page_icon="🧾", layout="centered")

# 每次執行畫面都開新連線：SQLite 開啟很快，也避免多使用者共用連線的執行緒問題
conn = db.connect()


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
    for u in conn.execute("SELECT name, role, kitchen FROM user WHERE active=1"):
        label = "廚房（僅點餐、廚房頁）" if u["kitchen"] else ("老闆" if u["role"] == s.OWNER else "店員")
        st.write(f"{u['name']}　{label}")
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
        role = st.selectbox("角色", [s.STAFF, s.KITCHEN, s.OWNER],
                            format_func={s.STAFF: "店員", s.KITCHEN: "廚房（只能用點餐、廚房頁）", s.OWNER: "老闆"}.get)
        pin = st.text_input(f"PIN（至少 {s.MIN_PIN_LENGTH} 位數字）", type="password")
        if st.form_submit_button("新增使用者", use_container_width=True):
            try:
                s.create_user(conn, name, role, pin)
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))

    st.divider()
    st.subheader("📱 裝置登入連結（平板書籤）")
    st.caption("給廚房／服務生的平板用：把連結存成書籤或加入主畫面，之後一點就登入，不必輸入 PIN。"
               "連結等同密碼，只在產生時顯示一次；平板遺失或換人時請撤銷。老闆帳號不能使用。")
    others = [dict(u) for u in conn.execute("SELECT id, name, role, kitchen FROM user WHERE active=1 AND role!='owner'")]
    if others:
        who = st.selectbox("給哪個帳號", others, format_func=lambda u: f"{u['name']}（{'廚房' if u['kitchen'] else '店員'}）")
        label = st.text_input("裝置名稱", placeholder="例：廚房平板")
        base = st.text_input("餐點系統網址", placeholder="例：https://你的網址/ 或 http://192.168.1.23:8502/")
        if st.button("產生連結", use_container_width=True):
            try:
                tok = s.create_device_token(conn, st.session_state.user, who["id"], label)
                st.success("已產生。請現在就複製到平板（關閉後不會再顯示）：")
                st.code(f"{base.rstrip('/')}/?device={tok}" if base.strip() else f"?device={tok}", language=None)
            except s.OpsError as e:
                st.error(str(e))
    for t in s.list_device_tokens(conn):
        c1, c2 = st.columns([4, 1])
        c1.write(f"{t['label'] or '（未命名）'}　{t['user_name']}　建立 {t['created_at']}"
                 f"　最近使用 {t['last_used_at'] or '從未'}" + ("　🚫已撤銷" if t["revoked"] else ""))
        if not t["revoked"] and c2.button("撤銷", key=f"rv{t['id']}"):
            s.revoke_device_token(conn, t["id"])
            st.rerun()


# ───────────────────────── 導覽（依權限） ─────────────────────────
if "user" not in st.session_state:
    login_view(conn, "🧾 營運後台")
else:
    user = st.session_state.user
    if s.is_kitchen(user):   # 廚房帳號只能使用餐點系統（點餐、廚房）
        st.error("🔒 這個帳號只能使用「餐點點餐系統」（點餐、廚房），不能進入營運後台。")
        sidebar_user(user)
        st.stop()
    pages = [st.Page(pos_page, title="POS（商品）", icon="🛒", url_path="pos"),
             st.Page(stock_page, title="庫存", icon="📦", url_path="stock")]
    if s.can(user, "report"):
        pages += [st.Page(items_page, title="營運主檔/進貨", icon="🏷️", url_path="items"),
                  st.Page(ledger_page, title="帳務報表", icon="📒", url_path="ledger"),
                  st.Page(users_page, title="使用者", icon="👥", url_path="users")]
    st.navigation(pages).run()
    sidebar_user(user)
