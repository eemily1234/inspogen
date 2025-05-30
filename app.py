import streamlit as st
from gtts import gTTS
import openai

# 替換成你的 OpenAI API 金鑰
openai.api_key = "你的 OpenAI API 金鑰"

st.title("📸 InspoGen - IG 多模態貼文產生器")

topic = st.text_input("輸入貼文主題（如：日常咖啡廳）")
style = st.selectbox("選擇文風", ["療癒", "搞笑", "文青", "極簡"])

if st.button("產生貼文內容"):
    # 建立 OpenAI Client
    client = openai.OpenAI()

    # 呼叫新版 Chat Completions API
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": f"以{style}風格寫一篇關於「{topic}」的 IG 貼文，約80字，加入 emoji"}]
    )
    post = response.choices[0].message.content
    st.markdown("### 📄 IG 貼文內容：")
    st.write(post)

    # 語音合成
    tts = gTTS(text=post, lang="zh")
    tts.save("voice.mp3")
    st.audio("voice.mp3", format="audio/mp3")

    # 圖片示意
    st.image("https://source.unsplash.com/400x300/?coffee,city", caption="情境示意圖")

