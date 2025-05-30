import streamlit as st
from gtts import gTTS
import os
import requests
from openai import OpenAI

# ✅ 使用新版 OpenAI SDK 的安全方式
client = OpenAI(api_key=st.secrets["OPENAI_API_KEY"])

# 🧠 GPT 文案生成
def generate_caption(topic, style):
    prompt = f"請用 {style} 風格寫一篇關於「{topic}」的 IG 貼文，約80字，加入 emoji。"
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

# 🔖 GPT Hashtag 產生
def generate_hashtags(topic):
    prompt = f"針對「{topic}」這個主題產生 3 個熱門 IG hashtag，格式為：#xxx #yyy #zzz"
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    )
    return response.choices[0].message.content.strip()

st.title("📸 InspoGen - openai v1 版本 IG 貼文產生器")

topic = st.text_input("輸入貼文主題（如：咖啡廳）")
style = st.selectbox("選擇貼文文風", ["療癒", "搞笑", "文青", "極簡"])
img_style = st.selectbox("選擇圖片風格", ["自然", "插畫", "極簡", "復古"])
tts_speed = st.selectbox("語音語速選擇", ["正常", "稍慢", "快速"])

if st.button("產生貼文內容") and topic:
    caption = generate_caption(topic, style)
    hashtags = generate_hashtags(topic)

    st.markdown("### 📄 IG 貼文內容")
    st.write(caption)
    st.markdown("### 🔖 Hashtag 推薦")
    st.write(hashtags)

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

    desc = f"這張圖片呈現了「{topic}」的情境，帶有{style}風格與 {img_style} 視覺效果。"
    voice_text = desc + " 接著是貼文內容：" + caption
    tts = gTTS(voice_text, lang="zh", slow=True if tts_speed == "稍慢" else False)
    tts.save("voice.mp3")
    st.audio("voice.mp3")

    with open("voice.mp3", "rb") as audio_file:
        st.download_button("⬇️ 下載語音", audio_file, file_name="voice.mp3")
