import streamlit as st
from gtts import gTTS
import requests

# 使用 Hugging Face Inference API（免費版，不需金鑰）
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
            return "⚠️ 目前無法產生貼文，請稍後再試或更換主題。"
    except:
        return "❌ 發生錯誤，請檢查網路或稍後重試。"

st.title("📸 InspoGen - HuggingFace 免費備用版")

topic = st.text_input("輸入貼文主題（如：咖啡廳）")
style = st.selectbox("選擇貼文文風", ["療癒", "搞笑", "文青", "極簡"])
img_style = st.selectbox("選擇圖片風格", ["自然", "插畫", "極簡", "復古"])
tts_speed = st.selectbox("語音語速選擇", ["正常", "稍慢", "快速"])

if st.button("產生貼文內容") and topic:
    caption = generate_caption(topic, style)
    st.markdown("### 📄 IG 貼文內容")
    st.write(caption)

    style_map = {"自然": "natural", "插畫": "illustration", "極簡": "minimalist", "復古": "vintage"}
    topic_map = {"咖啡廳": "coffee shop", "穿搭": "fashion", "旅行": "travel", "閱讀": "reading"}
    translated_topic = topic_map.get(topic, "lifestyle")
    translated_style = style_map.get(img_style, "aesthetic")
    search_term = f"{translated_topic},{translated_style}"
    img_url = f"https://source.unsplash.com/400x300/?{search_term}"

    try:
        st.image(img_url, caption=f"情境示意圖：{img_style}風")
    except:
        fallback_img = "https://source.unsplash.com/400x300/?coffee"
        st.image(fallback_img, caption="預設示意圖 ☕")

    voice_text = f"這張圖呈現「{topic}」的風格，為 {style} 文風與 {img_style} 視覺。貼文如下：" + caption
    tts = gTTS(voice_text, lang="zh", slow=True if tts_speed == "稍慢" else False)
    tts.save("voice.mp3")
    st.audio("voice.mp3")

    with open("voice.mp3", "rb") as audio_file:
        st.download_button("⬇️ 下載語音", audio_file, file_name="voice.mp3")
