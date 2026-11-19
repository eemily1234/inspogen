"""餐點點餐系統（獨立 App，手機／平板優先、觸控操作）。

頁面：外場總控台（叫號看板＋桌位圖＋接單＋點餐＋座位＋結帳，首頁）→ 廚房 → 吧台（飲料）→ 設定（老闆：桌位、菜單・套餐、熟客）；點餐頁給廚房帳號用
啟動：streamlit run meal_app.py --server.port 8502
與 ops_app.py（營運後台）共用同一個資料庫與使用者（OPS_DB），結帳的收入會自動進帳務報表。
"""
import html
import io
import math
import struct
import wave
from datetime import date
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
.tcard{border:3px solid;border-radius:16px;padding:.5rem .6rem;min-height:14.5rem;background:rgba(127,127,127,.06);margin-bottom:.35rem}
.tc-sel{box-shadow:0 0 0 4px rgba(47,125,225,.45)}
.tc-head{display:flex;flex-wrap:wrap;gap:.2rem .5rem;align-items:center}
.tc-head svg{flex:0 0 auto}
.tc-sub{font-size:.85rem;opacity:.8}
.tc-dishes{list-style:none;margin:.35rem 0 0;padding:0;font-size:.95rem;line-height:1.55}
.tc-more{opacity:.7}
.tc-empty{opacity:.65;margin-top:.6rem;font-size:.95rem}
.prep{display:flex;flex-wrap:wrap;gap:.5rem;margin:.3rem 0 .6rem}
.prep .pi{border:3px solid #f08c00;border-radius:14px;padding:.35rem .8rem;min-width:7.5rem;background:rgba(240,140,0,.08)}
.prep .pi b{font-size:2rem;display:block;line-height:1.1}
.prep .pi small{opacity:.75}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(6.6rem,1fr));gap:.45rem;margin:.4rem 0 .5rem}
.kpi div{border:2px solid rgba(127,127,127,.3);border-radius:14px;padding:.35rem .6rem;background:rgba(127,127,127,.05)}
.kpi span{display:block;font-size:.82rem;opacity:.75}
.kpi b{font-size:1.45rem;font-variant-numeric:tabular-nums}
[data-testid="stPopover"] button{min-height:3.2rem;font-size:1.02rem;justify-content:flex-start}
.qboard{display:grid;grid-template-columns:repeat(auto-fit,minmax(10.5rem,1fr));gap:.5rem;margin:.4rem 0 .7rem}
.qcol{border:3px solid;border-radius:16px;padding:.4rem .5rem;background:rgba(127,127,127,.05)}
.qcol h4{margin:0 0 .3rem;font-size:1rem;padding:0}
.qc-cooking{border-color:#f08c00}.qc-ready{border-color:#2f7de1}.qc-dining{border-color:#c9a400}
.qchips{display:flex;flex-wrap:wrap;gap:.35rem}
.qchip{border-radius:12px;padding:.25rem .55rem;background:rgba(127,127,127,.12);line-height:1.15}
.qchip b{display:block;font-size:1.5rem;font-variant-numeric:tabular-nums}
.qchip span{font-size:.8rem;opacity:.85}
.qchip i{display:block;font-style:normal;font-size:.75rem;color:#2e9e5b;font-weight:700}
.qc-ready .qchip{background:rgba(47,125,225,.16)}
.qempty{opacity:.5;padding:.2rem}
.tc-paid{color:#2e9e5b;font-weight:700}
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


# ───────────────────────── 鈴聲與彈跳視窗 ─────────────────────────
@st.cache_data
def bell_wav():
    """兩聲「叮—咚」的提示音（程式產生，不需要外部音效檔）。"""
    rate, out, frames = 22050, io.BytesIO(), bytearray()
    for i in range(int(rate * 1.3)):
        t, v = i / rate, 0.0
        for f0, start in ((1318.5, 0.0), (987.8, 0.35)):
            if t >= start:
                v += 0.45 * math.exp(-4 * (t - start)) * math.sin(2 * math.pi * f0 * (t - start))
        frames += struct.pack("<h", int(max(-1.0, min(1.0, v)) * 32000))
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(bytes(frames))
    return out.getvalue()


def ring():
    """響一次鈴（只在剛收到通知時響，按視窗裡的按鈕不會重複響）。"""
    if st.session_state.pop("ring_pending", False):
        st.audio(bell_wav(), format="audio/wav", autoplay=True)


def test_bell_button(key):
    """手動測試鈴聲；平板第一次使用時按一下，也能讓瀏覽器允許之後自動響鈴。"""
    if st.button("🔔 測試鈴聲", key=key):
        st.audio(bell_wav(), format="audio/wav", autoplay=True)


def hhmm(ts):
    return ts[11:16] if ts else ""


KIND_ICON = {"dine_in": "🍽️", "takeout": "🥡", "uber": "🛵"}
STATION_ICON = {"kitchen": "🍳", "bar": "🍹"}


# ───────────────────────── 外場總控台 ─────────────────────────
STAGE_COLOR = {"empty": "#2e9e5b", "new": "#e5484d", "preparing": "#f08c00", "ready": "#2f7de1", "dining": "#c9a400"}
# 椅子位置：一邊兩人（上排 1、2，下排 3、4）；超過 4 人時加在兩側
CHAIRS = [(42, 11), (78, 11), (42, 79), (78, 79), (12, 45), (108, 45), (12, 20), (108, 70)]


def table_svg(t):
    """一張桌子的圖示：桌面顏色＝流程階段；椅子一邊兩個，實心＝有人坐，椅子上標座位號碼。"""
    col = STAGE_COLOR[t["stage"]]
    guests = t.get("guests") or 0
    n = min(8, max(t["seats"], guests))
    chairs = ""
    for i, (x, y) in enumerate(CHAIRS[:n]):
        filled = i < guests
        chairs += (f"<circle cx='{x}' cy='{y}' r='10' fill='{col if filled else 'none'}' stroke='{col}' stroke-width='2.5'/>"
                   f"<text x='{x}' y='{y + 4}' text-anchor='middle' font-size='11' font-weight='700' "
                   f"fill='{'#fff' if filled else col}'>{i + 1}</text>")
    return (f"<svg viewBox='0 0 120 90' width='112' height='84' role='img' aria-label='桌 {html.escape(t['name'])}'>"
            f"<rect x='24' y='25' width='72' height='40' rx='9' fill='{col}'/>{chairs}"
            f"<text x='60' y='51' text-anchor='middle' font-size='17' font-weight='800' fill='#fff'>"
            f"{html.escape(t['name'])}</text></svg>")


def table_card_html(t, selected=False):
    stage = t["stage"]
    col = STAGE_COLOR[stage]
    if stage == "empty":
        sub = f"空桌・{t['seats']} 人桌（一邊兩人）"
        body = "<div class='tc-empty'>點下方「開桌點餐」</div>"
    else:
        paid = "　<span class='tc-paid'>💵 已結帳</span>" if t.get("paid_all") else ""
        sub = (f"<b>{t.get('queue') or ''}</b>　🕒{hhmm(t.get('opened_at'))}・{t['guests'] or '-'}/{t['seats']} 人"
               f"・${t['unpaid_total']}{paid}")
        items = []
        for d in t["dishes"][:5]:
            seat = f"<small>座{d['seat_no']}</small> " if d.get("seat_no") else ""
            combo = f"<small>（{html.escape('＋'.join(d['components']))}）</small>" if d.get("components") else ""
            items.append(f"<li>{ICON[d['status']]} {seat}{d['qty']}× {html.escape(d['name'])}{combo}</li>")
        if len(t["dishes"]) > 5:
            items.append(f"<li class='tc-more'>…還有 {len(t['dishes']) - 5} 項</li>")
        body = "<ul class='tc-dishes'>" + "".join(items) + "</ul>"
    return (f"<div class='tcard{' tc-sel' if selected else ''}' style='border-color:{col}'>"
            f"<div class='tc-head'>{table_svg(t)}<div><b style='color:{col}'>{orders.STAGE_LABEL[stage]}</b>"
            f"<br><span class='tc-sub'>{sub}</span></div></div>{body}</div>")


def queue_board_html(board):
    """叫號看板：所有單（內用、外帶、Uber）的單號，依狀態分三區。"""
    cols = []
    for key, label in orders.QUEUE_GROUPS:
        chips = "".join(
            f"<div class='qchip'><b>{x['queue']}</b><span>{KIND_ICON[x['kind']]} {html.escape(x['where'])}</span>"
            + ("<i>已結帳</i>" if x["paid_all"] else "") + "</div>" for x in board[key]) or "<div class='qempty'>—</div>"
        cols.append(f"<div class='qcol qc-{key}'><h4>{label}（{len(board[key])}）</h4><div class='qchips'>{chips}</div></div>")
    return "<div class='qboard'>" + "".join(cols) + "</div>"


def calls_panel(c):
    """廚房／吧台呼叫外場：最上方一直顯示到有人按「收到」。"""
    user = st.session_state.user
    for call in orders.open_calls(c):
        where = f"（{call['table_no']}）" if call["table_no"] else ""
        st.error(f"🔔 **呼叫外場：{call['message']}**{where}　🕒{hhmm(call['created_at'])}"
                 f"・{orders.wait_minutes(call['created_at'])} 分鐘前")
        if st.button("✅ 收到，馬上過去", key=f"ack{call['id']}", type="primary", use_container_width=True):
            orders.ack_call(c, call["id"], user["id"])
            st.rerun()


@st.dialog("🔔 廚房／吧台呼叫外場")
def floor_alert_dialog():
    ring()
    c = db.connect()
    try:
        calls = orders.open_calls(c)
        if not calls:
            st.write("已經有人回應了。")
        for call in calls:
            where = f"（{call['table_no']}）" if call["table_no"] else ""
            st.markdown(f"## {call['message']}{where}")
            st.caption(f"🕒 {hhmm(call['created_at'])} 呼叫")
            if st.button("✅ 收到，馬上過去", key=f"dack{call['id']}", type="primary", use_container_width=True):
                orders.ack_call(c, call["id"], st.session_state.user["id"])
                st.rerun()
        if st.button("稍後處理", key="dlater", use_container_width=True):
            st.rerun()
    finally:
        c.close()


def _select(kind, key):
    st.session_state["sel"] = {"kind": kind, "key": key}


@st.fragment(run_every=5)
def _floor_board():
    c = db.connect()   # fragment 會自動重跑，使用獨立連線
    try:
        user = st.session_state.user
        # 廚房／吧台呼叫：新的一筆 → 響鈴＋彈跳視窗（整頁重整後由 floor_page 打開）
        ids = {x["id"] for x in orders.open_calls(c)}
        seen = st.session_state.get("calls_seen")
        st.session_state["calls_seen"] = ids | (seen or set())
        if seen is not None and ids - seen:
            st.session_state["floor_alert"] = True
            st.session_state["ring_pending"] = True
            st.rerun()
        calls_panel(c)

        ov = orders.floor_overview(c)
        ready = sum(t["to_serve"] for t in ov["tables"]) + sum(t["to_serve"] for t in ov["takeouts"])
        prev = st.session_state.get("floor_ready_seen")
        if prev is not None and ready > prev:
            st.toast("🛎️ 有餐點完成，可以上菜了！")
        st.session_state["floor_ready_seen"] = ready

        kitchen_banner(c)
        uber_alert(c)
        st.markdown(queue_board_html(orders.queue_board(c)), unsafe_allow_html=True)

        if not ov["tables"]:
            st.info("還沒有設定桌位。")
            if s.can(user, "report"):
                f1 = st.number_input("1 樓桌數", 0, 99, orders.DEFAULT_FLOORS[1])
                f2 = st.number_input("2 樓桌數", 0, 99, orders.DEFAULT_FLOORS[2])
                if st.button("建立桌位（每桌 4 人）", type="primary", use_container_width=True):
                    try:
                        orders.setup_tables(c, {1: f1, 2: f2}, seats=orders.DEFAULT_SEATS)
                        st.rerun()
                    except s.OpsError as e:
                        st.error(str(e))
            else:
                st.caption("請老闆到「設定」頁建立桌位。")

        sel = st.session_state.get("sel") or {}
        names = {0: "其他", 1: "1 樓", 2: "2 樓"}
        floors = ov["floors"]
        labels = [f"{names.get(f, f'{f} 樓')}（{sum(1 for t in ov['tables'] if t['floor'] == f and t['stage'] != 'empty')}/"
                  f"{sum(1 for t in ov['tables'] if t['floor'] == f)}）" for f in floors]
        tabs = st.tabs(labels + [f"📥 接單・外帶（{len(ov['takeouts'])}）"])
        for tab, f in zip(tabs, floors):
            with tab:
                ts = [t for t in ov["tables"] if t["floor"] == f]
                for i in range(0, len(ts), 2):
                    row = st.columns(2)
                    for col, t in zip(row, ts[i:i + 2]):
                        chosen = sel.get("kind") == "table" and sel.get("key") == t["name"]
                        col.markdown(table_card_html(t, chosen), unsafe_allow_html=True)
                        label = "👉 處理中" if chosen else ("🍽️ 開桌點餐" if t["stage"] == "empty" else "點餐・結帳")
                        if col.button(label, key=f"pick_{t['name']}", use_container_width=True,
                                      type="primary" if chosen else "secondary"):
                            _select("table", t["name"])
                            st.rerun()   # 整頁重整，打開下方的處理區
        with tabs[-1]:
            if st.button("＋ 新外帶單", key="new_takeout", type="primary", use_container_width=True):
                _select("new", "takeout")
                st.rerun()
            if not ov["takeouts"]:
                st.caption("目前沒有外帶或 Uber 訂單")
            for tk in ov["takeouts"]:
                icon = "🛵 " if tk["where"].startswith("Uber") else "🥡 "
                dishes = "、".join(f"{ICON[d['status']]}{d['qty']}×{d['name']}" for d in tk["dishes"])
                chosen = sel.get("kind") == "order" and sel.get("key") == tk["order_id"]
                paid = "　💵已結帳" if tk.get("paid_all") else ""
                with st.container(border=True):
                    st.markdown(f"**{tk.get('queue') or ''}**　{TILE[tk['stage']]} {icon}**{tk['where']}**　"
                                f"{orders.STAGE_LABEL[tk['stage']]}　🕒{hhmm(tk['opened_at'])}・⏱{tk['wait']} 分"
                                f"　${tk['unpaid_total']}{paid}  \n{dishes}")
                    if st.button("👉 處理中" if chosen else "處理這張單", key=f"pick_o{tk['order_id']}",
                                 type="primary" if chosen else "secondary", use_container_width=True):
                        _select("order", tk["order_id"])
                        st.rerun()
    finally:
        c.close()


def _line_label(ln):
    seat = f"座{ln['seat_no']}・" if ln.get("seat_no") else ""
    combo = f"（{'＋'.join(c['name'] for c in ln['components'])}）" if ln.get("components") else ""
    return f"{ICON[ln['status']]} {seat}{ln['qty']}× {ln['name']}{combo}　{orders.LABEL[ln['status']]}"


def _dish_buttons(od):
    """餐點區：依下單輪次分組並標時間；每道菜是一顆按鍵，點開可上菜／取消（套餐會列出裡面的品項）。"""
    rounds = {}
    for ln in od["lines"]:
        rounds.setdefault(ln["round_no"], []).append(ln)
    for rn in sorted(rounds):
        lines = rounds[rn]
        first = min(l["created_at"] for l in lines)
        title = "第 1 輪" if rn == 1 else f"加點・第 {rn} 輪"
        st.markdown(f"**{title}**　🕒 {hhmm(first)}（{orders.wait_minutes(first)} 分鐘前）")
        cols = st.columns(2)
        for i, ln in enumerate(lines):
            with cols[i % 2].popover(_line_label(ln), use_container_width=True):
                st.markdown(f"**{ln['qty']} × {ln['name']}**　${ln['qty'] * ln['unit_price']}"
                            + (f"　座 {ln['seat_no']}" if ln.get("seat_no") else "　共用"))
                for comp in ln["components"]:
                    st.markdown(f"　{ICON[comp['status']]} {STATION_ICON.get(comp['station'], '')} "
                                f"{comp['qty']}× {comp['name']}　{orders.LABEL[comp['status']]}")
                if ln["note"]:
                    st.markdown(f":red[➜ {ln['note']}]")
                if ln["origin"]:
                    st.caption(ln["origin"])
                if ln["paid_qty"]:
                    st.caption(f"已付 {ln['paid_qty']}/{ln['qty']}")
                ready = (ln["status"] == "ready" if not ln["components"]
                         else any(c["status"] == "ready" for c in ln["components"]))
                all_new = (ln["status"] == "new" if not ln["components"]
                           else all(c["status"] in ("new", "cancelled") for c in ln["components"]))
                if ready and st.button("🛎️ 上菜", key=f"sv{ln['id']}", type="primary", use_container_width=True):
                    orders.serve_line(conn, ln["id"])
                    st.rerun()
                if ln["status"] != "cancelled" and all_new and not ln["paid_qty"] and st.button(
                        "🗑 取消這道", key=f"cl{ln['id']}", use_container_width=True):
                    try:
                        orders.cancel_line(conn, ln["id"])
                    except s.OpsError as e:
                        st.error(str(e))
                    st.rerun()
                if not ready and not all_new:
                    st.caption({"served": "已上菜", "cancelled": "已取消"}.get(ln["status"], "製作中，無法取消"))
    if od["to_serve"] and st.button(f"🛎️ 全部上菜（{od['to_serve']} 道）", type="primary",
                                    use_container_width=True, key=f"serveall{od['id']}"):
        orders.serve_all_ready(conn, od["id"])
        st.rerun()


def checkout_section(od):
    """結帳：整單結清、分開結帳（按 ＋ 設定份數）、均分試算。結清後客人還在，按上方「客人離開」才完成。"""
    user = st.session_state.user
    picks, pick_total = [], 0
    for ln in od["lines"]:
        if ln["remaining"] <= 0:
            continue
        c1, c2 = st.columns([3, 2])
        c1.markdown(f"{ln['qty']}× {ln['name']}  \n${ln['unit_price']}／份，未付 {ln['remaining']} 份")
        with c2:
            # key 帶上剩餘份數：付款後上限改變，數字自動歸零
            q = stepper(f"pq{ln['id']}_{ln['remaining']}", 0, ln["remaining"], 0)
        if q:
            picks.append((ln["id"], q))
            pick_total += q * ln["unit_price"]
    st.markdown(kpi_html([("合計", f"${od['total']}"), ("已付", f"${od['paid_total']}"),
                          ("待付", f"${od['unpaid_total']}")]), unsafe_allow_html=True)
    if od["paid_all"]:
        st.success("已全部結清。客人用完餐離開時，按上方「👋 客人離開・完成」。")
        return
    st.markdown("**付款方式**")
    pay = choice_buttons(f"pay_{od['id']}", PAY, cols=2)

    def done(msg):
        st.session_state["last_order"] = msg
        st.rerun()

    if st.button(f"💵 全部結清　${od['unpaid_total']}", type="primary", use_container_width=True,
                 key=f"payall{od['id']}"):
        try:
            sid = orders.checkout_order(conn, od["id"], pay, user["id"])
            done(f"已結清：{od['where']}　${od['unpaid_total']}（單號 #{sid}）。客人離開時記得按「完成」")
        except s.OpsError as e:
            st.error(str(e))
    if st.button(f"💵 只結上面選的份數　${pick_total}", disabled=not picks, use_container_width=True,
                 key=f"paysome{od['id']}"):
        try:
            sid = orders.checkout_lines(conn, od["id"], picks, pay, user["id"])
            done(f"已結帳 ${pick_total}：{od['where']}（單號 #{sid}）")
        except s.OpsError as e:
            st.error(str(e))
    with st.expander("🧮 均分試算（只算金額，不入帳）"):
        n = stepper(f"split_n_{od['id']}", 1, 30, 2)
        st.write("　".join(f"第{i + 1}位 ${v}" for i, v in enumerate(orders.split_evenly(od["unpaid_total"], n))))
    if s.can(user, "void") and st.button("取消整張訂單", use_container_width=True, key=f"cancel{od['id']}"):
        try:
            orders.cancel_order(conn, od["id"])
            st.session_state.pop("sel", None)
            done(f"已取消：{od['where']}")
        except s.OpsError as e:
            st.error(str(e))


def seats_section(od, seats):
    """座位：誰坐哪裡（可選熟客，自動帶入他不吃的東西）、不吃什麼。廚房與吧台都會看到。"""
    current = {x["seat_no"]: x for x in od["seats"]}
    customers = orders.list_customers(conn)
    cmap = {c["id"]: c for c in customers}
    st.caption("一邊兩人：座 1、2 在上排，座 3、4 在下排。熟客請先在「設定 → 熟客名單」建立。")
    for n in range(1, seats + 1):
        cur = current.get(n, {})
        with st.container(border=True):
            st.markdown(f"**座 {n}**" + (f"　{cur.get('guest_name') or ''}" if cur else "　（未登記）")
                        + (f"　:red[🚫 {cur['avoid']}]" if cur.get("avoid") else ""))
            options = [0] + [c["id"] for c in customers]
            idx = options.index(cur["customer_id"]) if cur.get("customer_id") in options else 0
            cid = st.selectbox("熟客", options, index=idx, key=f"seat_c_{od['id']}_{n}",
                               format_func=lambda x: "（不是熟客／新客人）" if x == 0 else
                               f"⭐ {cmap[x]['name']}" + (f"（不吃：{cmap[x]['avoid']}）" if cmap[x]["avoid"] else ""))
            name = st.text_input("名字或稱呼", value=cur.get("guest_name") or "", key=f"seat_n_{od['id']}_{n}",
                                 placeholder="例：陳先生、小朋友")
            avoid = st.text_input("不吃什麼／過敏", value=(cur.get("avoid") or "") if not cur.get("customer_id") else "",
                                  key=f"seat_a_{od['id']}_{n}", placeholder="例：不吃牛、蝦過敏")
            if st.button(f"儲存座 {n}", key=f"seat_s_{od['id']}_{n}", use_container_width=True):
                try:
                    orders.set_seat(conn, od["id"], n, name, cid or None, avoid)
                    st.session_state["last_order"] = f"已更新座 {n}"
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))


def notify_kitchen_section(od):
    st.caption("送出後，對方會響鈴並跳出視窗。")
    target = choice_buttons(f"nk_to_{od['id']}", ["kitchen", "bar"], {"kitchen": "🍳 通知廚房", "bar": "🍹 通知吧台"})
    cols = st.columns(2)
    for i, msg in enumerate(orders.KITCHEN_MESSAGES):
        if cols[i % 2].button(msg, key=f"nk{od['id']}_{i}", use_container_width=True):
            orders.call_kitchen(conn, msg, od["where"], st.session_state.user["id"], station=target)
            st.session_state["last_order"] = f"已通知{orders.STATIONS[target]}：{msg}（{od['where']}）"
            st.rerun()
    custom = st.text_input("其他內容", key=f"nkc{od['id']}", placeholder="例：101 小朋友的餐先出")
    if st.button("送出通知", key=f"nks{od['id']}", use_container_width=True, disabled=not custom.strip()):
        try:
            orders.call_kitchen(conn, custom, od["where"], st.session_state.user["id"], station=target)
            st.session_state["last_order"] = f"已通知{orders.STATIONS[target]}：{custom.strip()}（{od['where']}）"
            st.session_state.pop(f"nkc{od['id']}", None)
            st.rerun()
        except s.OpsError as e:
            st.error(str(e))


def move_section(t, ov):
    others = [x for x in ov["tables"] if x["name"] != t["name"] and x["floor"] != 0]
    st.caption("點選要移到的桌子：🟩空桌＝換桌，有客人的桌＝併桌")
    target = st.session_state.get("mv_target")
    if target not in [x["name"] for x in others]:
        target = None
    table_grid(others, lambda n: st.session_state.__setitem__("mv_target", n), target, key="mv")
    if target and st.button(f"確認：桌 {t['name']} → 桌 {target}", key="ws_move", type="primary",
                            use_container_width=True):
        try:
            r = orders.merge_tables(conn, t["name"], target)
            _select("table", target)
            st.session_state.pop("mv_target", None)
            st.session_state["last_order"] = ("已換桌" if r["action"] == "move" else "已併桌") + f"：{t['name']} → {target}"
            st.rerun()
        except s.OpsError as e:
            st.error(str(e))


def _close_button(od):
    """客人離開・完成：要結清、餐點都上完才能按。"""
    if od["can_close"]:
        if st.button("👋 客人離開・完成", key=f"close{od['id']}", type="primary", use_container_width=True):
            try:
                orders.close_order(conn, od["id"])
                st.session_state.pop("sel", None)
                st.session_state["last_order"] = f"{od['where']}（{od['queue']}）已完成，桌子已清空"
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
    else:
        need = []
        if od["unpaid_total"] > 0:
            need.append(f"結清 ${od['unpaid_total']}")
        if od["unfinished"] or od["to_serve"]:
            need.append(f"上完 {od['unfinished'] + od['to_serve']} 道")
        st.caption("👋 客人離開前需要：" + "、".join(need))


def _workspace():
    """選到的桌子或訂單：在同一頁處理餐點、加點、座位、結帳、通知、換桌。
    放在自動更新區外面，打字、選需求時畫面不會被重整打斷。"""
    sel = st.session_state.get("sel")
    if not sel:
        return
    user = st.session_state.user
    ov = orders.floor_overview(conn)
    ods = {o["id"]: o for o in orders.open_orders(conn)}

    def header(title, sub=""):
        h1, h2 = st.columns([3, 1])
        h1.markdown(f"## {title}" + (f"  \n{sub}" if sub else ""))
        if h2.button("✕ 收起", key="ws_close", use_container_width=True):
            st.session_state.pop("sel", None)
            st.rerun()

    with st.container(border=True):
        if sel["kind"] == "new":
            header("🥡 新外帶單")
            order_form(user, "takeout", None, after=None, on_placed=lambda r: _select("order", r["order_id"]))
            return
        if sel["kind"] == "table":
            t = next((x for x in ov["tables"] if x["name"] == sel["key"]), None)
            if not t:
                st.session_state.pop("sel", None)
                return
            if t["stage"] == "empty":
                header(f"{TILE['empty']} 桌 {t['name']}　開桌點餐", f"{t['seats']} 人桌・一邊兩人")
                order_form(user, "dine_in", t["name"], guests_seats=t["seats"], after=None, seat_count=t["seats"])
                return
            od = ods.get(t["order_id"])
        else:
            t = None
            od = ods.get(sel["key"])
        if not od:
            st.session_state.pop("sel", None)
            st.rerun()
        stage = orders._stage_of(od)
        paid = "・💵 已結帳" if od["paid_all"] else f"・待付 ${od['unpaid_total']}"
        header(f"{od['queue']}　{TILE[stage]} {od['where']}　{orders.STAGE_LABEL[stage]}",
               f"🕒 {hhmm(od['created_at'])} 開單・⏱ {orders.wait_minutes(od['created_at'])} 分鐘"
               f"{('・' + str(od['guests']) + ' 人') if od.get('guests') else ''}{paid}")
        _close_button(od)
        if od["note"]:
            st.markdown(f":red[⚠️ {od['note']}]")
        names = ["🍽️ 餐點", "💵 結帳", "📣 通知"]
        if t:
            names[1:1] = ["➕ 加點", "👥 座位"]
            names.append("🔀 換桌")
        tabs = dict(zip(names, st.tabs(names)))
        with tabs["🍽️ 餐點"]:
            _dish_buttons(od)
        if t:
            with tabs["➕ 加點"]:
                order_form(user, "dine_in", t["name"], after=None, seat_count=t["seats"],
                           seat_names={x["seat_no"]: x["guest_name"] for x in od["seats"]})
            with tabs["👥 座位"]:
                seats_section(od, max(t["seats"], od.get("guests") or 0))
        with tabs["💵 結帳"]:
            checkout_section(od)
        with tabs["📣 通知"]:
            notify_kitchen_section(od)
        if t:
            with tabs["🔀 換桌"]:
                move_section(t, ov)


def floor_page():
    top_bar()
    st.header("🗺️ 外場總控台")
    if st.session_state.pop("floor_alert", False):
        floor_alert_dialog()
    if msg := st.session_state.pop("last_order", None):
        st.success(msg)
    _floor_board()
    _workspace()
    test_bell_button("bell_floor")


# ───────────────────────── 點餐表單（外場總控台與點餐頁共用） ─────────────────────────
def kpi_html(items):
    return "<div class='kpi'>" + "".join(
        f"<div><span>{html.escape(k)}</span><b>{html.escape(v)}</b></div>" for k, v in items) + "</div>"


def order_form(user, otype, table, guests_seats=None, after="floor", on_placed=None, seat_count=None,
               seat_names=None):
    """點餐表單。每桌（或外帶）有自己的購物車，換桌不會混在一起。
    seat_count：可指定每道菜是哪個座位點的；guests_seats：開桌時選人數並登記誰來用餐。
    after=None：送出後留在原頁；否則送出後換到該頁。on_placed(r)：送出成功時呼叫。"""
    accepting = orders.kitchen_status(conn)["accepting"]
    if not accepting:
        kitchen_banner(conn)
    ckey = f"ocart_{table or 'takeout'}"
    cart = st.session_state.setdefault(ckey, [])
    gkey = f"guests_{table}"
    seat_names = dict(seat_names or {})
    seats_info = {}
    if guests_seats:
        st.markdown("**用餐人數**")
        stepper(gkey, 1, guests_seats, 2)
        customers = orders.list_customers(conn)
        cmap = {c["id"]: c for c in customers}
        with st.expander("👥 誰來用餐、不吃什麼（選填，廚房會看到）"):
            for n in range(1, (seat_count or guests_seats) + 1):
                c1, c2, c3 = st.columns([2, 2, 2])
                cid = c1.selectbox(f"座 {n} 熟客", [0] + list(cmap), key=f"nseat_c_{table}_{n}",
                                   format_func=lambda x: "—" if x == 0 else f"⭐ {cmap[x]['name']}")
                name = c2.text_input(f"座 {n} 名字", key=f"nseat_n_{table}_{n}", placeholder="稱呼")
                avoid = c3.text_input(f"座 {n} 不吃", key=f"nseat_a_{table}_{n}", placeholder="例：不吃牛")
                if cid or name.strip() or avoid.strip():
                    seats_info[n] = {"customer_id": cid or None, "guest_name": name, "avoid": avoid}
                    seat_names[n] = name.strip() or (cmap[cid]["name"] if cid else "")

    meals = [i for i in s.list_items(conn) if i["type"] == "meal"]
    combos = {i["id"]: orders.combo_components(conn, i["id"]) for i in meals}
    if not meals:
        st.info("還沒有餐點，請老闆到「設定」新增。")
    st.markdown("**點一下餐點加入**")
    for i in range(0, len(meals), 3):
        row = st.columns(3)
        for col, it in zip(row, meals[i:i + 3]):
            tag = " 🍱" if combos[it["id"]] else (" 🍹" if it["station"] == "bar" else "")
            if col.button(f"{it['name']}{tag}  \n${it['price']}", key=f"om_{ckey}_{it['id']}", use_container_width=True):
                cart.append({"uid": uuid4().hex[:8], "item_id": it["id"]})
                st.rerun()

    names = {i["id"]: i for i in meals}
    total = 0
    for e in list(cart):
        it = names.get(e["item_id"])
        if not it:
            cart.remove(e)
            continue
        with st.container(border=True):
            inside = f"　<small>套餐：{'＋'.join(c['name'] for c in combos[it['id']])}</small>" if combos[it["id"]] else ""
            st.markdown(f"**{it['name']}**　${it['price']}{inside}", unsafe_allow_html=True)
            e["qty"] = stepper(f"oq{e['uid']}", 1, 99, 1)
            if seat_count and otype == "dine_in":
                labels = {0: "共用"}
                labels.update({n: f"座{n}" + (f" {seat_names[n]}" if seat_names.get(n) else "")
                               for n in range(1, seat_count + 1)})
                st.caption("誰點的？")
                e["seat"] = choice_buttons(f"os{e['uid']}", list(labels), labels, cols=min(5, len(labels))) or None
            e["quick"] = quick_pick("特殊需求", QUICK_NOTES, f"on{e['uid']}")
            e["text"] = st.text_input("其他需求", key=f"ot_{e['uid']}", placeholder="例：醬分開、飯少一點")
            if st.button("🗑 移除這道", key=f"od{e['uid']}", use_container_width=True):
                cart.remove(e)
                st.rerun()
            total += it["price"] * e["qty"]
    if not cart:
        st.caption("尚未選擇餐點")
        return
    note_key = f"o_note_{ckey}"
    order_note = st.text_input("整單備註（過敏、趕時間…）", key=note_key)
    st.markdown(f"### 本次合計　${total}")
    if st.button("📨 送出", type="primary", use_container_width=True, disabled=not accepting, key=f"send_{ckey}"):
        lines = [{"item_id": e["item_id"], "qty": e["qty"], "seat": e.get("seat"),
                  "note": "、".join(e.get("quick", []) + ([e["text"].strip()] if e.get("text", "").strip() else []))}
                 for e in cart]
        try:
            r = orders.place_order(conn, otype, lines, table, order_note, user["id"],
                                   guests=st.session_state.get(gkey) if guests_seats else None,
                                   seats=seats_info or None)
        except s.OpsError as e:
            st.error(str(e))
            return
        where = f"桌 {r['table_no']}" if otype == "dine_in" else f"外帶取餐號 #{r['pickup_no']}"
        for e in cart:
            for k in (f"oq{e['uid']}", f"on{e['uid']}", f"ot_{e['uid']}", f"os{e['uid']}"):
                st.session_state.pop(k, None)
        for k in [k for k in st.session_state if str(k).startswith(f"nseat_") and str(k).split("_")[2] == str(table)]:
            st.session_state.pop(k, None)
        for k in (note_key, gkey, ckey):
            st.session_state.pop(k, None)
        msg = (f"已送出（單號 {orders.queue_label(r['queue_no'])}）：{where}"
               + (f"（第 {r['round_no']} 輪加點）" if r["is_addon"] else "") + "，廚房／吧台會響鈴通知")
        if on_placed:
            on_placed(r)
        if after:
            go(after, last_order=msg)
        st.session_state["last_order"] = msg
        st.rerun()


# ───────────────────────── 點餐頁（廚房帳號用） ─────────────────────────
def _pick_order_table(name):
    st.session_state["order_table"] = name
    st.session_state["pick_open"] = False


def order_page():
    user = st.session_state.user
    top_bar()
    st.header("🍽️ 點餐")
    if msg := st.session_state.pop("last_order", None):
        st.success(msg)
    kitchen_banner(conn)
    otype = choice_buttons("order_type", ["dine_in", "takeout"], {"dine_in": "🍽️ 內用", "takeout": "🥡 外帶"})
    if otype == "takeout":
        order_form(user, "takeout", None, after="floor")
        return
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
                table_grid([t for t in ov["tables"] if t["floor"] == f], _pick_order_table, table, key=f"ot{f}")
    if table and info:
        order_form(user, "dine_in", table, guests_seats=info["seats"] if info["stage"] == "empty" else None,
                   after="floor", seat_count=info["seats"],
                   seat_names={x["seat_no"]: x["guest_name"] for x in info.get("seats_info", [])})


# ───────────────────────── 廚房／吧台：接單、備料總表、呼叫外場、看板 ─────────────────────────
MSG_DIR = {"kitchen": "to_kitchen", "bar": "to_bar"}


def _line_md(ln, big=True):
    """一個品項在廚房／吧台看到的樣子：份數、品名、所屬套餐、座位與客人、忌口、需求。"""
    out = f"### {ln['qty']} × {ln['name']}" if big else f"**{ln['qty']} × {ln['name']}**"
    extra = []
    if ln.get("combo"):
        extra.append(f"🍱 {ln['combo']}")
    if ln.get("seat_no"):
        extra.append(f"座{ln['seat_no']}" + (f" {ln['guest']}" if ln.get("guest") else "") + (" ⭐熟客" if ln.get("regular") else ""))
    if extra:
        out += f"  \n{'　'.join(extra)}"
    if ln.get("avoid"):
        out += f"  \n:red[🚫 不吃：{ln['avoid']}]"
    if ln.get("note"):
        out += f"  \n:red[➜ {ln['note']}]"
    return out


@st.dialog("🔔 新訂單／外場通知")
def station_alert_dialog(station, keys):
    ring()
    c = db.connect()
    try:
        keys = {tuple(k) for k in keys}
        tickets = [t for t in orders.kitchen_tickets(c, station) if (t["order_id"], t["round_no"]) in keys]
        msgs = orders.open_calls(c, MSG_DIR[station])
        for t in tickets:
            tag = f"　:red[加點（第 {t['round_no']} 輪）]" if t["is_addon"] else "　新訂單"
            st.markdown(f"### {orders.queue_label(t['queue_no'])}　{t['where']}{tag}")
            st.caption(f"🕒 {hhmm(t['created_at'])} 下單")
            if t["order_note"]:
                st.markdown(f":red[⚠️ {t['order_note']}]")
            for ln in t["lines"]:
                st.markdown(_line_md(ln, big=False))
        for m in msgs:
            st.markdown(f"### 📣 外場通知：{m['message']}" + (f"（{m['table_no']}）" if m["table_no"] else ""))
            st.caption(f"🕒 {hhmm(m['created_at'])}")
        if not tickets and not msgs:
            st.write("已經處理好了。")
        new_ones = [t for t in tickets if t["status"] == "new"]
        if new_ones and st.button(f"✅ 接單・開始製作（{len(new_ones)} 張）", type="primary",
                                  use_container_width=True, key=f"{station}_d_accept"):
            for t in new_ones:
                try:
                    orders.advance_ticket(c, t["order_id"], t["round_no"], station)
                except s.OpsError:
                    pass
            st.rerun()
        if msgs and st.button("✅ 收到外場通知", type="primary", use_container_width=True, key=f"{station}_d_ack"):
            for m in msgs:
                orders.ack_call(c, m["id"], st.session_state.user["id"])
            st.rerun()
        if st.button("稍後處理", use_container_width=True, key=f"{station}_d_later"):
            st.rerun()
    finally:
        c.close()


@st.fragment(run_every=5)
def _station_board(station):
    c = db.connect()
    name = orders.STATIONS[station]
    try:
        user = st.session_state.user
        tickets = orders.kitchen_tickets(c, station)
        msgs = orders.open_calls(c, MSG_DIR[station])
        keys = {(t["order_id"], t["round_no"]) for t in tickets}
        mids = {m["id"] for m in msgs}
        seen_t = st.session_state.get(f"{station}_seen")
        seen_m = st.session_state.get(f"{station}_msg_seen")
        st.session_state[f"{station}_seen"] = keys | (seen_t or set())
        st.session_state[f"{station}_msg_seen"] = mids | (seen_m or set())
        fresh = keys - seen_t if seen_t is not None else set()
        if fresh or (seen_m is not None and mids - seen_m):
            # 外場送單／加點／通知 → 響鈴＋彈跳視窗（整頁重整後由頁面打開）
            st.session_state[f"{station}_alert"] = [list(k) for k in fresh]
            st.session_state["ring_pending"] = True
            st.rerun()

        if station == "kitchen":
            uber_alert(c)
        for m in msgs:
            st.warning(f"📣 **外場通知：{m['message']}**" + (f"（{m['table_no']}）" if m["table_no"] else "")
                       + f"　🕒{hhmm(m['created_at'])}")
            if st.button("✅ 收到", key=f"{station}_ack{m['id']}", use_container_width=True):
                orders.ack_call(c, m["id"], user["id"])
                st.rerun()

        ks = orders.kitchen_status(c)
        if station == "kitchen":
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

        prep = orders.prep_summary(c, station)
        st.markdown("### 📋 目前要準備")
        if prep:
            chips = "".join(
                f"<div class='pi'><b>{p['total']}</b>{html.escape(p['name'])}<br>"
                f"<small>待接單 {p['new_qty']}・製作中 {p['preparing_qty']}</small></div>" for p in prep)
            st.markdown(f"<div class='prep'>{chips}</div>", unsafe_allow_html=True)
        else:
            st.caption("目前沒有要準備的品項")

        with st.container(border=True):
            st.markdown("**🔔 呼叫外場**（外場會響鈴並跳出視窗）")
            cols = st.columns(2)
            for i, msg in enumerate(orders.CALL_MESSAGES):
                if cols[i % 2].button(msg, key=f"{station}_call_{i}", use_container_width=True):
                    orders.call_floor(c, f"{msg}（{name}）", None, user["id"])
                    st.toast(f"已通知外場：{msg}")
                    st.rerun()
            custom = st.text_input("其他內容", key=f"{station}_call_custom", placeholder="例：101 的湯好了")
            if st.button("送出呼叫", key=f"{station}_call_send", use_container_width=True, disabled=not custom.strip()):
                try:
                    orders.call_floor(c, f"{custom.strip()}（{name}）", None, user["id"])
                    st.toast("已通知外場")
                    st.session_state.pop(f"{station}_call_custom", None)
                except s.OpsError as e:
                    st.error(str(e))
                st.rerun()

        st.caption(f"待處理 {len(tickets)} 張　每 5 秒自動更新")
        if not tickets:
            st.success("目前沒有待處理的單 👍")
        for t in tickets:
            with st.container(border=True):
                head = f"**{orders.queue_label(t['queue_no'])}**　{ICON[t['status']]} **{t['where']}**"
                if t["is_addon"]:
                    head += f"　:red[加點（第 {t['round_no']} 輪）]"
                for origin in t["origins"]:
                    head += f"　:orange[{origin}]"
                state = "待接單" if t["status"] == "new" else "已接單・製作中"
                st.markdown(f"{head}　{state}  \n🕒 {hhmm(t['created_at'])} 下單・⏱ {orders.wait_minutes(t['created_at'])} 分鐘")
                if t["order_note"]:
                    st.markdown(f":red[⚠️ 整單備註：{t['order_note']}]")
                for ln in t["lines"]:
                    st.markdown(_line_md(ln))
                label = "✅ 接單・開始製作" if t["status"] == "new" else "🛎️ 完成出餐"
                if st.button(label, key=f"{station}_k{t['order_id']}_{t['round_no']}", use_container_width=True,
                             type="primary" if t["status"] == "new" else "secondary"):
                    try:
                        new_status = orders.advance_ticket(c, t["order_id"], t["round_no"], station)
                        if new_status == "ready" and uber.UberConfig().configured:
                            od = next((x for x in orders.open_orders(c) if x["id"] == t["order_id"]), None)
                            if od and not od["unfinished"]:   # 整張單都做好了才通知 Uber
                                uber.notify_ready(c, uber.UberClient(), t["order_id"])
                    except s.OpsError as e:
                        st.error(str(e))
                    st.rerun()
                if st.button(f"🔔 呼叫外場（{t['where']}）", key=f"{station}_kc{t['order_id']}_{t['round_no']}",
                             use_container_width=True):
                    orders.call_floor(c, f"請到{name}", t["where"], user["id"])
                    st.toast(f"已通知外場：{t['where']}")
                    st.rerun()
    finally:
        c.close()


def _station_page(station, title):
    top_bar()
    st.header(title)
    alert = st.session_state.pop(f"{station}_alert", None)
    if alert is not None:
        station_alert_dialog(station, alert)
    if msg := st.session_state.pop("last_order", None):
        st.success(msg)
    _station_board(station)
    test_bell_button(f"bell_{station}")


def kitchen_page():
    _station_page("kitchen", "👨‍🍳 廚房")


def bar_page():
    _station_page("bar", "🍹 吧台")


# ───────────────────────── 設定（老闆）：桌位、菜單、套餐、熟客 ─────────────────────────
def settings_page():
    top_bar()
    st.header("⚙️ 設定")
    t1, t2, t3 = st.tabs(["桌位", "菜單・套餐", "熟客名單"])
    with t1:
        tables = orders.list_tables(conn)
        cur = {f: sum(1 for t in tables if t["floor"] == f) for f in (1, 2)}
        st.caption("每層桌子的數量。桌號自動編為 101、102…（1 樓）、201、202…（2 樓），每桌 4 人（一邊兩人）。")
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
            name = st.text_input("名稱（餐點、飲料或套餐）")
            price = st.number_input("售價", 0, step=5)
            station = st.radio("由誰製作", list(orders.STATIONS), horizontal=True,
                               format_func=lambda x: f"{STATION_ICON[x]} {orders.STATIONS[x]}")
            cost = st.number_input("單份成本（選填，供毛利參考）", 0, step=5)
            if st.form_submit_button("新增", use_container_width=True):
                try:
                    iid = s.add_item(conn, name, "meal", price, cost)
                    orders.set_station(conn, iid, station)
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))
        st.caption("套餐：先新增一個品項當作套餐（設定套餐價），再在它下面勾選包含哪些品項（例如主餐＋飲料）。"
                   "點了套餐，主餐會送到廚房、飲料另外送到吧台。")
        meals = [i for i in s.list_items(conn, active_only=False) if i["type"] == "meal"]
        plain = [i for i in meals if not orders.combo_components(conn, i["id"])]
        for it in meals:
            comps = orders.combo_components(conn, it["id"])
            with st.container(border=True):
                kind = "🍱 套餐" if comps else f"{STATION_ICON[it['station']]} {orders.STATIONS[it['station']]}"
                st.markdown(f"**{it['name']}**　{kind}" + ("" if it["active"] else "（停售）")
                            + (f"  \n內容：{'＋'.join(c['name'] for c in comps)}" if comps else ""))
                c1, c2 = st.columns(2)
                new_price = c1.number_input("售價", 0, value=it["price"], step=5, key=f"mp{it['id']}")
                if new_price != it["price"]:
                    s.update_item(conn, it["id"], price=new_price)
                    st.rerun()
                if not comps:
                    st_new = c2.radio("由誰製作", list(orders.STATIONS), index=list(orders.STATIONS).index(it["station"]),
                                      horizontal=True, key=f"ms{it['id']}",
                                      format_func=lambda x: f"{STATION_ICON[x]} {orders.STATIONS[x]}")
                    if st_new != it["station"]:
                        orders.set_station(conn, it["id"], st_new)
                        st.rerun()
                with st.expander("🍱 設成套餐／修改套餐內容"):
                    options = [p["id"] for p in plain if p["id"] != it["id"]]
                    pmap = {p["id"]: p for p in plain}
                    chosen = st.multiselect("包含哪些品項（每樣一份）", options,
                                            default=[c["id"] for c in comps if c["id"] in options],
                                            key=f"mc{it['id']}",
                                            format_func=lambda x: f"{STATION_ICON[pmap[x]['station']]} {pmap[x]['name']}")
                    if st.button("儲存套餐內容（全部取消勾選＝變回單點）", key=f"mcs{it['id']}", use_container_width=True):
                        try:
                            orders.set_combo(conn, it["id"], [(x, 1) for x in chosen])
                            st.rerun()
                        except s.OpsError as e:
                            st.error(str(e))
                if st.button("恢復販售" if not it["active"] else "停售", key=f"ma{it['id']}", use_container_width=True):
                    s.update_item(conn, it["id"], active=0 if it["active"] else 1)
                    st.rerun()
    with t3:
        st.caption("熟客的「不吃什麼」會在點餐選到他時自動帶入，廚房和吧台都看得到。")
        with st.form("newcust", clear_on_submit=True):
            cname = st.text_input("名字")
            phone = st.text_input("電話（選填）")
            avoid = st.text_input("不吃什麼／過敏", placeholder="例：不吃香菜、蝦過敏")
            note = st.text_input("其他偏好（選填）", placeholder="例：飲料少冰")
            if st.form_submit_button("加入熟客名單", use_container_width=True):
                try:
                    orders.add_customer(conn, cname, phone, avoid, note)
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))
        for cu in orders.list_customers(conn):
            with st.container(border=True):
                st.markdown(f"**⭐ {cu['name']}**" + (f"　{cu['phone']}" if cu["phone"] else ""))
                av = st.text_input("不吃什麼／過敏", value=cu["avoid"] or "", key=f"ca{cu['id']}")
                nt = st.text_input("其他偏好", value=cu["note"] or "", key=f"cn{cu['id']}")
                c1, c2 = st.columns(2)
                if c1.button("儲存", key=f"cs{cu['id']}", use_container_width=True):
                    orders.update_customer(conn, cu["id"], avoid=av, note=nt)
                    st.rerun()
                if c2.button("移出名單", key=f"cd{cu['id']}", use_container_width=True):
                    orders.update_customer(conn, cu["id"], active=0)
                    st.rerun()


# ───────────────────────── 導覽 ─────────────────────────
if "user" not in st.session_state:
    login_view(conn, "🍽️ 餐點點餐系統", allow_device=True)
else:
    user = st.session_state.user
    orders.ensure_default_tables(conn)   # 第一次使用：1 樓 4 桌、2 樓 4 桌，每桌 4 人
    # 依帳號角色只註冊能進的頁面：廚房帳號只有「廚房、吧台、點餐」，網址手動輸入其他頁也進不去
    ALL_PAGES = {
        "floor": (floor_page, "外場總控台", "🗺️"), "order": (order_page, "點餐", "🍽️"),
        "kitchen": (kitchen_page, "廚房", "👨‍🍳"), "bar": (bar_page, "吧台", "🍹"),
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
