import streamlit as st
from gtts import gTTS
import requests

# 使用 Hugging Face 免費模型（無需 API 金鑰）
HF_API_URL = "https://api-inference.huggingface.co/models/microsoft/DialoGPT-medium"

def generate_caption(topic, style):
    headers = {"Content-Type": "application/json"}
    payload = {
        "inputs": f"用{style}語氣寫一段關於「{topic}」的 IG 貼文，加入表情符號，大約 50 字。",
    }
    try:
        response = requests.post(HF_API_URL, headers=headers, json=payload)
        result = response.json()
        if isinstance(result, list) and len(result) > 0:
            return result[0]["generated_text"]
        else:
            return "⚠️ 無法產生貼文，請稍後再試。"
    except:
        return "❌ 發生錯誤，請檢查網路或稍後重試。"

st.title("📸 InspoGen - HuggingFace 升級版")

# 下拉選單改寫主題選擇
topic = st.selectbox("選擇主題", ["咖啡廳", "旅行", "閱讀", "穿搭", "日常生活"])
style = st.selectbox("選擇文風", ["療癒", "搞笑", "文青", "極簡"])
img_style = st.selectbox("選擇圖片風格", ["自然", "插畫", "極簡", "復古"])
tts_speed = st.selectbox("語音語速", ["正常", "稍慢", "快速"])

if st.button("產生貼文內容"):
    caption = generate_caption(topic, style)
    st.markdown("### 📄 IG 貼文內容")
    st.write(caption)

    style_map = {"自然": "natural", "插畫": "illustration", "極簡": "minimalist", "復古": "vintage"}
    topic_map = {
        "咖啡廳": "coffee shop", "穿搭": "fashion", "旅行": "travel",
        "閱讀": "reading", "日常生活": "daily lifestyle"
    }
