import streamlit as st
from gtts import gTTS
import requests

st.set_page_config(page_title="InspoGen - Unsplash 圖片版", page_icon="🌄")
st.title("🌄 InspoGen - 穩定圖片版本（Unsplash）")

# Step 1: IG 貼文文字生成
st.header("✏️ Step 1：貼文內容生成")
topic = st.text_input("請輸入主題（如：咖啡廳）")
style = st.selectbox("選擇文風", ["療癒", "搞笑", "文青", "極簡"])

caption = ""
if st.button("🔮 產生貼文內容"):
    prompt = f"請用{style}的語氣，為「{topic}」寫一段 IG 貼文，60字內，加上 emoji。"
    try:
        res = requests.post(
            "https://api-inference.huggingface.co/models/google/flan-t5-base",
            headers={"Content-Type": "application/json"},
            json={"inputs": prompt},
            timeout=20
        )
        result = res.json()
        if isinstance(result, list) and "generated_text" in result[0]:
            caption = result[0]["generated_text"].strip()
        else:
            caption = f"這是一段模擬的 {style} 貼文 ✨ 主題是「{topic}」"
    except:
        caption = f"這是一段模擬的 {style} 貼文 ✨ 主題是「{topic}」"
    st.success("✅ 貼文完成")
    st.write(caption)

    # 語音產出
    tts = gTTS(caption, lang="zh")
    tts.save("voice.mp3")
    st.audio("voice.mp3")

# Step 2: 顯示圖片（Unsplash 模擬）
st.header("🖼️ Step 2：主題圖片建議")
if topic:
    unsplash_url = f"https://source.unsplash.com/600x400/?{topic},aesthetic"
    st.image(unsplash_url, caption=f"與「{topic}」相關的圖片（來自 Unsplash）")
else:
    st.info("請先輸入主題，我們會顯示相關主題圖片 🌿")
