"""兩個 App（ops_app.py 營運後台、meal_app.py 點餐系統）共用的畫面元件。"""
import streamlit as st

from . import einvoice as ei
from . import services as s

CAT = {"health": "保健品", "organic": "有機商品", "meal": "餐點"}
PAY = ["現金", "轉帳", "行動支付", "信用卡", "Uber Eats"]


def role_label(user):
    if s.is_kitchen(user):
        return "廚房"
    return "老闆" if user["role"] == s.OWNER else "店員"


def login_view(conn, title, allow_device=False):
    """登入畫面；第一次使用會先建立老闆帳號。兩個 App 共用同一份使用者資料。

    allow_device=True（只有餐點系統）：網址帶 ?device=密鑰 時自動登入（平板書籤，免輸入 PIN）。"""
    if allow_device and not st.session_state.get("user"):
        user = s.user_from_device_token(conn, st.query_params.get("device"))
        if user:
            st.session_state.user = user
            st.rerun()
    st.title(title)
    if not s.has_users(conn):
        st.info("第一次使用：請設定老闆帳號")
        with st.form("first"):
            name = st.text_input("姓名")
            pin = st.text_input(f"PIN（至少 {s.MIN_PIN_LENGTH} 位數字）", type="password")
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
            try:
                user = s.authenticate(conn, name, pin)
            except s.OpsError as e:   # 失敗次數過多被鎖定
                st.error(str(e))
                return
            if user:
                st.session_state.user = user
                st.rerun()
            st.error("姓名或 PIN 錯誤")


def sidebar_user(user):
    with st.sidebar:
        st.caption(f"{user['name']}（{role_label(user)}）")
        if st.button("登出"):
            del st.session_state["user"]
            st.rerun()


def invoice_inputs(conn, key, pay_method=None):
    """結帳時的電子發票欄位。回傳 (買受人資料 dict 或 None, 錯誤訊息或 None)。
    沒啟用電子發票、或付款方式由平台開票（Uber Eats）時不顯示，回傳 (None, None)。"""
    if not ei.enabled(conn) or pay_method in ei.EXEMPT_PAY:
        return None, None
    st.markdown("**🧾 發票**")
    kinds = list(ei.CARRIER_TYPES)
    kind = st.pills("發票類型", kinds, format_func=lambda k: ei.CARRIER_TYPES[k], default="print",
                    key=f"inv_kind_{key}", label_visibility="collapsed") or "print"
    hints = {"mobile": ("手機條碼", "/ABC+123"), "cdc": ("自然人憑證號碼", "AB12345678901234"),
             "ban": ("統一編號", "8 位數字"), "donate": ("捐贈碼", "例：919（創世基金會）")}
    if kind == "print":
        left = ei.remaining(conn)
        if left == 0:
            return None, "這一期的發票號碼用完了或還沒登記（設定 → 電子發票）"
        if left <= 20:
            st.caption(f"⚠️ 這一期發票號碼只剩 {left} 張")
        return {"kind": "print"}, None
    label, ph = hints[kind]
    value = st.text_input(label, key=f"inv_val_{key}_{kind}", placeholder=ph)
    if not value.strip():
        return None, f"請輸入{label}"
    try:
        return ei.normalize_buyer({"kind": kind, "value": value}), None
    except s.OpsError as e:
        return None, str(e)


def invoice_message(conn, sale_id):
    inv = ei.for_sale(conn, sale_id)
    if not inv:
        return ""
    tail = "，請列印證明聯" if not (inv["carrier_type"] or inv["donate_code"]) else ""
    return f"　🧾 發票 {inv['number']}（隨機碼 {inv['random_code']}，{ei.describe_buyer(inv)}{tail}）"
