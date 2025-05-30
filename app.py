import streamlit as st
from gtts import gTTS
import openai


app = streamlit(__name__)

@app.route('/')
def home():
    return jsonify({'message': 'Hello, World!'})

if __name__ == '__main__':
    app.run(debug=True)
import streamlit as st
from gtts import gTTS
import openai

openai.api_key = "你的 OpenAI API 金鑰"

st.title("📸 InspoGen - IG 多模態貼文產生器")

topic = st.text_input("輸入貼文主題（如：日常咖啡廳）")
style = st.selectbox("選擇文風", ["療癒", "搞笑", "文青", "極簡"])

if st.button("產生貼文內容"):
    prompt = f"以{style}風格寫一篇關於「{topic}」的 IG 貼文，約80字，加入 emoji"
    response = openai.ChatCompletion.create(
        model="gpt-3.5-turbo",
        messages=[{"role": "user", "content": prompt}]
    )
    post = response.choices[0].message.content
    st.markdown("### 📄 產生結果：")
    st.write(post)

    # 語音
    tts = gTTS(text=post, lang="zh")
    tts.save("voice.mp3")
    st.audio("voice.mp3", format="audio/mp3")

    # 圖片模擬
    st.image("https://source.unsplash.com/400x300/?coffee,city", caption="示意圖")

