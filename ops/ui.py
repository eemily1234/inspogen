"""兩個 App（ops_app.py 營運後台、meal_app.py 點餐系統）共用的畫面元件。"""
import streamlit as st

from . import services as s

CAT = {"health": "保健品", "organic": "有機商品", "meal": "餐點"}
PAY = ["現金", "轉帳", "行動支付", "信用卡"]


def role_label(user):
    if s.is_kitchen(user):
        return "廚房"
    return "老闆" if user["role"] == s.OWNER else "店員"


def login_view(conn, title):
    """登入畫面；第一次使用會先建立老闆帳號。兩個 App 共用同一份使用者資料。"""
    st.title(title)
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


def sidebar_user(user):
    with st.sidebar:
        st.caption(f"{user['name']}（{role_label(user)}）")
        if st.button("登出"):
            del st.session_state["user"]
            st.rerun()
