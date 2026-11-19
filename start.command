#!/bin/bash
# 營運系統一鍵啟動（Mac：在 Finder 裡點兩下這個檔案即可）
# 會做的事：更新程式 → 準備 Python 環境 → 檢查資料庫 → 開啟餐點系統與營運後台
# 關閉：在這個終端機視窗按 Ctrl + C（兩個系統會一起關掉）

cd "$(dirname "$0")" || exit 1
echo "== 營運系統啟動中（資料夾：$(pwd)）=="

echo "→ 更新程式"
git pull -q 2>/dev/null || echo "  （更新略過，沿用目前的版本）"

if [ ! -d .venv ]; then
  echo "→ 第一次使用：建立 Python 環境"
  python3 -m venv .venv || { echo "建立 Python 環境失敗，請確認已安裝 Python 3.10 以上"; read -r -p "按 Enter 關閉"; exit 1; }
fi
source .venv/bin/activate
echo "→ 檢查套件"
python -m pip install -q --upgrade pip >/dev/null 2>&1
pip install -q -r requirements.txt || { echo "安裝套件失敗，請把上面的錯誤訊息貼給 Claude"; read -r -p "按 Enter 關閉"; exit 1; }

echo "→ 檢查資料庫"
python - <<'PY'
import os, time
from ops import db
path = db.DB_PATH
if not os.path.exists(path):
    print("  尚未建立資料庫：開啟後台時會請你建立老闆帳號")
else:
    try:
        conn = db.connect()
        n = conn.execute("SELECT COUNT(*) FROM user").fetchone()[0]
        conn.close()
        print(f"  資料庫正常（{n} 個帳號）")
    except Exception as e:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        backup = path.replace("ops.db", f"ops-無法讀取-{stamp}.db")
        for suffix in ("", "-wal", "-shm"):
            if os.path.exists(path + suffix):
                os.rename(path + suffix, backup + suffix)
        print(f"  ⚠️ 原本的資料庫無法讀取（{type(e).__name__}: {e}）")
        print(f"  已改名保留為：{os.path.basename(backup)}，系統會用新的空白資料庫啟動")
        print("  開啟後台時會請你建立新的老闆帳號；舊檔案若需要救回，請告訴 Claude")
PY

IP=$(ipconfig getifaddr en0 2>/dev/null)
echo ""
echo "== 已啟動 =="
echo "  營運後台：      http://localhost:8501"
echo "  餐點點餐系統：  http://localhost:8502"
[ -n "$IP" ] && echo "  平板（同一個 Wi-Fi）：http://$IP:8502"
echo "  關閉：在這個視窗按 Ctrl + C"
echo ""

trap 'kill 0' EXIT
streamlit run meal_app.py --server.port 8502 --server.headless true &
streamlit run ops_app.py --server.port 8501
