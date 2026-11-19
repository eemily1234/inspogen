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
import streamlit.components.v1 as components

from ops import db, einvoice as ei, orders, services as s, uber
from ops.ui import PAY, invoice_inputs, invoice_message, login_view, sidebar_user

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
.qboard{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.35rem;margin:.4rem 0 .7rem}
.qcol{border:3px solid;border-radius:16px;padding:.4rem .5rem;background:rgba(127,127,127,.05)}
.qcol h4{margin:0 0 .3rem;font-size:1rem;padding:0}
.qc-cooking{border-color:#f08c00}.qc-ready{border-color:#2f7de1}.qc-dining{border-color:#c9a400}
.qchips{display:flex;flex-wrap:wrap;gap:.35rem}
.qchip{border-radius:12px;padding:.25rem .55rem;background:rgba(127,127,127,.12);line-height:1.15}
.qchip b{display:block;font-size:1.3rem;font-variant-numeric:tabular-nums}
.qcol h4{font-size:.88rem !important;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.qcol{min-width:0}
.qchip span{font-size:.8rem;opacity:.85}
.qchip i{display:block;font-style:normal;font-size:.75rem;color:#2e9e5b;font-weight:700}
.qc-ready .qchip{background:rgba(47,125,225,.16)}
.qempty{opacity:.5;padding:.2rem}
.tc-paid{color:#2e9e5b;font-weight:700}
/* DJ 打碟墊式點餐鍵：方形、厚實、按下會沉下去；顏色＝廚房（橘紅）、吧台（藍）、套餐（紫） */
[class*="st-key-inv_kind_"] button{min-height:48px;font-size:1.02rem;padding:6px 16px}
[class*="st-key-padk_"] button,[class*="st-key-padb_"] button,[class*="st-key-padc_"] button{
 aspect-ratio:1/1;min-height:5.6rem;max-height:9.5rem;border-radius:18px;border:0;color:#fff;font-weight:800;
 box-shadow:inset 0 -6px 0 rgba(0,0,0,.25),0 4px 10px rgba(0,0,0,.18);transition:transform .06s,box-shadow .06s}
[class*="st-key-padk_"] button{background:linear-gradient(145deg,#ff8a4c,#e5484d)}
[class*="st-key-padb_"] button{background:linear-gradient(145deg,#4cc3ff,#2f7de1)}
[class*="st-key-padc_"] button{background:linear-gradient(145deg,#b48cff,#7c4dff)}
[class*="st-key-padk_"] button:active,[class*="st-key-padb_"] button:active,[class*="st-key-padc_"] button:active{
 transform:translateY(3px) scale(.97);box-shadow:inset 0 -2px 0 rgba(0,0,0,.25),0 1px 3px rgba(0,0,0,.2)}
[class*="st-key-padk_"] button p,[class*="st-key-padb_"] button p,[class*="st-key-padc_"] button p{color:#fff;font-size:1rem;line-height:1.25}
[class*="st-key-padk_"] button:disabled,[class*="st-key-padb_"] button:disabled,[class*="st-key-padc_"] button:disabled{
 background:#8d939a !important;opacity:.5;box-shadow:none}
.pad-legend i{display:inline-block;width:.8rem;height:.8rem;border-radius:4px;margin:0 .2rem 0 .5rem;vertical-align:-1px}
.pad-legend .lk{background:#e5484d}.pad-legend .lb{background:#2f7de1}.pad-legend .lc{background:#7c4dff}
/* 座位圖：座位（空＝虛線、有人＝暖色、選到＝藍）＋中間的桌子（顏色＝狀態），全部可以點 */
[class*="st-key-seate_"] button,[class*="st-key-seatf_"] button,[class*="st-key-seats_"] button{
 min-height:2.7rem;border-radius:999px;font-size:.92rem;padding:.2rem .3rem}
[class*="st-key-seate_"] button{border:2px dashed rgba(127,127,127,.55);background:transparent}
[class*="st-key-seatf_"] button{border:0;background:#ffe2b8;color:#6b3a00}
[class*="st-key-seats_"] button{border:0;background:#2f7de1;color:#fff}
[class*="st-key-seatf_"] button p{color:#6b3a00;font-size:.92rem}
[class*="st-key-seats_"] button p{color:#fff;font-size:.92rem}
[class*="st-key-seate_"] button p{font-size:.92rem}
[class*="st-key-tbl_"] button{min-height:4.4rem;border-radius:14px;border:0;font-weight:800;margin:.15rem 0}
[class*="st-key-tbl_"] button p{color:#fff;font-size:1rem;line-height:1.3}
[class*="st-key-tbl_empty_"] button{background:#2e9e5b}
[class*="st-key-tbl_new_"] button{background:#e5484d}
[class*="st-key-tbl_preparing_"] button{background:#f08c00}
[class*="st-key-tbl_ready_"] button{background:#2f7de1}
[class*="st-key-tbl_dining_"] button{background:#b89400}
[class*="st-key-tbl_"][class*="_on_"] button{box-shadow:0 0 0 4px rgba(20,20,20,.75)}
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


def quick_pick(label, options, key, default=None):
    """觸控選項（可多選）；舊版 Streamlit 沒有 pills 時改用多選框。"""
    default = [x for x in (default or []) if x in options]
    if hasattr(st, "pills"):
        return list(st.pills(label, options, selection_mode="multi", key=key, default=default,
                             label_visibility="collapsed") or [])
    return st.multiselect(label, options, key=key, default=default)


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


SIDEBAR_PAGES = {"shift", "report", "settings"}   # 交班、報表、設定收在側邊欄，不佔頂端導覽列


def top_bar():
    """頂端導覽列：每頁最上方一排大按鈕（手機也維持同一列），取代 Streamlit 預設導覽。
    交班、報表收在左側邊欄（點左上角 » 打開），不佔頂端位置。"""
    if not USE_TOPBAR:
        return
    shown = [pg for k, pg in PAGES.items() if k not in SIDEBAR_PAGES]
    cols = st.columns(len(shown))
    for col, pg in zip(cols, shown):
        col.page_link(pg, label=pg.title, icon=pg.icon, use_container_width=True)


def sidebar_tools():
    """側邊欄：交班狀態（店員、老闆）與今日營收摘要（只有老闆），各自連到完整頁面。"""
    if "shift" in PAGES:
        with st.sidebar:
            st.markdown("### 💰 交班")
            try:
                sh = orders.current_shift(conn)
                if sh:
                    summ = orders.shift_summary(conn, sh["id"])
                    st.caption(f"🟢 班別進行中（{hhmm(sh['opened_at'])} 開班）")
                    st.metric("抽屜應有現金", f"＄{summ['expected_cash']}")
                else:
                    st.caption("⚪ 目前沒有開班")
            except Exception:   # 側邊欄只是摘要，出錯不影響主畫面
                st.caption("交班狀態暫時無法顯示")
            st.page_link(PAGES["shift"], label="開班／關班", icon="💰", use_container_width=True)
            st.divider()
    if "report" in PAGES:
        _sidebar_report()
    if "settings" in PAGES:
        with st.sidebar:
            st.markdown("### ⚙️ 設定")
            st.caption("桌位、菜單、套餐、加價選項、售完、熟客")
            st.page_link(PAGES["settings"], label="打開設定", icon="⚙️", use_container_width=True)
            st.divider()


def _sidebar_report():
    with st.sidebar:
        st.markdown("### 📊 今日營業")
        try:
            r = orders.sales_report(conn, date.today(), date.today())
            st.metric("營收（實收）", f"＄{r['revenue']}")
            c1, c2 = st.columns(2)
            c1.metric("單數", r["orders"])
            c2.metric("客單價", f"＄{r['avg_order']}")
            if r["ranking"]:
                st.caption("今日熱銷：" + "、".join(f"{x['name']}×{x['qty']}" for x in r["ranking"][:3]))
        except Exception:   # 側邊欄只是摘要，出錯不影響主畫面
            st.caption("今日數字暫時無法顯示")
        st.page_link(PAGES["report"], label="看完整報表", icon="📊", use_container_width=True)
        st.divider()


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


# ───────────────────────── 外場總控台：可觸控的座位圖 ─────────────────────────
TABLE_ICON = {"empty": "🟩", "new": "🟥", "preparing": "🟧", "ready": "🟦", "dining": "🟨"}


def _select(kind, key, seat=None):
    st.session_state["sel"] = {"kind": kind, "key": key}
    st.session_state["sel_seat"] = seat or 0
    st.session_state["scroll_ws"] = True   # 點了桌子或座位 → 畫面自動捲到處理區


SCROLL_TO_WORKSPACE = """<script>
const a = window.parent.document.querySelector('.ws-anchor');
if (a) { a.scrollIntoView({behavior: 'smooth', block: 'start'}); }
</script>"""


def _pending_seats(table):
    """空桌還沒送單前登記的座位（誰坐、不吃什麼），送單時一起寫入。"""
    return st.session_state.setdefault(f"pseats_{table}", {})


def _seat_info(t):
    """座位圖要顯示的資料：每個座位坐誰、不吃什麼、點了幾道。座位與訂單資料直接連動。"""
    n_seats = max(t["seats"], t.get("guests") or 0)
    info = {n: {"name": "", "avoid": "", "regular": False, "dishes": 0} for n in range(1, n_seats + 1)}
    for x in t.get("seats_info", []):
        i = info.setdefault(x["seat_no"], {"name": "", "avoid": "", "regular": False, "dishes": 0})
        i.update(name=x["guest_name"] or "", avoid=x["avoid"] or "", regular=bool(x["customer_id"]))
    if t["stage"] == "empty":
        for n, x in _pending_seats(t["name"]).items():
            i = info.setdefault(int(n), {"name": "", "avoid": "", "regular": False, "dishes": 0})
            i.update(name=x.get("guest_name") or "", avoid=x.get("avoid") or "", regular=bool(x.get("customer_id")))
    for d in t["dishes"]:
        if d.get("seat_no"):
            info.setdefault(d["seat_no"], {"name": "", "avoid": "", "regular": False, "dishes": 0})["dishes"] += d["qty"]
    return info


def _seat_label(n, i):
    label = f"{n}"
    if i["name"]:
        label += f" {('⭐' if i['regular'] else '')}{i['name'][:4]}"
    if i["dishes"]:
        label += f"・{i['dishes']}道"
    if i["avoid"]:
        label += " 🚫"
    return label


def table_map(t, sel):
    """一張桌子的座位圖：上排座 1、2，中間是桌子，下排座 3、4（一邊兩人）。座位和桌子都可以點。"""
    info = _seat_info(t)
    on_table = sel.get("kind") == "table" and sel.get("key") == t["name"]
    sel_seat = st.session_state.get("sel_seat") if on_table else None

    def seat_button(col, n):
        i = info.get(n)
        if not i:
            return
        kind = "s" if sel_seat == n else ("f" if (i["name"] or i["dishes"] or i["avoid"]) else "e")
        tip = (f"座 {n}：{i['name'] or '未登記'}" + (f"，不吃：{i['avoid']}" if i["avoid"] else "")
               + (f"，點了 {i['dishes']} 道" if i["dishes"] else ""))
        if col.button(_seat_label(n, i), key=f"seat{kind}_{t['name']}_{n}", use_container_width=True, help=tip):
            _select("table", t["name"], n)
            st.rerun()

    seats = sorted(info)
    top, bottom, extra = seats[:2], seats[2:4], seats[4:]
    row = st.columns(2)
    for col, n in zip(row, top):
        seat_button(col, n)
    stage = t["stage"]
    if stage == "empty":
        text = f"{t['name']}  \n空桌・點座位開桌"
    else:
        money = "💵已結帳" if t.get("paid_all") else f"＄{t['unpaid_total']}"
        text = f"{t['name']}　{t.get('queue') or ''}  \n{orders.STAGE_LABEL[stage]}・{money}"
    if st.button(text, key=f"tbl_{stage}_{'on' if on_table else 'off'}_{t['name']}", use_container_width=True):
        _select("table", t["name"])
        st.rerun()
    row = st.columns(2)
    for col, n in zip(row, bottom):
        seat_button(col, n)
    for k in range(0, len(extra), 2):
        row = st.columns(2)
        for col, n in zip(row, extra[k:k + 2]):
            seat_button(col, n)


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
        tabs = st.tabs(labels + [f"📥 外帶・Uber（{len(ov['takeouts'])}）"])
        for tab, f in zip(tabs, floors):
            with tab:
                st.caption("點桌子＝整桌；點座位＝幫那位客人點餐、記不吃什麼。🟩空 🟥待接單 🟧製作中 🟦待上菜 🟨用餐中")
                ts = [t for t in ov["tables"] if t["floor"] == f]
                for i in range(0, len(ts), 2):
                    row = st.columns(2)
                    for col, t in zip(row, ts[i:i + 2]):
                        with col.container(border=True):
                            table_map(t, sel)
        with tabs[-1]:
            if st.button("＋ 新外帶單", key="new_takeout", type="primary", use_container_width=True):
                _select("new", "takeout")
                st.rerun()
            if not ov["takeouts"]:
                st.caption("目前沒有外帶或 Uber 訂單")
            for tk in ov["takeouts"]:
                icon = "🛵" if tk["where"].startswith("Uber") else "🥡"
                money = "💵已結帳" if tk.get("paid_all") else f"＄{tk['unpaid_total']}"
                dishes = "、".join(f"{d['qty']}×{d['name']}" for d in tk["dishes"])
                chosen = sel.get("kind") == "order" and sel.get("key") == tk["order_id"]
                if st.button(f"{tk.get('queue') or ''}　{icon} {tk['where']}・{orders.STAGE_LABEL[tk['stage']]}・{money}  \n"
                             f"🕒{hhmm(tk['opened_at'])}　{dishes}", key=f"pick_o{tk['order_id']}",
                             type="primary" if chosen else "secondary", use_container_width=True):
                    _select("order", tk["order_id"])
                    st.rerun()
    finally:
        c.close()


def _next_step(od):
    """依目前狀況只給一個「下一步」：上菜 → 結帳 → 客人離開。回傳是否該打開結帳區。"""
    if od["to_serve"]:
        if st.button(f"🛎️ 下一步：上菜（{od['to_serve']} 道做好了）", type="primary", use_container_width=True,
                     key=f"next_serve{od['id']}"):
            orders.serve_all_ready(conn, od["id"])
            st.session_state["last_order"] = f"{od['where']} 已上菜"
            st.rerun()
        return False
    if od["can_close"]:
        if st.button("👋 下一步：客人離開・完成（桌子清空）", type="primary", use_container_width=True,
                     key=f"close{od['id']}"):
            try:
                orders.close_order(conn, od["id"])
                st.session_state.pop("sel", None)
                st.session_state["last_order"] = f"{od['where']}（{od['queue']}）已完成，桌子已清空"
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
        return False
    if od["unfinished"]:
        st.info(f"🔥 廚房／吧台製作中（{od['unfinished']} 道），做好會響鈴通知。"
                + ("　💵 已結帳" if od["paid_all"] else ""))
        return False
    if od["unpaid_total"] > 0:
        st.warning(f"✅ 餐點都上齊了，下一步：結帳 ＄{od['unpaid_total']}（下方已打開）")
        return True
    return False


def seat_panel(t, od):
    """座位列：點座位＝之後點的餐算這位客人的；並可登記這位客人是誰、不吃什麼。"""
    info = _seat_info(t)
    labels = {0: "共用"}
    labels.update({n: f"座{n}" + (f" {i['name'][:4]}" if i["name"] else "") + (" 🚫" if i["avoid"] else "")
                   for n, i in info.items()})
    st.markdown("**👥 點座位：幫誰點？**")
    seat = choice_buttons("sel_seat", list(labels), labels, cols=min(5, len(labels)))
    if not seat:
        return 0
    i = info[seat]
    current = {x["seat_no"]: x for x in (od["seats"] if od else [])}
    pend = _pending_seats(t["name"]) if not od else {}
    cur = current.get(seat) or pend.get(seat) or {}
    with st.container(border=True):
        mine = [ln for ln in (od["lines"] if od else []) if ln.get("seat_no") == seat and ln["status"] != "cancelled"]
        mine_txt = "、".join(f"{ICON[ln['status']]}{ln['qty']}×{ln['name']}" for ln in mine)
        st.markdown(f"**座 {seat}**　{('⭐' if i['regular'] else '')}{i['name'] or '（未登記）'}"
                    + (f"　:red[🚫 不吃：{i['avoid']}]" if i["avoid"] else "")
                    + (f"  \n已點：{mine_txt}" if mine else ""))
        with st.expander("✏️ 登記／修改這位客人（熟客、名字、不吃什麼）"):
            customers = orders.list_customers(conn)
            cmap = {c["id"]: c for c in customers}
            options = [0] + list(cmap)
            idx = options.index(cur.get("customer_id")) if cur.get("customer_id") in options else 0
            key = f"{od['id'] if od else t['name']}_{seat}"
            cid = st.selectbox("熟客", options, index=idx, key=f"sp_c_{key}",
                               format_func=lambda x: "（不是熟客）" if x == 0 else
                               f"⭐ {cmap[x]['name']}" + (f"（不吃：{cmap[x]['avoid']}）" if cmap[x]["avoid"] else ""))
            name = st.text_input("名字或稱呼", value=cur.get("guest_name") or "", key=f"sp_n_{key}",
                                 placeholder="例：陳先生、小朋友")
            avoid = st.text_input("不吃什麼／過敏", value="" if cur.get("customer_id") else (cur.get("avoid") or ""),
                                  key=f"sp_a_{key}", placeholder="例：不吃牛、蝦過敏")
            if st.button(f"儲存座 {seat}", key=f"sp_s_{key}", use_container_width=True):
                try:
                    if od:
                        orders.set_seat(conn, od["id"], seat, name, cid or None, avoid)
                    else:   # 還沒開桌：先記著，送單時一起寫入
                        pend[seat] = {"customer_id": cid or None, "guest_name": name, "avoid": avoid}
                        if cid and cmap[cid]["avoid"]:
                            pend[seat]["avoid"] = "；".join(x for x in (cmap[cid]["avoid"], avoid.strip()) if x)
                            pend[seat]["guest_name"] = name.strip() or cmap[cid]["name"]
                    st.session_state["last_order"] = f"已更新座 {seat}"
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))
    return seat


def _workspace():
    """選到的桌子（或座位）或外帶單：同一頁由上往下做完——下一步、座位、點餐、已點、結帳。"""
    sel = st.session_state.get("sel")
    if not sel:
        return
    user = st.session_state.user
    ov = orders.floor_overview(conn)
    ods = {o["id"]: o for o in orders.open_orders(conn)}

    def header(title, sub=""):
        h1, h2 = st.columns([4, 1])
        h1.markdown(f"### {title}" + (f"  \n{sub}" if sub else ""))
        if h2.button("✕", key="ws_close", use_container_width=True, help="收起"):
            st.session_state.pop("sel", None)
            st.rerun()

    st.markdown("<div class='ws-anchor'></div>", unsafe_allow_html=True)
    with st.container(border=True):
        if sel["kind"] == "new":
            header("🥡 新外帶單")
            order_form(user, "takeout", None, after=None, on_placed=lambda r: _select("order", r["order_id"]))
            return
        t = od = None
        if sel["kind"] == "table":
            t = next((x for x in ov["tables"] if x["name"] == sel["key"]), None)
            if not t:
                st.session_state.pop("sel", None)
                return
            od = ods.get(t["order_id"]) if t["order_id"] else None
        else:
            od = ods.get(sel["key"])
            if not od:
                st.session_state.pop("sel", None)
                st.rerun()

        if od:
            stage = orders._stage_of(od)
            paid = "💵 已結帳" if od["paid_all"] else f"待付 ＄{od['unpaid_total']}"
            header(f"{od['queue']}　{TABLE_ICON[stage]} {od['where']}　{orders.STAGE_LABEL[stage]}",
                   f"🕒 {hhmm(od['created_at'])} 開單・⏱ {orders.wait_minutes(od['created_at'])} 分鐘"
                   f"{('・' + str(od['guests']) + ' 人') if od.get('guests') else ''}・{paid}")
            if od["note"]:
                st.markdown(f":red[⚠️ {od['note']}]")
            pay_focus = _next_step(od)
        else:
            header(f"{TABLE_ICON['empty']} 桌 {t['name']}　開桌點餐", f"{t['seats']} 人桌（一邊兩人）・點座位再點餐，人數自動計算")
            pay_focus = False

        seat = seat_panel(t, od) if t else 0
        if t:
            info = _seat_info(t)
            st.markdown(f"**➕ 點餐{'（算座 ' + str(seat) + ' 的）' if seat else '（共用）'}**")
            order_form(user, "dine_in", t["name"], after=None, seat_count=len(info),
                       seat_names={n: i["name"] for n, i in info.items() if i["name"]},
                       seat_avoid={n: i["avoid"] for n, i in info.items() if i["avoid"]},
                       default_seat=seat or None, new_table=od is None)
        if not od:
            return
        st.markdown("**🍽️ 已點**（點一道菜可以上菜、招待或取消）")
        _dish_buttons(od)
        with st.expander(f"💵 結帳（{'已結清' if od['paid_all'] else '待付 ＄' + str(od['unpaid_total'])}）",
                         expanded=pay_focus):
            checkout_section(od)
        with st.expander("📣 通知廚房／吧台"):
            notify_kitchen_section(od)
        if t:
            with st.expander("🔀 換桌／併桌"):
                move_section(t, ov)


def floor_page():
    top_bar()
    st.header("🗺️ 外場總控台")
    # 版面固定：訊息、處理區都放在永遠存在的位置，出現或消失都不會讓其他區塊重建
    msg_box = st.container()
    ws_box = st.container()   # 選了桌子或座位 → 處理區直接出現在最上面，不用往下找
    if msg := st.session_state.pop("last_order", None):
        msg_box.success(msg)
    with ws_box:
        _workspace()
    _floor_board()
    test_bell_button("bell_floor")
    if st.session_state.pop("scroll_ws", False) and st.session_state.get("sel"):
        components.html(SCROLL_TO_WORKSPACE, height=0)
    if st.session_state.pop("floor_alert", False):   # 彈跳視窗放最後，同樣不影響上面的版面
        floor_alert_dialog()


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


def _line_label(ln):
    seat = f"座{ln['seat_no']}・" if ln.get("seat_no") else ""
    combo = f"（{'＋'.join(c['name'] for c in ln['components'])}）" if ln.get("components") else ""
    gift = "🎁" if ln.get("gift") else ""
    opts = f"［{ln['options']}］" if ln.get("options") else ""
    return f"{ICON[ln['status']]} {seat}{ln['qty']}× {ln['name']}{opts}{gift}{combo}　{orders.LABEL[ln['status']]}"


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
                st.markdown(f"**{ln['qty']} × {ln['name']}**　＄{ln['qty'] * ln['unit_price']}"
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
                if ln["status"] != "cancelled" and not ln["paid_qty"] and not ln["gift"] and st.button(
                        "🎁 改成招待", key=f"pg{ln['id']}", use_container_width=True):
                    orders.set_gift(conn, ln["id"])
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
    """結帳：整單結清或分開結帳（按 ＋ 設定份數）、優惠折扣與備註、招待（贈品）、均分試算。
    結清後客人還在，按上方「客人離開」才完成。"""
    user = st.session_state.user
    oid = od["id"]
    picks, pick_total = [], 0
    for ln in od["lines"]:
        if ln["status"] == "cancelled":
            continue
        if ln["gift"]:
            st.markdown(f"🎁 {ln['qty']}× {ln['name']}　招待（＄0）")
            continue
        if ln["remaining"] <= 0:
            continue
        c1, c2, c3 = st.columns([3, 2, 1])
        c1.markdown(f"{ln['qty']}× {ln['name']}  \n＄{ln['unit_price']}／份，未付 {ln['remaining']} 份")
        with c2:
            # key 帶上剩餘份數：付款後上限改變，數字自動歸零
            q = stepper(f"pq{ln['id']}_{ln['remaining']}", 0, ln["remaining"], 0)
        if not ln["paid_qty"] and c3.button("🎁", key=f"gift{ln['id']}", use_container_width=True,
                                            help="改成招待（贈品，不收錢）"):
            try:
                orders.set_gift(conn, ln["id"], st.session_state.get(f"dnote_{oid}", ""))
                st.session_state["last_order"] = f"已招待：{ln['name']}"
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
        if q:
            picks.append((ln["id"], q))
            pick_total += q * ln["unit_price"]
    st.markdown(kpi_html([("合計", f"＄{od['total']}"), ("已付", f"＄{od['paid_total']}"),
                          ("待付", f"＄{od['unpaid_total']}")]), unsafe_allow_html=True)
    if od["paid_all"] or od["unpaid_total"] <= 0:
        st.success("已全部結清。客人用完餐離開時，按上方「👋 客人離開・完成」。")
        return

    st.markdown("**🏷️ 優惠折扣**")
    presets = {k: label for k, label, _ in orders.DISCOUNT_PRESETS}
    preset = choice_buttons(f"disc_{oid}", list(presets), presets, cols=3)
    amount = 0
    if preset == "amount":
        amount = st.number_input("折抵金額", 0, od["unpaid_total"], 0, step=10, key=f"damt_{oid}")
    dnote = st.text_input("優惠備註／贈品說明", key=f"dnote_{oid}", placeholder="例：生日 9 折、熟客優惠、招待甜點")
    st.markdown("**付款方式**")
    pay = choice_buttons(f"pay_{oid}", PAY, cols=2)
    inv, inv_err = invoice_inputs(conn, oid, pay)
    if inv_err:
        st.warning(inv_err)

    def pay_button(label_prefix, subtotal, key, do):
        disc = orders.discount_amount(subtotal, preset, amount) if subtotal else 0
        due = subtotal - disc
        detail = f"　（小計 ＄{subtotal} − 折扣 ＄{disc}）" if disc else ""
        if st.button(f"{label_prefix}　＄{due}{detail}", key=key, use_container_width=True,
                     type="primary" if key.startswith("payall") else "secondary",
                     disabled=subtotal <= 0 or bool(inv_err)):
            try:
                sid = do(disc)
                extra = (f"，折扣 ＄{disc}" if disc else "") + (f"（{dnote.strip()}）" if dnote.strip() else "")
                extra += invoice_message(conn, sid)
                st.session_state["last_order"] = f"已收 ＄{due}：{od['where']}（單號 #{sid}）{extra}"
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))

    pay_button("💵 全部結清", od["unpaid_total"], f"payall{oid}",
               lambda d: orders.checkout_order(conn, oid, pay, user["id"], discount=d, note=dnote, invoice=inv))
    pay_button("💵 只結上面選的份數", pick_total, f"paysome{oid}",
               lambda d: orders.checkout_lines(conn, oid, picks, pay, user["id"], discount=d, note=dnote,
                                              invoice=inv))
    with st.expander("🧮 均分試算（只算金額，不入帳）"):
        n = stepper(f"split_n_{oid}", 1, 30, 2)
        st.write("　".join(f"第{i + 1}位 ＄{v}" for i, v in enumerate(orders.split_evenly(od["unpaid_total"], n))))
    if s.can(user, "void") and st.button("取消整張訂單", use_container_width=True, key=f"cancel{oid}"):
        try:
            orders.cancel_order(conn, oid)
            st.session_state.pop("sel", None)
            st.session_state["last_order"] = f"已取消：{od['where']}"
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


# ───────────────────────── 點餐表單（外場總控台與點餐頁共用） ─────────────────────────
def kpi_html(items):
    return "<div class='kpi'>" + "".join(
        f"<div><span>{html.escape(k)}</span><b>{html.escape(v)}</b></div>" for k, v in items) + "</div>"


AVOID_OPTIONS = ["不吃香菜", "不吃蔥", "不吃辣", "不吃牛", "不吃豬", "不吃蛋", "海鮮過敏", "堅果過敏", "素食"]


def _sync_entry(e):
    """份數與「誰點的」存在按鍵狀態裡；顯示或送出前再讀一次，確保跳出視窗裡的選擇一定帶到。"""
    uid = e["uid"]
    if f"oq{uid}" in st.session_state:
        e["qty"] = st.session_state[f"oq{uid}"]
    if f"os{uid}" in st.session_state:
        e["seat"] = st.session_state[f"os{uid}"] or None
    return e


def _entry_note(e):
    """把跳出視窗裡選的不吃、備註、其他文字合成一行，給廚房／吧台看。"""
    parts = list(e.get("avoid", [])) + list(e.get("quick", []))
    if (e.get("text") or "").strip():
        parts.append(e["text"].strip())
    return "、".join(parts)


@st.dialog("📝 這道菜：誰點的、不吃什麼、備註")
def item_dialog(ckey, uid, seat_count, seat_names, seat_avoid):
    """按下點餐鍵後立刻跳出：份數、誰點的（會直接提示這位客人不吃什麼）、不吃、備註、招待。"""
    cart = st.session_state.get(ckey, [])
    e = next((x for x in cart if x["uid"] == uid), None)
    if not e:
        st.write("這道已經移除了。")
        return
    # 視窗在點選後會單獨重跑（不同執行緒），要用自己的資料庫連線
    c = db.connect()
    try:
        it = s.get_item(c, e["item_id"])
        comps = orders.combo_components(c, it["id"])
        groups = orders.item_options(c, it["id"])
    finally:
        c.close()
    st.markdown(f"### {it['name']}　＄{it['price']}"
                + (f"  \n🍱 套餐：{'＋'.join(c['name'] for c in comps)}" if comps else ""))
    e["qty"] = stepper(f"oq{uid}", 1, 99, e.get("qty", 1))
    if seat_count:
        labels = {0: "共用"}
        labels.update({n: f"座{n}" + (f" {seat_names[n]}" if seat_names.get(n) else "") for n in range(1, seat_count + 1)})
        st.markdown("**誰點的？**")
        e["seat"] = choice_buttons(f"os{uid}", list(labels), labels, cols=min(5, len(labels))) or None
        if e["seat"] and seat_avoid.get(e["seat"]):
            st.error(f"🚫 座{e['seat']} {seat_names.get(e['seat']) or ''}：{seat_avoid[e['seat']]}")
    chosen, delta = [], 0
    for g in groups:   # 加價選項：單選用大按鈕、多選用標籤；必選的群組預設選第一個
        lab = {ch["id"]: ch["name"] + (f" +{ch['price_delta']}" if ch["price_delta"] > 0 else
                                      f" {ch['price_delta']}" if ch["price_delta"] < 0 else "") for ch in g["choices"]}
        price = {ch["id"]: ch["price_delta"] for ch in g["choices"]}
        prev = [x for x in e.get("options", []) if x in lab]
        st.markdown(f"**{g['name']}**" + ("（必選）" if g["required"] else "（可多選）" if g["multi"] else "（可不選）"))
        key = f"opt{uid}_{g['id']}"
        if g["multi"]:
            if hasattr(st, "pills"):
                picked = st.pills(g["name"], list(lab), selection_mode="multi", format_func=lab.get, default=prev,
                                  key=key, label_visibility="collapsed") or []
            else:
                picked = st.multiselect(g["name"], list(lab), format_func=lab.get, default=prev, key=key)
            chosen += list(picked)
        else:
            opts = ([] if g["required"] else [0]) + list(lab)
            if key not in st.session_state and prev:
                st.session_state[key] = prev[0]
            v = choice_buttons(key, opts, {0: "不用", **lab}, cols=min(4, len(opts)))
            if v:
                chosen.append(v)
        delta += sum(price[x] for x in chosen if x in price)
    e["options"] = chosen
    if groups:
        st.caption(f"單價 ＄{it['price'] + delta}" + (f"（原價 ＄{it['price']}）" if delta else ""))
    st.markdown("**🚫 不吃／過敏**")
    e["avoid"] = quick_pick("不吃／過敏", AVOID_OPTIONS, f"oa{uid}", default=e.get("avoid", []))
    st.markdown("**📝 備註**")
    e["quick"] = quick_pick("備註", QUICK_NOTES, f"on{uid}", default=e.get("quick", []))
    e["text"] = st.text_input("其他", value=e.get("text", ""), key=f"ot_{uid}", placeholder="例：醬分開、飯少一點")
    e["gift"] = st.toggle("🎁 招待（贈品，不收錢）", value=e.get("gift", False), key=f"og{uid}")
    c1, c2 = st.columns(2)
    if c1.button("✅ 好了", type="primary", use_container_width=True, key=f"ok{uid}"):
        st.rerun()
    if c2.button("🗑 不要這道", use_container_width=True, key=f"rm{uid}"):
        cart.remove(e)
        st.rerun()


def order_form(user, otype, table, after="floor", on_placed=None, seat_count=None,
               seat_names=None, seat_avoid=None, default_seat=None, new_table=False):
    """點餐：DJ 打碟墊式的大方塊，按一下直接加入，並立刻跳出「誰點的、不吃什麼、備註」視窗。
    default_seat：在外場點了某個座位 → 新點的菜預設算那個座位的（視窗裡仍可改）。
    new_table：空桌開桌；人數＝實際有點餐或有登記的座位數，不用另外選。
    每桌（或外帶）有自己的購物車，換桌不會混在一起。
    after=None：送出後留在原頁；否則送出後換到該頁。on_placed(r)：送出成功時呼叫。"""
    accepting = orders.kitchen_status(conn)["accepting"]
    if not accepting:
        kitchen_banner(conn)
    ckey = f"ocart_{table or 'takeout'}"
    cart = st.session_state.setdefault(ckey, [])
    seat_names = dict(seat_names or {})
    seat_avoid = dict(seat_avoid or {})
    pending = st.session_state.get(f"pseats_{table}", {}) if new_table else {}

    meals = [i for i in s.list_items(conn) if i["type"] == "meal"]
    combos = {i["id"]: orders.combo_components(conn, i["id"]) for i in meals}
    avail = orders.availability(conn)
    optmap = {ch["id"]: (ch["name"], ch["price_delta"])
              for i in meals for g in orders.item_options(conn, i["id"]) for ch in g["choices"]}
    if not meals:
        st.info("還沒有餐點，請老闆到「設定」新增。")
    st.markdown("**按一下就點**　<span class='pad-legend'><i class='lk'></i>廚房 <i class='lb'></i>吧台 "
                "<i class='lc'></i>套餐</span>", unsafe_allow_html=True)
    in_cart = {}
    for e in cart:
        _sync_entry(e)
        in_cart[e["item_id"]] = in_cart.get(e["item_id"], 0) + e.get("qty", 1)
    for i in range(0, len(meals), 4):
        row = st.columns(4)
        for col, it in zip(row, meals[i:i + 4]):
            kind = "c" if combos[it["id"]] else ("b" if it["station"] == "bar" else "k")
            badge = f"×{in_cart[it['id']]}  \n" if it["id"] in in_cart else ""
            a = avail.get(it["id"], {"ok": True, "left": None, "why": ""})
            state = f"  \n🚫{a['why']}" if not a["ok"] else (f"  \n剩 {a['left']}" if a["left"] is not None else "")
            if col.button(f"{badge}{it['name']}  \n＄{it['price']}{state}", key=f"pad{kind}_{ckey}_{it['id']}",
                          use_container_width=True, disabled=not a["ok"]):
                uid = uuid4().hex[:8]
                cart.append({"uid": uid, "item_id": it["id"], "qty": 1, "seat": default_seat})
                st.session_state[f"os{uid}"] = default_seat or 0   # 視窗裡「誰點的」預設選好
                st.session_state["pad_edit"] = [ckey, uid]
                st.rerun()

    edit = st.session_state.get("pad_edit")
    if edit and edit[0] == ckey:
        st.session_state.pop("pad_edit", None)
        item_dialog(ckey, edit[1], seat_count if otype == "dine_in" else None, seat_names, seat_avoid)

    names = {i["id"]: i for i in meals}
    total = 0
    if cart:
        st.markdown("**本次點的**")
    for e in list(cart):
        it = names.get(e["item_id"])
        if not it:
            cart.remove(e)
            continue
        _sync_entry(e)
        qty = e.get("qty", 1)
        seat = f"座{e['seat']}・" if e.get("seat") else ""
        note = _entry_note(e)
        gift = "　🎁招待" if e.get("gift") else ""
        opts = [optmap[x] for x in e.get("options", []) if x in optmap]
        price = 0 if e.get("gift") else (it["price"] + sum(d for _, d in opts)) * qty
        if opts:
            note = "、".join(n for n, _ in opts) + ("；" + note if note else "")
        c1, c2, c3 = st.columns([5, 1, 1])
        c1.markdown(f"{seat}**{qty}× {it['name']}**　＄{price}{gift}" + (f"  \n:red[{note}]" if note else ""))
        if c2.button("✏️", key=f"ed{e['uid']}", use_container_width=True, help="修改不吃／備註／份數"):
            st.session_state["pad_edit"] = [ckey, e["uid"]]
            st.rerun()
        if c3.button("🗑", key=f"od{e['uid']}", use_container_width=True, help="移除"):
            cart.remove(e)
            st.rerun()
        total += price
    if not cart:
        st.caption("尚未點餐")
        return
    note_key = f"o_note_{ckey}"
    order_note = st.text_input("整單備註（過敏、趕時間…）", key=note_key)
    st.markdown(f"### 本次合計　＄{total}")
    if st.button("📨 送出", type="primary", use_container_width=True, disabled=not accepting, key=f"send_{ckey}"):
        lines = [{"item_id": e["item_id"], "qty": e.get("qty", 1), "seat": e.get("seat"), "gift": e.get("gift", False),
                  "options": e.get("options", []), "note": _entry_note(e)} for e in cart]
        used = {ln["seat"] for ln in lines if ln["seat"]} | {int(n) for n in pending}
        try:
            r = orders.place_order(conn, otype, lines, table, order_note, user["id"],
                                   guests=len(used) if (otype == "dine_in" and used) else None,
                                   seats=pending or None)
        except s.OpsError as e:
            st.error(str(e))
            return
        where = f"桌 {r['table_no']}" if otype == "dine_in" else f"外帶取餐號 #{r['pickup_no']}"
        for e in cart:
            for k in (f"oq{e['uid']}", f"on{e['uid']}", f"oa{e['uid']}", f"ot_{e['uid']}", f"os{e['uid']}", f"og{e['uid']}"):
                st.session_state.pop(k, None)
        for k in (note_key, ckey, f"pseats_{table}"):
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
    msg_box = st.container()
    if msg := st.session_state.pop("last_order", None):
        msg_box.success(msg)
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
        order_form(user, "dine_in", table, new_table=info["stage"] == "empty",
                   after="floor", seat_count=info["seats"],
                   seat_names={x["seat_no"]: x["guest_name"] for x in info.get("seats_info", [])},
                   seat_avoid={x["seat_no"]: x["avoid"] for x in info.get("seats_info", []) if x["avoid"]})


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
    if ln.get("options"):
        out += f"  \n➕ {ln['options']}"
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

        with st.expander(f"🚫 售完管理（{name}）：一按就售完，外場馬上不能點"):
            avail = orders.availability(c)
            mine = [i for i in s.list_items(c) if i["type"] == "meal" and i["station"] == station
                    and not orders.combo_components(c, i["id"])]
            if not mine:
                st.caption(f"沒有由{name}製作的品項")
            for it in mine:
                a = avail[it["id"]]
                c1, c2, c3 = st.columns([3, 2, 2])
                c1.markdown(f"**{it['name']}**　" + ("🚫 售完" if not a["ok"] else
                                                       f"剩 {a['left']} 份" if a["left"] is not None else "供應中"))
                if a["ok"]:
                    if c2.button("🚫 售完", key=f"so_{station}_{it['id']}", use_container_width=True):
                        orders.set_sold_out(c, it["id"], True)
                        st.rerun()
                elif c2.button("✅ 恢復", key=f"so_{station}_{it['id']}", type="primary", use_container_width=True):
                    orders.set_sold_out(c, it["id"], False)
                    st.rerun()
                with c3.popover("剩幾份", use_container_width=True):
                    n = st.number_input("今天還剩幾份", 0, 999, a["left"] or 0, key=f"pl_{station}_{it['id']}")
                    if st.button("設定", key=f"pls_{station}_{it['id']}", use_container_width=True):
                        orders.set_portions(c, it["id"], n)
                        st.rerun()
                    if st.button("不限份數", key=f"plu_{station}_{it['id']}", use_container_width=True):
                        orders.set_portions(c, it["id"], None)
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
    msg_box = st.container()
    if msg := st.session_state.pop("last_order", None):
        msg_box.success(msg)
    _station_board(station)
    test_bell_button(f"bell_{station}")
    alert = st.session_state.pop(f"{station}_alert", None)
    if alert is not None:
        station_alert_dialog(station, alert)


def kitchen_page():
    _station_page("kitchen", "👨‍🍳 廚房")


def bar_page():
    _station_page("bar", "🍹 吧台")


# ───────────────────────── 交班對帳 ─────────────────────────
def shift_page():
    top_bar()
    st.header("💰 交班對帳")
    msg_box = st.container()
    if msg := st.session_state.pop("last_order", None):
        msg_box.success(msg)
    user = st.session_state.user
    sh = orders.current_shift(conn)
    if not sh:
        st.info("目前沒有開班。開班時先點一下抽屜裡的零用金。")
        cash = st.number_input("零用金（抽屜裡的現金）", 0, 1_000_000, 2000, step=100, key="open_cash")
        if st.button("🟢 開班", type="primary", use_container_width=True):
            try:
                orders.open_shift(conn, cash, user["id"])
                st.session_state["last_order"] = f"已開班，零用金 ＄{cash}"
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
    else:
        summ = orders.shift_summary(conn, sh["id"])
        st.markdown(f"🟢 **班別進行中**　🕒 {hhmm(summ['opened_at'])} 開班")
        st.markdown(kpi_html([("零用金", f"＄{summ['opening_cash']}"), ("現金收入", f"＄{summ['cash_sales']}"),
                              ("抽屜應有現金", f"＄{summ['expected_cash']}"), ("本班總收入", f"＄{summ['total']}")]),
                    unsafe_allow_html=True)
        if summ["by_pay"]:
            st.markdown("**各付款方式**")
            for pay, v in summ["by_pay"].items():
                st.markdown(f"- {pay}：{v['count']} 筆　＄{v['amount']}")
        st.caption("包含外場結帳與後台 POS 的收入；作廢的不算。")
        counted = st.number_input("實際點收的現金", 0, 10_000_000, summ["expected_cash"], step=10, key="close_cash")
        diff = counted - summ["expected_cash"]
        if diff:
            st.warning(f"差額 ＄{diff:+d}（{'多' if diff > 0 else '少'} ＄{abs(diff)}）")
        note = st.text_input("備註（差額原因等）", key="close_note")
        if st.button("🔴 關班", type="primary", use_container_width=True):
            try:
                r = orders.close_shift(conn, counted, user["id"], note)
                d = r["diff"]
                st.session_state["last_order"] = (f"已關班：應有 ＄{r['expected_cash']}，點收 ＄{r['counted_cash']}，"
                                                  + ("帳款相符 ✅" if d == 0 else f"差額 ＄{d:+d}"))
                for k in ("close_cash", "close_note"):
                    st.session_state.pop(k, None)
                st.rerun()
            except s.OpsError as e:
                st.error(str(e))
    hist = [x for x in orders.recent_shifts(conn) if x["closed_at"]]
    if hist:
        st.subheader("最近的班")
        for x in hist:
            d = x["counted_cash"] - x["expected_cash"]
            st.markdown(f"{x['opened_at'][5:16]} → {x['closed_at'][11:16]}　應有 ＄{x['expected_cash']}　點收 ＄{x['counted_cash']}　"
                        + ("✅ 相符" if d == 0 else f"⚠️ 差 ＄{d:+d}") + (f"　（{x['note']}）" if x["note"] else ""))


# ───────────────────────── 報表（老闆） ─────────────────────────
def report_page():
    top_bar()
    st.header("📊 營業報表")
    c1, c2 = st.columns(2)
    start = c1.date_input("從", date.today(), key="rp_start")
    end = c2.date_input("到", date.today(), key="rp_end")
    r = orders.sales_report(conn, start, end)
    st.markdown(kpi_html([("營收（實收）", f"＄{r['revenue']}"), ("單數", f"{r['orders']} 單"),
                          ("客單價", f"＄{r['avg_order']}"), ("來客數", f"{r['guests']} 人"),
                          ("折扣", f"＄{r['discount']}"), ("招待", f"＄{r['gift_value']}")]), unsafe_allow_html=True)
    st.subheader("🏆 餐點排行")
    if r["ranking"]:
        st.dataframe([{"品項": x["name"], "份數": x["qty"], "金額": x["amount"], "招待份數": x["gift_qty"]}
                      for x in r["ranking"]], use_container_width=True, hide_index=True)
    else:
        st.caption("這段期間沒有點餐")
    st.subheader("🕒 各時段營收")
    if r["by_hour"]:
        st.bar_chart({"時段": [f"{h:02d}時" for h in r["by_hour"]], "營收": list(r["by_hour"].values())},
                     x="時段", y="營收")
    else:
        st.caption("這段期間沒有結帳")
    st.subheader("💳 付款方式")
    for pay, amt in r["by_pay"].items():
        st.markdown(f"- {pay}：＄{amt}")
    st.caption("營收＝餐點結帳的實收金額（已扣折扣、不含作廢）；客單價＝營收 ÷ 有結帳的單數；"
               "招待＝招待品項以原價計算的金額。")


# ───────────────────────── 設定（老闆）：桌位、菜單、套餐、熟客 ─────────────────────────
def einvoice_settings():
    """電子發票：啟用、登記字軌（號碼區間）、看今天開了哪些、上傳佇列。"""
    on = st.toggle("啟用電子發票（結帳時開立）", value=ei.enabled(conn), key="ei_on")
    if on != ei.enabled(conn):
        ei.set_enabled(conn, on)
        st.rerun()
    st.caption("Uber Eats 的單由平台開發票，這裡不重複開。作廢銷貨會一併作廢發票（只能同一期；跨期要開折讓單）。")
    period = ei.period_of(date.today())
    left = ei.remaining(conn)
    (st.error if left == 0 else st.warning if left <= 20 else st.success)(
        f"本期（{ei.period_label(period)}）剩 {left} 個號碼")
    with st.expander("➕ 登記字軌（加值中心或財政部給的號碼區間）", expanded=left == 0):
        with st.form("ei_track", clear_on_submit=True):
            c1, c2 = st.columns(2)
            p = c1.text_input("期別", value=period, help="民國年＋偶數月，例 11412 是 114 年 11–12 月")
            pre = c2.text_input("字軌（2 個英文字母）", max_chars=2)
            c3, c4 = st.columns(2)
            a = c3.number_input("起號", 0, 99999999, 0, format="%08d")
            b = c4.number_input("迄號", 0, 99999999, 49, format="%08d")
            if st.form_submit_button("登記", use_container_width=True):
                try:
                    ei.add_track(conn, p, pre, a, b)
                    st.success("已登記")
                    st.rerun()
                except s.OpsError as e:
                    st.error(str(e))
    for t in ei.list_tracks(conn)[:12]:
        st.caption(f"{ei.period_label(t['period'])}　{t['prefix']}{t['start_no']:08d}～{t['prefix']}{t['end_no']:08d}"
                   f"　下一張 {t['prefix']}{min(t['next_no'], t['end_no']):08d}　剩 {max(t['remaining'], 0)} 張")
    q = ei.queue_counts(conn)
    st.markdown("**上傳狀態**")
    st.info(f"待上傳 {q['pending']}　失敗待重送 {q['failed']}　已上傳 {q['sent']}  \n"
            "加值中心還沒接上：發票號碼照常開，資料先存在這裡，接上後會依序補傳。"
            "法規規定開立後 48 小時內要上傳，正式使用前請先完成串接。")
    st.markdown("**今天開的發票**")
    rows = ei.list_invoices(conn)
    if not rows:
        st.caption("今天還沒有")
    for r in rows:
        st.caption(f"{'🚫 ' if r['status'] == 'void' else ''}{r['number']}　＄{r['amount']}　{ei.describe_buyer(r)}"
                   f"　隨機碼 {r['random_code']}　{r['created_at'][11:16]}")


def settings_page():
    top_bar()
    st.header("⚙️ 設定")
    t1, t2, t3, t4 = st.tabs(["桌位", "菜單・套餐", "熟客名單", "🧾 電子發票"])
    with t4:
        einvoice_settings()
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
                with st.expander("➕ 加價選項（份量、加料、冰量甜度…）"):
                    for g in orders.item_options(conn, it["id"]):
                        g1, g2 = st.columns([4, 1])
                        g1.markdown(f"**{g['name']}**" + ("（必選）" if g["required"] else "") + ("（可多選）" if g["multi"] else "")
                                    + "  \n" + "、".join(ch["name"] + (f"+{ch['price_delta']}" if ch["price_delta"] > 0 else
                                                                      f"{ch['price_delta']}" if ch["price_delta"] < 0 else "")
                                                         for ch in g["choices"]))
                        if g2.button("刪除", key=f"og_del{g['id']}", use_container_width=True):
                            orders.delete_option_group(conn, g["id"])
                            st.rerun()
                    with st.form(f"og_new{it['id']}", clear_on_submit=True):
                        gname = st.text_input("群組名稱", placeholder="例：份量、加料、冰量、甜度")
                        gchoices = st.text_input("選項（用逗號分隔，加價寫 +數字）", placeholder="例：小份-10, 正常, 大份+20")
                        r1, r2 = st.columns(2)
                        req = r1.checkbox("必選")
                        multi = r2.checkbox("可多選")
                        if st.form_submit_button("新增選項群組", use_container_width=True):
                            try:
                                orders.add_option_group(conn, it["id"], gname, gchoices, required=req, multi=multi)
                                st.rerun()
                            except s.OpsError as e:
                                st.error(str(e))
                b1, b2 = st.columns(2)
                sold = bool(it.get("sold_out"))
                if b1.button("✅ 恢復供應" if sold else "🚫 今日售完", key=f"msold{it['id']}", use_container_width=True):
                    orders.set_sold_out(conn, it["id"], not sold)
                    st.rerun()
                if b2.button("恢復販售" if not it["active"] else "下架（停售）", key=f"ma{it['id']}", use_container_width=True):
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
        "floor": (floor_page, "外場", "🗺️"), "order": (order_page, "點餐", "🍽️"),
        "kitchen": (kitchen_page, "廚房", "👨‍🍳"), "bar": (bar_page, "吧台", "🍹"),
        "shift": (shift_page, "交班", "💰"), "report": (report_page, "報表", "📊"),
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
    sidebar_tools()
    sidebar_user(user)
