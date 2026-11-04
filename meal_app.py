"""餐點點餐系統（獨立 App，手機／平板優先、觸控操作）。

頁面：外場（即時總覽，首頁）→ 點餐 → 廚房（接單開關）→ 訂單/結帳 → 設定（老闆）
啟動：streamlit run meal_app.py --server.port 8502
與 ops_app.py（營運後台）共用同一個資料庫與使用者（OPS_DB），結帳的收入會自動進帳務報表。
"""
from uuid import uuid4

import streamlit as st

from ops import db, orders, services as s, uber
from ops.ui import PAY, login_view, sidebar_user

st.set_page_config(page_title="餐點點餐系統", page_icon="🍽️", layout="wide",
                   initial_sidebar_state="collapsed")

QUICK_NOTES = ["少辣", "不辣", "加辣", "不要香菜", "不要蔥", "少冰", "去冰", "少糖", "無糖", "打包"]
ICON = {"new": "🆕", "preparing": "🔥", "ready": "🛎️", "served": "✅", "cancelled": "🚫"}
# 桌位／流程階段顏色：綠=空桌、紅=待接單、橘=製作中、藍=待上菜（要服務生處理）、黃=用餐中
TILE = {"empty": "🟩", "new": "🟥", "preparing": "🟧", "ready": "🟦", "dining": "🟨"}
FLOW_ORDER = ["empty", "new", "preparing", "ready", "dining"]

# 觸控與手機優先：大按鈕、手機上欄位不換行（加減鈕、桌位格子維持同一列）、流程圖可左右滑動
st.markdown("""
<style>
.block-container{padding:3.6rem .7rem 4rem;max-width:1100px}
.stButton>button{min-height:3.4rem;font-size:1.1rem;font-weight:600;border-radius:14px;line-height:1.3}
.stButton>button p{font-size:1.05rem}
[data-testid="stRadio"] label{padding:.45rem .3rem}
.stTextInput input,.stSelectbox div[data-baseweb="select"]{min-height:3rem;font-size:1.1rem}
button[data-baseweb="tab"]{min-height:3rem;font-size:1.15rem;padding:0 1.1rem}
.num{text-align:center;font-size:1.9rem;font-weight:800;line-height:3.4rem}
@media (max-width:640px){
 [data-testid="stHorizontalBlock"]{flex-wrap:nowrap !important;gap:.4rem !important}
 [data-testid="stColumn"],[data-testid="column"]{min-width:0 !important;flex:1 1 0% !important;width:auto !important}
 .stButton>button{padding:.4rem .2rem}
}
.flow{display:flex;gap:.15rem;align-items:center;padding:.5rem 0}
.flow .node{flex:1 1 0;min-width:0;text-align:center;padding:.4rem .15rem;border-radius:14px;border:3px solid;background:rgba(127,127,127,.08)}
.flow .node b{display:block;font-size:1.5rem;line-height:1.2}
.flow .node span{font-size:.8rem;white-space:nowrap}
.flow .arrow{font-size:1.2rem;opacity:.45;flex:0 0 auto}
[data-testid="stPageLink"] a{justify-content:center;padding:.5rem .1rem;border-radius:12px;font-weight:700;min-height:3rem}
[data-testid="stPageLink"] a p{font-size:.95rem;white-space:nowrap}
.n-empty{border-color:#2e9e5b}.n-new{border-color:#e5484d}.n-preparing{border-color:#f08c00}
.n-ready{border-color:#2f7de1}.n-dining{border-color:#c9a400}
.badge-on{background:#2e9e5b;color:#fff;padding:.25rem .8rem;border-radius:999px;font-weight:700}
.badge-off{background:#e5484d;color:#fff;padding:.25rem .8rem;border-radius:999px;font-weight:700}
[data-testid="stAppDeployButton"],.stDeployButton{display:none}
</style>
""", unsafe_allow_html=True)

conn = db.connect()
USE_TOPBAR = False   # 啟動導覽後依 Streamlit 版本決定
PAGES = {}


# ───────────────────────── 共用觸控元件 ─────────────────────────
def _bump(key, delta, lo, hi):
    st.session_state[key] = min(hi, max(lo, st.session_state.get(key, lo) + delta))


def stepper(key, lo, hi, init=None):
    """大型加減鈕：－ 數字 ＋（手機上也維持同一列）。"""
    st.session_state.setdefault(key, lo if init is None else init)
    st.session_state[key] = min(hi, max(lo, st.session_state[key]))
    c1, c2, c3 = st.columns(3)
    c1.button("－", key=f"{key}_m", on_click=_bump, args=(key, -1, lo, hi), use_container_width=True)
    c2.markdown(f'<div class="num">{st.session_state[key]}</div>', unsafe_allow_html=True)
    c3.button("＋", key=f"{key}_p", on_click=_bump, args=(key, 1, lo, hi), use_container_width=True)
    return st.session_state[key]


def quick_pick(label, options, key):
    """特殊需求：觸控選項；舊版 Streamlit 沒有 pills 時改用多選框。"""
    if hasattr(st, "pills"):
        return list(st.pills(label, options, selection_mode="multi", key=key) or [])
    return st.multiselect(label, options, key=key)


def _set(key, value):
    st.session_state[key] = value


def choice_buttons(key, options, labels=None, cols=None):
    """大按鈕單選（取代小圓點）。options 是值，labels 是 {值: 顯示文字}。回傳目前選到的值。"""
    labels = labels or {}
    cur = st.session_state.get(key)
    if cur not in options:
        cur = st.session_state[key] = options[0]
    n = cols or len(options)
    for i in range(0, len(options), n):
        row = st.columns(n)
        for col, opt in zip(row, options[i:i + n]):
            col.button(labels.get(opt, str(opt)), key=f"{key}__{opt}", on_click=_set, args=(key, opt),
                       type="primary" if opt == cur else "secondary", use_container_width=True)
    return cur


def go(page, **state):
    """換頁（像流程圖一樣一步到位）：先記下要帶過去的資料，再重新整理並切換。"""
    st.session_state.update(state)
    st.session_state["go_to"] = page
    st.rerun()


def top_bar():
    """頂端導覽列：每頁最上方一排大按鈕（手機也維持同一列），取代 Streamlit 預設導覽。"""
    if not USE_TOPBAR:
        return
    cols = st.columns(len(PAGES))
    for col, pg in zip(cols, PAGES.values()):
        col.page_link(pg, label=pg.title, icon=pg.icon, use_container_width=True)


def kitchen_banner(c):
    ks = orders.kitchen_status(c)
    if ks["accepting"]:
        st.markdown('<span class="badge-on">🟢 廚房接單中</span>', unsafe_allow_html=True)
    else:
        st.error("🔴 廚房暫停接單" + (f"：{ks['reason']}" if ks["reason"] else "") + "　（暫時不能送新單）")
    return ks["accepting"]


def uber_alert(c):
    """Uber 訂單處理失敗時的醒目提醒（不能默默漏單）。"""
    n = orders.external_alerts(c)
    if n:
        st.warning(f"⚠️ 有 {n} 筆 Uber 訂單處理失敗（最近 3 小時），請到 Uber 商家平板確認，必要時手動補單。")


def flow_strip(flow):
    nodes = []
    for k in FLOW_ORDER:
        nodes.append(f'<div class="node n-{k}"><b>{flow.get(k, 0)}</b><span>{TILE[k]} {orders.STAGE_LABEL[k]}</span></div>')
    return '<div class="flow">' + '<div class="arrow">›</div>'.join(nodes) + "</div>"


def table_grid(tables, on_pick, selected=None, cols=3, key="t"):
    """桌位格子（可點）。on_pick(name) 在點擊時呼叫。"""
    if len(tables) == 4:   # 每層 4 桌：排成 2×2，比 3＋1 整齊也更好點
        cols = 2
    for i in range(0, len(tables), cols):
        row = st.columns(cols)
        for col, t in zip(row, tables[i:i + cols]):
            stage = t.get("stage", "empty")
            mark = "👉" if t["name"] == selected else TILE[stage]
            if stage == "empty":
                label = f"{mark} {t['name']}  \n空桌  \n{t['seats']}人"
            else:
                serve = f" 🛎️{t['to_serve']}" if t.get("to_serve") else ""
                guests = f"{t['guests']}/{t['seats']}人" if t.get("guests") else f"{t['seats']}人"
                label = f"{mark} {t['name']}  \n{orders.STAGE_LABEL[stage]}{serve}  \n{guests}"
            col.button(label, key=f"{key}_{t['name']}", on_click=on_pick, args=(t["name"],),
                       type="primary" if t["name"] == selected else "secondary",
                       use_container_width=True)


# ───────────────────────── 外場：即時總覽 ─────────────────────────
def _pick_table(name):
    st.session_state["sel_table"] = name


@st.fragment(run_every=5)
def _floor_board():
    c = db.connect()   # fragment 會自動重跑，使用獨立連線
    try:
        user = st.session_state.user
        ov = orders.floor_overview(c)
        ready = sum(t["to_serve"] for t in ov["tables"]) + sum(t["to_serve"] for t in ov["takeouts"])
        prev = st.session_state.get("floor_ready_seen")
        if prev is not None and ready > prev:
            st.toast("🛎️ 有餐點完成，可以上菜了！")
        st.session_state["floor_ready_seen"] = ready

        kitchen_banner(c)
        uber_alert(c)
        st.markdown(flow_strip(ov["flow"]), unsafe_allow_html=True)
        st.caption("流程：下單 → 廚房接單 → 製作 → 上菜 → 用餐 → 結帳　｜　每 5 秒自動更新　｜　點桌子操作")

        if not ov["tables"]:
            st.info("還沒有設定桌位。")
            if s.can(user, "report"):
                f1 = st.number_input("1 樓桌數", 0, 99, orders.DEFAULT_FLOORS[1])
                f2 = st.number_input("2 樓桌數", 0, 99, orders.DEFAULT_FLOORS[2])
                if st.button("建立桌位（每桌 4 人）", type="primary", use_container_width=True):
                    try:
                        orders.setup_tables(c, {1: f1, 2: f2}, seats=4)
                        st.rerun()
                    except s.OpsError as e:
                        st.error(str(e))
            else:
                st.caption("請老闆到「設定」頁建立桌位。")
        else:
            floors = ov["floors"]
            names = {0: "其他", 1: "1 樓", 2: "2 樓"}
            tabs = st.tabs([f"{names.get(f, f'{f} 樓')}（{sum(1 for t in ov['tables'] if t['floor'] == f and t['stage'] != 'empty')}/"
                            f"{sum(1 for t in ov['tables'] if t['floor'] == f)}）" for f in floors])
            for tab, f in zip(tabs, floors):
                with tab:
                    table_grid([t for t in ov["tables"] if t["floor"] == f], _pick_table,
                               st.session_state.get("sel_table"), key=f"fl{f}")

        sel = st.session_state.get("sel_table")
        t = next((t for t in ov["tables"] if t["name"] == sel), None)
        if t:
            _table_panel(c, t, ov)

        if ov["takeouts"]:
            st.subheader("🥡 外帶")
            for tk in ov["takeouts"]:
                with st.container(border=True):
                    icon = "🛵 " if tk["where"].startswith("Uber") else ""
                    st.markdown(f"{TILE[tk['stage']]} {icon}**{tk['where']}**　{orders.STAGE_LABEL[tk['stage']]}"
                                f"　⏱ {tk['wait']} 分鐘　${tk['unpaid_total']}")
                    if tk["to_serve"] and st.button(f"🛎️ 已取餐（{tk['to_serve']}）", key=f"tk_s{tk['order_id']}",
                                                    type="primary", use_container_width=True):
                        orders.serve_all_ready(c, tk["order_id"])
                        st.rerun()
                    if st.button("💵 結帳", key=f"tk_p{tk['order_id']}", use_container_width=True):
                        go("orders", sel_order=tk["order_id"])
    finally:
        c.close()


def _table_panel(c, t, ov):
    with st.container(border=True):
        stage = t["stage"]
        st.markdown(f"## {TILE[stage]} 桌 {t['name']}　{orders.STAGE_LABEL[stage]}")
        if stage == "empty":
            st.caption(f"空桌（{t['seats']} 人桌）　選人數後開桌點餐")
            n = stepper("guests", 1, t["seats"], 2)
            if st.button(f"🍽️ 開桌點餐（{n} 位）", type="primary", use_container_width=True, key="panel_open"):
                go("order", order_table=t["name"], order_type="dine_in")
            return
        c1, c2, c3 = st.columns(3)
        c1.metric("人數", f"{t['guests'] or '-'}")
        c2.metric("等候", f"{t['wait']}分")
        c3.metric("待付", f"${t['unpaid_total']}")
        cnt = t["counts"]
        st.markdown(f"🆕 待接單 {cnt.get('new', 0)}　🔥 製作中 {cnt.get('preparing', 0)}　🛎️ 待上菜 {cnt.get('ready', 0)}")
        if t["note"]:
            st.markdown(f":red[⚠️ {t['note']}]")
        if t["to_serve"] and st.button(f"🛎️ 上菜（{t['to_serve']} 道已完成）", type="primary",
                                       use_container_width=True, key="panel_serve"):
            orders.serve_all_ready(c, t["order_id"])
            st.rerun()
        a, b = st.columns(2)
        if a.button("➕ 加點", use_container_width=True, key="panel_add"):
            go("order", order_table=t["name"], order_type="dine_in")
        if b.button("💵 結帳", use_container_width=True, key="panel_pay"):
            go("orders", sel_order=t["order_id"])
        with st.expander("🔀 換桌 / 併桌"):
            others = [x for x in ov["tables"] if x["name"] != t["name"] and x["floor"] != 0]
            st.caption("點選要移到的桌子：🟩空桌 = 換桌，有客人的桌 = 併桌")
            target = st.session_state.get("mv_target")
            if target not in [x["name"] for x in others]:
                target = None
            table_grid(others, lambda n: st.session_state.__setitem__("mv_target", n), target, key="mv")
            if target and st.button(f"確認：桌 {t['name']} → 桌 {target}", key="panel_move",
                                    type="primary", use_container_width=True):
                try:
                    r = orders.merge_tables(c, t["name"], target)
                    st.session_state["sel_table"] = target
                    st.session_state.pop("mv_target", None)
                    st.toast(("已換桌" if r["action"] == "move" else "已併桌") + f"：{t['name']} → {target}")
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))


def floor_page():
    top_bar()
    st.header("🗺️ 外場即時狀況")
    if msg := st.session_state.pop("last_order", None):
        st.success(msg)
    _floor_board()


# ───────────────────────── 點餐（觸控） ─────────────────────────
def _pick_order_table(name):
    st.session_state["order_table"] = name
    st.session_state["pick_open"] = False


def order_page():
    user = st.session_state.user
    top_bar()
    st.header("🍽️ 點餐")
    accepting = kitchen_banner(conn)
    cart = st.session_state.setdefault("ocart", [])

    otype = choice_buttons("order_type", ["dine_in", "takeout"], {"dine_in": "🍽️ 內用", "takeout": "🥡 外帶"})
    table = None
    if otype == "dine_in":
        ov = orders.floor_overview(conn)
        table = st.session_state.get("order_table")
        if table and not any(t["name"] == table for t in ov["tables"]) and ov["tables"]:
            table = st.session_state["order_table"] = None
        info = next((t for t in ov["tables"] if t["name"] == table), None)
        if table and not st.session_state.get("pick_open"):
            c1, c2 = st.columns([3, 2])
            c1.markdown(f"### 桌 {table}" + ("　:orange[加點]" if info and info["stage"] != "empty" else ""))
            if c2.button("更換桌位", use_container_width=True):
                st.session_state["pick_open"] = True
                st.rerun()
        else:
            st.caption("點選桌位")
            if not ov["tables"]:
                st.info("請先到「設定」建立桌位。")
            floors = ov["floors"]
            tabs = st.tabs([{0: "其他", 1: "1 樓", 2: "2 樓"}.get(f, f"{f} 樓") for f in floors]) if floors else []
            for tab, f in zip(tabs, floors):
                with tab:
                    table_grid([t for t in ov["tables"] if t["floor"] == f], _pick_order_table,
                               table, key=f"ot{f}")
        if table and info and info["stage"] == "empty":
            st.markdown("**用餐人數**")
            stepper("guests", 1, info["seats"], 2)

    meals = [i for i in s.list_items(conn) if i["type"] == "meal"]
    if not meals:
        st.info("還沒有餐點，請老闆到「設定」新增。")
    st.markdown("**點餐點**（點一下加入）")
    for i in range(0, len(meals), 3):
        row = st.columns(3)
        for col, it in zip(row, meals[i:i + 3]):
            if col.button(f"{it['name']}  \n${it['price']}", key=f"om{it['id']}", use_container_width=True):
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
            st.markdown(f"**{it['name']}**　${it['price']}")
            e["qty"] = stepper(f"oq{e['uid']}", 1, 99, 1)
            e["quick"] = quick_pick("特殊需求", QUICK_NOTES, f"on{e['uid']}")
            e["text"] = st.text_input("其他需求", key=f"ot_{e['uid']}", placeholder="例：醬分開、飯少一點")
            if st.button("🗑 移除這道", key=f"od{e['uid']}", use_container_width=True):
                cart.remove(e)
                st.rerun()
            total += it["price"] * e["qty"]
    if not cart:
        st.caption("尚未選擇餐點")
        return
    order_note = st.text_input("整單備註（過敏、趕時間…）", key="o_note")
    st.markdown(f"### 合計　${total}")
    if st.button("📨 送出到廚房", type="primary", use_container_width=True, disabled=not accepting):
        lines = [{"item_id": e["item_id"], "qty": e["qty"],
                  "note": "、".join(e.get("quick", []) + ([e["text"].strip()] if e.get("text", "").strip() else []))}
                 for e in cart]
        try:
            r = orders.place_order(conn, otype, lines, table, order_note, user["id"],
                                   guests=st.session_state.get("guests") if otype == "dine_in" else None)
            where = f"桌 {r['table_no']}" if otype == "dine_in" else f"外帶取餐號 #{r['pickup_no']}"
            for k in [k for k in st.session_state if str(k).startswith(("oq", "on", "ot_"))]:
                st.session_state.pop(k, None)
            for k in ("o_note", "order_table", "guests", "pick_open"):
                st.session_state.pop(k, None)
            go("floor", ocart=[], last_order=f"已送出到廚房：{where}"
               + (f"（第 {r['round_no']} 輪加點）" if r["is_addon"] else ""))
        except s.OpsError as e:
            st.error(str(e))


# ───────────────────────── 廚房（接單開關＋看板） ─────────────────────────
@st.fragment(run_every=5)
def _kitchen_board():
    c = db.connect()
    try:
        uber_alert(c)
        ks = orders.kitchen_status(c)
        if ks["accepting"]:
            st.markdown('<span class="badge-on">🟢 接單中</span>', unsafe_allow_html=True)
            reason = st.text_input("暫停原因（選填，會顯示給外場）", key="k_reason", placeholder="例：客滿、食材售完")
            if st.button("⏸ 暫停接單", use_container_width=True, key="k_pause"):
                orders.set_kitchen_accepting(c, False, reason)
                st.rerun()
        else:
            st.error("🔴 暫停接單中" + (f"：{ks['reason']}" if ks["reason"] else ""))
            if st.button("▶️ 恢復接單", type="primary", use_container_width=True, key="k_resume"):
                orders.set_kitchen_accepting(c, True)
                st.rerun()

        tickets = orders.kitchen_tickets(c)
        keys = {(t["order_id"], t["round_no"]) for t in tickets}
        if "kitchen_seen" in st.session_state and keys - st.session_state.kitchen_seen:
            st.toast(f"🔔 有 {len(keys - st.session_state.kitchen_seen)} 張新單！")
        st.session_state.kitchen_seen = keys
        st.caption(f"待處理 {len(tickets)} 張　每 5 秒自動更新")
        if not tickets:
            st.success("目前沒有待處理的單 👍")
        for t in tickets:
            with st.container(border=True):
                head = f"{ICON[t['status']]} **{t['where']}**"
                if t["is_addon"]:
                    head += f"　:red[加點（第 {t['round_no']} 輪）]"
                for origin in t["origins"]:
                    head += f"　:orange[{origin}]"
                state = "待接單" if t["status"] == "new" else "已接單・製作中"
                st.markdown(f"{head}　{state}　⏱ {orders.wait_minutes(t['created_at'])} 分鐘")
                if t["order_note"]:
                    st.markdown(f":red[⚠️ 整單備註：{t['order_note']}]")
                for ln in t["lines"]:
                    st.markdown(f"### {ln['qty']} × {ln['name']}")
                    if ln["note"]:
                        st.markdown(f":red[　➜ {ln['note']}]")
                label = "✅ 接單・開始製作" if t["status"] == "new" else "🛎️ 完成出餐"
                if st.button(label, key=f"k{t['order_id']}_{t['round_no']}", use_container_width=True,
                             type="primary" if t["status"] == "new" else "secondary"):
                    try:
                        new_status = orders.advance_ticket(c, t["order_id"], t["round_no"])
                        if new_status == "ready" and uber.UberConfig().configured:
                            uber.notify_ready(c, uber.UberClient(), t["order_id"])   # 失敗只記錄，不影響店內作業
                    except s.OpsError as e:
                        st.error(str(e))
                    st.rerun()
    finally:
        c.close()


def kitchen_page():
    top_bar()
    st.header("👨‍🍳 廚房")
    if msg := st.session_state.pop("last_order", None):
        st.success(msg)
    _kitchen_board()


# ───────────────────────── 訂單 / 結帳 ─────────────────────────
@st.fragment(run_every=5)
def _orders_board():
    user = st.session_state.user
    c = db.connect()
    try:
        ods = orders.open_orders(c)
        if not ods:
            st.info("目前沒有未結帳的訂單")
            return
        labels = {o["id"]: f"{o['where']}　待付 ${o['unpaid_total']}"
                  + (f"　🛎️待上菜 {o['to_serve']}" if o["to_serve"] else "")
                  + (f"　🔥製作中 {o['unfinished']}" if o["unfinished"] else "") for o in ods}
        st.markdown("**選擇訂單**")
        sel = choice_buttons("sel_order", list(labels), labels, cols=1)
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
            with st.container(border=True):
                st.markdown(txt + (f"  \n:red[➜ {ln['note']}]" if ln["note"] else ""))
                if ln["remaining"] > 0:
                    st.caption("本次要結幾份（分開結帳）")
                    # key 帶上剩餘份數：付款後上限改變，數字自動歸零
                    q = stepper(f"pq{ln['id']}_{ln['remaining']}", 0, ln["remaining"], 0)
                    if q:
                        picks.append((ln["id"], q))
                        pick_total += q * ln["unit_price"]
                b1, b2 = st.columns(2)
                if ln["status"] == "ready" and b1.button("🛎️ 已上菜", key=f"sv{ln['id']}", use_container_width=True):
                    orders.serve_line(c, ln["id"])
                    st.rerun()
                if ln["status"] == "new" and ln["paid_qty"] == 0 and b2.button(
                        "取消這道", key=f"cl{ln['id']}", use_container_width=True):
                    try:
                        orders.cancel_line(c, ln["id"])
                        st.rerun()
                    except s.OpsError as e:
                        st.error(str(e))

        st.markdown(f"### 合計 ${od['total']}　已付 ${od['paid_total']}　**待付 ${od['unpaid_total']}**")
        if od["unfinished"]:
            st.caption(f"尚有 {od['unfinished']} 項未出餐（仍可先結帳）")
        st.markdown("**付款方式**")
        pay = choice_buttons("pay_order", PAY, cols=2)
        if st.button(f"💵 結帳勾選品項　${pick_total}", disabled=not picks, use_container_width=True):
            try:
                sid = orders.checkout_lines(c, od["id"], picks, pay, user["id"])
                go("orders", last_order=f"已結帳 ${pick_total}：{od['where']}（單號 #{sid}）")
            except s.OpsError as e:
                st.error(str(e))
        if st.button(f"💵 全部結清　${od['unpaid_total']}", type="primary", use_container_width=True):
            try:
                sid = orders.checkout_order(c, od["id"], pay, user["id"])
                go("orders", last_order=f"已結清：{od['where']}　${od['unpaid_total']}（單號 #{sid}）")
            except s.OpsError as e:
                st.error(str(e))
        st.caption("分開結帳：在要先付的品項按 ＋ 設定份數，再按「結帳勾選品項」，可分次、分付款方式。")

        with st.expander("🧮 均分試算（只算金額，不入帳）"):
            n = stepper(f"split_n_{od['id']}", 1, 30, 2)
            st.write("　".join(f"第{i + 1}位 ${v}" for i, v in enumerate(orders.split_evenly(od["unpaid_total"], n))))
            st.caption("要各自付款，請用上面的分開結帳依品項記帳。")

        if s.can(user, "void") and st.button("取消整張訂單", use_container_width=True):
            try:
                orders.cancel_order(c, od["id"])
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
    finally:
        c.close()


def orders_page():
    top_bar()
    st.header("🧾 訂單 / 結帳")
    if msg := st.session_state.pop("last_order", None):
        st.success(msg)
    _orders_board()


# ───────────────────────── 設定（老闆）：桌位、菜單 ─────────────────────────
def settings_page():
    top_bar()
    st.header("⚙️ 設定")
    t1, t2 = st.tabs(["桌位", "菜單"])
    with t1:
        tables = orders.list_tables(conn)
        cur = {f: sum(1 for t in tables if t["floor"] == f) for f in (1, 2)}
        st.caption("每層桌子的數量。桌號自動編為 101、102…（1 樓）、201、202…（2 樓），每桌 4 人。")
        n1 = st.number_input("1 樓桌數", 0, 99, cur[1] if tables else orders.DEFAULT_FLOORS[1])
        n2 = st.number_input("2 樓桌數", 0, 99, cur[2] if tables else orders.DEFAULT_FLOORS[2])
        seats = st.number_input("每桌座位數", 1, 20, tables[0]["seats"] if tables else orders.DEFAULT_SEATS)
        if st.button("儲存桌位", type="primary", use_container_width=True):
            try:
                n = orders.setup_tables(conn, {1: n1, 2: n2}, seats=seats)
                st.success(f"已設定 {n} 桌")
            except s.OpsError as e:
                st.error(str(e))
    with t2:
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
        for it in (i for i in s.list_items(conn, active_only=False) if i["type"] == "meal"):
            with st.container(border=True):
                st.markdown(f"**{it['name']}**" + ("" if it["active"] else "（停售）"))
                new_price = st.number_input("售價", 0, value=it["price"], step=5, key=f"mp{it['id']}")
                if new_price != it["price"]:
                    s.update_item(conn, it["id"], price=new_price)
                    st.rerun()
                if st.button("恢復販售" if not it["active"] else "停售", key=f"ma{it['id']}",
                             use_container_width=True):
                    s.update_item(conn, it["id"], active=0 if it["active"] else 1)
                    st.rerun()


# ───────────────────────── 導覽 ─────────────────────────
if "user" not in st.session_state:
    login_view(conn, "🍽️ 餐點點餐系統", allow_device=True)
else:
    user = st.session_state.user
    orders.ensure_default_tables(conn)   # 第一次使用：1 樓 4 桌、2 樓 4 桌，每桌 4 人
    # 依帳號角色只註冊能進的頁面：廚房帳號只有「廚房、點餐」，網址手動輸入其他頁也進不去
    ALL_PAGES = {
        "floor": (floor_page, "外場", "🗺️"), "order": (order_page, "點餐", "🍽️"),
        "kitchen": (kitchen_page, "廚房", "👨‍🍳"), "orders": (orders_page, "結帳", "🧾"),
        "settings": (settings_page, "設定", "⚙️"),
    }
    allowed = s.meal_pages(user)
    PAGES = {k: st.Page(ALL_PAGES[k][0], title=ALL_PAGES[k][1], icon=ALL_PAGES[k][2],
                        url_path=k, default=(i == 0)) for i, k in enumerate(allowed)}
    try:   # 新版 Streamlit：隱藏預設導覽，改用每頁頂端的大按鈕列（手機、平板更好點）
        nav = st.navigation(list(PAGES.values()), position="hidden")
        USE_TOPBAR = True
    except TypeError:
        nav = st.navigation(list(PAGES.values()))
        USE_TOPBAR = False
    target = st.session_state.pop("go_to", None)
    if target:   # 目標頁不在權限內（例如廚房帳號送單後想回外場）就回到自己的首頁
        st.switch_page(PAGES.get(target) or PAGES[allowed[0]])
    nav.run()
    sidebar_user(user)
