# Line 記帳設定指南（P2）

## 它能做什麼
老闆在 Line 官方帳號傳訊息就能記帳，機器人會回覆確認：

| 傳送 | 效果 |
|---|---|
| `支出 午餐 120` | 記一筆支出（金額在前或在後都可以） |
| `進貨 魚油 10 4000 A1 2027-01-01` | 品名 數量 總金額 [批號] [效期]：庫存 +10、記進貨成本 $4000；保健品批號與效期必填 |
| `庫存` / `庫存 魚油` | 全部庫存（含低庫存、30 天內到期）／單一品項與批次 |
| `今日` / `本月` | 收入、進貨成本、支出、毛利、現金淨額 |
| `復原` | 撤銷上一筆記帳（進貨若該批已有賣出則不能復原，需改用「盤點／報廢」） |
| `說明` | 顯示指令列表 |

安全規則：
- 只有**綁定過的老闆帳號**能記帳；其他人只會收到綁定說明，看不到任何資料。
- 店員不能綁定（店員本來就看不到帳務）。
- Line 重送同一則訊息時只處理一次，不會重複記帳。

## 設定步驟
1. 到 [Line Developers](https://developers.line.biz/) 建立 Provider → 建立 **Messaging API** channel（會同時產生 Line 官方帳號）。
2. 在 channel 取得：
   - **Channel secret**（Basic settings）
   - **Channel access token (long-lived)**（Messaging API 頁，按 Issue）
3. 設定環境變數後啟動 webhook 伺服器（需與 `ops_app.py` 使用同一個 `OPS_DB`）：
   ```
   export LINE_CHANNEL_SECRET=...
   export LINE_CHANNEL_ACCESS_TOKEN=...
   export OPS_DB=/path/to/ops.db
   pip install -r requirements.txt
   uvicorn line_server:app --host 0.0.0.0 --port 8000
   ```
> 若用 `docs/deploy.md` 的部署方式，webhook 網址是 `https://你的網域/line/webhook`（Line 與 Uber 共用一個入口 `webhook_app.py`），憑證寫在 `.env`，不必手動執行下面的 uvicorn。

4. Line 要求 webhook 是**公開的 HTTPS 網址**。測試可用 `cloudflared tunnel --url http://localhost:8000` 或 ngrok；正式使用建議部署到雲端主機。
5. 在 Messaging API 頁填 Webhook URL：`https://你的網址/webhook`，開啟 **Use webhook**，並在 Line Official Account Manager 關閉「自動回應訊息」。
6. 綁定：登入 `ops_app.py`（老闆）→「使用者」頁 →「產生 Line 綁定碼」→ 用手機加官方帳號好友，傳 `綁定 123456`。

## 注意
- 憑證（secret、token）只放環境變數，不要寫進程式或上傳到 GitHub。
- SQLite 同時被網頁與 webhook 讀寫，4 人小店的量沒問題；之後成長再換 PostgreSQL。
- 開發環境無法連到 Line，因此簽章驗證、指令解析、重送去重都以自動測試驗證，**實際收發訊息需申請 channel 後試一次**。
