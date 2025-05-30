import streamlit as st
from gtts import gTTS
import os
import requests

# 📄 模擬文字生成（可替換為 Gemini/OpenRouter）
def generate_caption(topic, style):
    return f"這是一篇模擬的 {style} 風格 IG 貼文，主題是：{topic} ☕️📸"

def generate_hashtags(topic):
    topic_map = {
        "咖啡廳": "#咖啡廳 #午後時光 #文青日常",
        "旅行": "#旅行日記 #探索世界 #拍照打卡",
        "穿搭": "#今日穿搭 #OOTD #時尚風格"
    }
    return topic_map.get(topic, f"#{topic} #生活分享 #inspo")

st.title("📸 InspoGen - 進階版 IG 多模態產生器")

# 🧾 使用者輸入
topic = st.text_input("輸入貼文主題（如：咖啡廳）")
style = st.selectbox("選擇貼文文風", ["療癒", "搞笑", "文青", "極簡"])
img_style = st.selectbox("選擇圖片風格", ["自然", "插畫", "極簡", "復古"])
tts_speed = st.selectbox("語音語速選擇", ["正常", "稍慢", "快速"])

if st.button("產生貼文內容") and topic:
    # 📄 生成文字與 hashtag
    caption = generate_caption(topic, style)
    hashtags = generate_hashtags(topic)

    st.markdown("### 📄 IG 貼文內容")
    st.write(caption)
    st.markdown("### 🔖 Hashtag 推薦")
    st.write(hashtags)

    # 🖼️ 圖片風格融合
    search_term = f"{topic},{img_style}"
    img_url = f"https://source.unsplash.com/400x300/?{search_term}"
    try:
        st.image(img_url, caption=f"情境示意圖：{img_style}風")
    except:
        fallback_img = "https://source.unsplash.com/400x300/?coffee"
        st.image(fallback_img, caption="預設示意圖 ☕")

    # 🔊 語音產生（語速控制）
    desc = f"這張圖片呈現了「{topic}」的情境，帶有{style}風格與 {img_style} 視覺效果。"
    voice_text = desc + " 接著是貼文內容：" + caption

    tts = gTTS(voice_text, lang="zh", slow=True if tts_speed == "稍慢" else False)
    tts.save("voice.mp3")
    st.audio("voice.mp3")

    with open("voice.mp3", "rb") as audio_file:
        st.download_button("⬇️ 下載語音", audio_file, file_name="voice.mp3")




