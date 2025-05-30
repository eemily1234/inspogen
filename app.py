import streamlit as st
from gtts import gTTS
import requests

HF_API_URL = "https://api-inference.huggingface.co/models/google/flan-t5-base"
HEADERS = {"Content-Type": "application/json"}

def generate_caption(topic, style):
    prompt = f"請用{style}的語氣，為「{topic}」寫一段大約60字的IG貼文，加入 emoji。"
    payload = {"inputs": prompt}
    try:
        with st.spinner("⏳ 生成中，請稍候..."):
            response = requests.post(HF_API_URL, headers=HEADERS, json=payload, timeout=20)
            result = response.json()
            if isinstance(result, list) and "generated_text" in result[0]:
                return result[0]["generated_text"].strip()
    except Exception as e:
        return None
    return None

st.set_page_config(page_title="InspoGen - flan 模型", page_icon="📸")
st.title("📸 InspoGen - flan-t5 穩定版")

topic = st.selectbox("選擇主題", ["咖啡廳", "旅行", "閱讀", "穿搭", "日常生活"])
style = st.selectbox("選擇文風", ["療癒", "搞笑", "文青", "極簡"])
img_style = st.selectbox("圖片風格", ["自然", "插畫", "極簡", "復古"])
tts_speed = st.selectbox("語音語速", ["正常", "稍慢", "快速"])

if st.button("產生貼文內容"):
    caption = generate_caption(topic, style)
    st.markdown("### 📄 IG 貼文內容")
    if caption:
        st.write(caption)
    else:
        st.error("⚠️ 無法產生貼文，伺服器忙碌或超時，請稍後重試。")
        caption = f"這是一段模擬的 {style} 風格 IG 貼文：主題是「{topic}」 🌟📷☕"

    style_map = {"自然": "natural", "插畫": "illustration", "極簡": "minimalist", "復古": "vintage"}
    topic_map = {
        "咖啡廳": "coffee shop", "穿搭": "fashion", "旅行": "travel",
        "閱讀": "reading", "日常生活": "daily lifestyle"
    }
    search_term = f"{topic_map.get(topic, 'lifestyle')},{style_map.get(img_style, 'aesthetic')}"
    img_url = f"https://source.unsplash.com/400x300/?{search_term}"

    try:
        st.image(img_url, caption=f"情境示意圖：{img_style}風")
    except:
        st.image("https://source.unsplash.com/400x300/?coffee", caption="☕ 預設示意圖")

    voice = f"這段貼文以 {style} 文風描寫 {topic}，畫面呈現 {img_style} 效果。內容如下：" + caption
    tts = gTTS(voice, lang="zh", slow=True if tts_speed == "稍慢" else False)
    tts.save("voice.mp3")
    st.audio("voice.mp3")
    with open("voice.mp3", "rb") as f:
        st.download_button("⬇️ 下載語音", f, file_name="voice.mp3")
