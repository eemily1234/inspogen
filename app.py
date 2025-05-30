return response.choices[0].message.content.strip()

st.title("📸 InspoGen - IG 貼文產生器")

# 使用者輸入
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

    # 圖片處理（風格轉英文、備用圖）
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

    # 語音生成
    desc = f"這張圖片呈現了「{topic}」的情境，帶有{style}風格與 {img_style} 視覺效果。"
    voice_text = desc + " 接著是貼文內容：" + caption
    tts = gTTS(voice_text, lang="zh", slow=True if tts_speed == "稍慢" else False)
    tts.save("voice.mp3")
    st.audio("voice.mp3")

    with open("voice.mp3", "rb") as audio_file:
        st.download_button("⬇️ 下載語音", audio_file, file_name="voice.mp3")






