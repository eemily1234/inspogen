import streamlit as st
from gtts import gTTS
import os
import requests
from io import BytesIO
from PIL import Image

# 文案生成使用 Google Gemini API（或改用 OpenRouter 免費模型）
# 注意：這裡只示意，請替換 YOUR_API_KEY
def generate_caption(topic, style):
    return f"這是一篇模擬的 {style} 風格 IG 貼文，主題是：{topic} ☕️📸"

st.title("📸 InspoGen - 免費多模態 IG 產生器")

topic = st.text_input("輸入貼文主題（如：日常咖啡廳）")
style = st.selectbox("選擇風格", ["療癒", "搞笑", "文青", "極簡"])

if st.button("產生貼文內容"):
    caption = generate_caption(topic, style)
    st.markdown("### 📄 IG 貼文內容")
    st.write(caption)

    # 語音產生 (gTTS)
    tts = gTTS(caption, lang="zh")
    tts.save("voice.mp3")
    st.audio("voice.mp3")

    # 圖片示意（使用 Bing 圖庫）
    img_url = f"https://source.unsplash.com/400x300/?{topic},aesthetic"
    response = requests.get(img_url)
    image = Image.open(BytesIO(response.content))
    st.image(image, caption="情境示意圖")

    # 下載語音
    with open("voice.mp3", "rb") as audio_file:
        st.download_button("下載語音", audio_file, file_name="voice.mp3")



