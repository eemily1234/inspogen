import streamlit as st
from gtts import gTTS
import os
import requests

# ⛅ 不使用 openai，模擬生成內容（未來可串 Gemini、OpenRouter）
def generate_caption(topic, style):
    return f"這是一篇模擬的 {style} 風格 IG 貼文，主題是：{topic} ☕️📸"

st.title("📸 InspoGen - 免費多模態 IG 貼文產生器")

# 使用者輸入
topic = st.text_input("輸入貼文主題（如：日常咖啡廳）")
style = st.selectbox("選擇文風", ["療癒", "搞笑", "文青", "極簡"])

if st.button("產生貼文內容") and topic:
    # 📄 貼文文字
    caption = generate_caption(topic, style)
    st.markdown("### 📄 IG 貼文內容")
    st.write(caption)

    # 🔊 語音產生（gTTS）
    tts = gTTS(caption, lang="zh")
    tts.save("voice.mp3")
    st.audio("voice.mp3")

    with open("voice.mp3", "rb") as audio_file:
        st.download_button("⬇️ 下載語音", audio_file, file_name="voice.mp3")

    # 🖼️ 圖片示意（Bing / Unsplash）
    img_url = f"https://source.unsplash.com/400x300/?{topic},aesthetic"
    st.image(img_url, caption="情境示意圖（自動取得）")

