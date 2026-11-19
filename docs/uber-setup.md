# Uber Eats 訂單自動進廚房

## 它怎麼運作

```
顧客在 Uber Eats 下單
   → Uber 通知我們的網址（webhook）
   → 驗證簽章 → 立刻回 200（Uber 要求）
   → 取得訂單內容 → 建立訂單，直接出現在「廚房」看板與「外場」外帶區（🛵 Uber ＋短編號）
   → 自動向 Uber「接受」訂單
   廚房暫停接單時 → 自動向 Uber「拒絕」
廚房按「完成出餐」→ 通知 Uber 備餐完成
Uber 取消訂單 → 還沒開始做就自動取消；已開始做會在整單備註出現「⚠️ 平台已取消此單」交給人判斷
```

- 廚房、外場看到的 Uber 單，操作方式與店內外帶單一樣（接單、出餐、結帳）。
- **品項對應**：Uber 的品項會先用「記住的對應」，再用**品名完全相同**的餐點對上；對不上的不會漏單，會建立停售的暫存品項「[Uber] 品名」並在該道菜備註「未對應菜單，請核對品項與價格」。**建議把 Uber 菜單上的品名與店內菜單設成一樣。**
- 價格：對得上的品項一律用**店內菜單價格**記帳；暫存品項價格是 0，請手動核對。
- **結帳**：Uber 訂單用「結帳」頁，付款方式選「Uber Eats」。平台抽成請另外在後台記一筆支出（目前不會自動拆算）。
- **失敗不會默默漏掉**：任何處理失敗（連不上 Uber、資料格式不符…）都會記錄，並在外場與廚房頁顯示橘色警告「有 N 筆 Uber 訂單處理失敗」，請到 Uber 商家平板確認並手動補單。原始資料存在資料庫的 `external_event` 表，可用來除錯。

## ⚠️ 請務必先看：哪些是確認過的、哪些沒有

**確認過（依 Uber 官方說明）**
- Webhook 用 `X-Uber-Signature` 驗證：用 client secret 對請求內容做 HMAC-SHA256，結果為小寫十六進位。
- 收到後要回 HTTP 200 且內容為空；之後必須在 **11.5 分鐘內**接受或拒絕，否則訂單自動取消。
- `orders.notification` = 新訂單，`orders.failure` = 訂單取消。

**沒有確認（我在開發環境連不上 Uber 文件網站，也沒有您的憑證）**
- 取得訂單／接受／拒絕／備餐完成 這幾個 API 的**確切網址路徑**。程式裡的預設值是依印象寫的，**很可能需要校正**。
  可以用環境變數覆寫：`UBER_ORDER_PATH`、`UBER_ACCEPT_PATH`、`UBER_DENY_PATH`、`UBER_READY_PATH`、`UBER_API_BASE`、`UBER_TOKEN_URL`、`UBER_SCOPE`、`UBER_DENY_REASON`。
- 訂單 JSON 的欄位名稱（品項、數量、備註、加料）。程式的解析刻意寫得寬鬆（`ops/uber.py` 的 `parse_uber_order`），但**沒有用真實訂單驗證過**。
- **所以整套 Uber 串接，在用真實憑證與真實（或測試）訂單跑過一遍之前，請當作「尚未驗證」。** 自動測試只證明「在我假設的格式下」邏輯正確。

## 設定步驟

1. **取得 API 權限**：到 Uber 的開發者後台建立應用程式並申請 Eats 訂單相關的權限（需要 Uber 核准，時間不在我們控制內；有些店家會透過 Uber 認證的整合夥伴而不是自己申請）。取得 `client_id`、`client_secret`，並請 Uber 開啟「訂單通知 webhook」。
2. **填環境變數**（`.env`）：`UBER_CLIENT_ID`、`UBER_CLIENT_SECRET`。
3. **Webhook 網址**填 `https://你的網域/uber/webhook`（先完成 `docs/deploy.md`，需要公開 HTTPS）。
4. **試跑**：用 Uber 的測試／沙盒環境或一筆真實小額訂單。
5. **把第一筆真實事件校正好**：若建單失敗，看最近的處理紀錄：
   ```
   docker compose exec hooks python -c "import sqlite3;c=sqlite3.connect('/data/ops.db');[print(r) for r in c.execute('select received_at,status,note from external_event order by id desc limit 5')]"
   ```
   完整的原始內容在同一張表的 `payload` 欄位。**把那筆資料（遮掉個資）和錯誤貼給我，我來校正解析與 API 路徑。** 這是最快讓它真正可用的方法。

## 已知限制
- 尚無「品項對應」的管理畫面（目前靠品名相同自動對應）。
- 平台抽成、外送費、優惠不會自動入帳。
- 備餐完成通知失敗只會記錄，不影響店內作業。
- 同一個訂單被 Uber 重送通知時只會建一張單。
