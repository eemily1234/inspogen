import streamlit as st
import requests
from gtts import gTTS

# 設定頁面標題
st.set_page_config(page_title="InspoGen - 圖片描述 + Pixabay 圖片", page_icon="🌄")

# 設定 API 金鑰
API_KEY = "50612478-642e69e59d3208e7eae44e7e0"

# UI
st.title("🌄 InspoGen - 使用 Pixabay 與語音描述")

st.header("Step 1：輸入主題與文風")
topic = st.text_input("請輸入主題（如：咖啡廳）")
style = st.selectbox("選擇圖片風格", ["插畫", "寫實", "極簡", "療癒"])

# 產生圖片與描述語音
if st.button("🎨 產生圖片與語音描述"):
    if not topic:
        st.warning("請輸入主題")
    else:
        # 搜尋 Pixabay 圖片
        search_url = f"https://pixabay.com/api/?key={API_KEY}&q={topic}&image_type=photo&per_page=3"
        res = requests.get(search_url)
        data = res.json()

        if "hits" in data and len(data["hits"]) > 0:
            image_url = data["hits"][0]["webformatURL"]
            description = f"這是一張以「{topic}」為主題的圖片，風格為 {style}。"

            st.image(image_url, caption=description)
            st.markdown("### 語音描述")
            tts = gTTS(description, lang="zh")
            tts.save("desc.mp3")
            st.audio("desc.mp3")
            with open("desc.mp3", "rb") as f:
                st.download_button("⬇️ 下載語音檔", f, file_name="desc.mp3")
        else:
            st.error("找不到符合主題的圖片，請換一個試試～")
