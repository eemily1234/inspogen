import streamlit as st
from gtts import gTTS
import requests
from PIL import Image
from io import BytesIO
import base64

st.set_page_config(page_title="InspoGen 一頁式 AI 貼文產生器", page_icon="🌟")
st.title("🌟 InspoGen - 一頁式 AI 多模態貼文產生器")

# --- 主題選擇區塊 ---
st.header("📝 Step 1：輸入主題與風格")
topic = st.selectbox("選擇主題", ["咖啡廳", "旅行", "閱讀", "穿搭", "日常生活"])
style = st.selectbox("選擇文風", ["療癒", "搞笑", "文青", "極簡"])
tts_speed = st.selectbox("語音語速", ["正常", "稍慢", "快速"])

# --- 產文區塊 ---
st.subheader("✏️ IG 貼文內容")
caption = ""
if st.button("🔮 產生 IG 貼文內容"):
    prompt = f"請用{style}的語氣，為「{topic}」寫一段大約60字的IG貼文，加入 emoji。"
    try:
        with st.spinner("AI 生成中，請稍候..."):
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
                caption = f"這是一段模擬的 {style} 風格 IG 貼文，主題是「{topic}」 ✨📷"
    except:
        caption = f"這是一段模擬的 {style} 風格 IG 貼文，主題是「{topic}」 ✨📷"
    st.success("✅ 產生完成")
    st.write(caption)

    # 語音
    tts = gTTS(
        f"這段貼文主題是「{topic}」，文風是 {style}，內容是：" + caption,
        lang="zh",
        slow=True if tts_speed == "稍慢" else False
    )
    tts.save("voice.mp3")
    st.audio("voice.mp3")
    with open("voice.mp3", "rb") as f:
        st.download_button("⬇️ 下載語音", f, file_name="voice.mp3")

# --- 產圖區塊 ---
st.header("🖼️ Step 2：輸入描述產生配圖")
desc = st.text_input("請描述你想要的圖片風格（例如：可愛插畫風的下午茶場景）")

if st.button("🎨 生成配圖") and desc:
    st.info("圖片生成中，請稍候約 10～15 秒...")
    try:
        response = requests.post(
            "https://hf.space/embed/lambdal/text-to-image/api/predict",
            json={"data": [desc]},
            timeout=30
        )
        output = response.json()
        if output and "data" in output and len(output["data"]) > 0:
            base64_img = output["data"][0].split(",")[1]
            img_data = base64.b64decode(base64_img)
            image = Image.open(BytesIO(img_data))
            st.image(image, caption="✨ AI 生成圖片")
            st.success("✅ 生成完成，可右鍵另存圖片")
        else:
            st.error("⚠️ 圖片產生失敗，請更換描述內容再試")
    except:
        st.error("❌ 發生錯誤，請稍後重試")
