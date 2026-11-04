# 營運系統設計（v4，依清晰版組織圖＋已確認事項）

> v2 以第二張清晰組織圖為主架構；第一張草圖的概念（COP 核心、錢貨一起、Line 記帳、每週貼文）整合進各模組。
> **已確認**：第三框＝「營運」（含保健品、有機商品、餐點）；最右框＝「數位」（POS、內部同步、進／銷貨）；Uber 指 Uber Eats，需**自動串接**；業務**零售＋餐飲**皆有；**4 人**使用；**需要電子發票**。
> **第二輪決議**：餐點**不建立配方**（只記收入，不扣食材庫存）；保健品**要批號與效期**；POS 用**平板與手機**；店員**看不到成本與報表**；電子發票服務商與 Uber Eats 串接**先略過**。
> 第 8 節為決議摘要。

## 1. 組織圖解讀

公司下分六大模組：

| 模組 | 圖上內容 | 系統功能 |
|---|---|---|
| **帳務** | 所有成本、收入、支出、報表 | 總帳、成本／收入／支出登錄、各式報表 |
| **庫存** | POS＋進銷貨；即時存貨／預警 | 即時存貨、低庫存預警，由 POS 與進銷貨驅動 |
| **營運** | 保健品、有機商品、餐點 | 商品與餐點主檔；餐點不建配方，只記收入；保健品記批號與效期 |
| **行銷** | Line、Fb、Uber、Google Maps | 五個通路的貼文／上架／評論管理 |
| **客服** | Tel ＋ Line | 電話與 Line 客人問題紀錄、回覆、FAQ |
| **數位** | POS、內部同步、進／銷貨 | 數位化核心：POS、各系統內部同步、進銷貨單、電子發票、Uber Eats 串接 |

> 第一張圖的 COP 在這張圖對應「公司」這一層：六個模組共用同一份資料，COP = 共用的資料核心。

## 2. 整體架構

```mermaid
flowchart TB
    CO[公司 / COP 資料核心]
    CO --> ACC[帳務]
    CO --> INV[庫存]
    CO --> PRD[營運]
    CO --> MKT[行銷]
    CO --> CS[客服]
    CO --> STORE[數位：POS / 同步 / 進銷貨]

    PRD --> P1[保健品]
    PRD --> P2[有機商品]
    PRD --> P3[餐點]
    MKT --> M1[Line]
    MKT --> M2[Fb]
    MKT --> M3[Uber Eats]
    MKT --> M4[Google Maps]
    CS --> C1[電話 Tel]
    CS --> C2[Line]

    STORE -- "銷貨：同時扣庫存、記收入" --> INV
    STORE --> ACC
    STORE --> EINV[電子發票]
    M3 -- "訂單自動進入" --> STORE
    INV -- "進貨：記成本" --> ACC
    INV -- "預警" --> ALERT[低庫存通知]
    ACC --> RPT[報表]
```

設計原則：
1. **一份資料**：商品、客戶、庫存、帳只存在 COP，各模組共用。
2. **錢貨同步**：POS 賣一筆 = 一筆收入（帳務）＋ 一筆庫存異動，同一個資料庫交易完成。進貨 = 一筆成本 ＋ 庫存增加。
3. **即時**：庫存隨交易立刻變動，達到門檻立刻預警。

## 3. 各模組設計

### 3.1 帳務
- 登錄類型：**收入、成本、支出**（對應圖上「所有成本、收入、支出」）。
- 來源：POS 自動、進貨自動、手動輸入、**Line 記帳**（傳一句話即記，機器人回覆確認；例：`支出 午餐 120`、`進貨 有機糙米 10包 2300`）。
- 報表：日／週／月損益、毛利、依商品分類（保健／有機）與通路分析、匯出 CSV 給會計。

### 3.2 庫存
- 存貨量 = 所有異動加總。異動只新增不修改：進貨、銷貨、退貨、盤點、報廢。
- **即時存貨**：每個商品、每個據點的當前數量。
- **預警**：低於安全庫存時通知（Line／畫面紅字）；可延伸「預估可售天數」。
- 進銷貨單：進貨單（供應商、成本）、銷貨由 POS 自動產生。

### 3.3 營運（商品與餐點）
- 品項三類：**保健品、有機商品**（實體商品，直接扣庫存）、**餐點**（現做，不直接有庫存）。
- 餐點**不建立配方**：賣出只記收入（可填單份成本供毛利參考），**不扣庫存**。日後要追食材成本再加配方功能。
- 保健品**必須記批號與有效期限**，銷貨依「效期最近者先出」（FEFO）扣庫存，並提示即將到期品項。
- 保健品建議加批號與有效期限（❓視需求）。
- 各通路可售狀態：品項是否在 Uber Eats、Line 上架。

### 3.4 行銷
- 通路：Line、Fb、Uber、Google Maps。
- 功能：
  - 貼文／活動排程（Fb、Line；建議每週 1–2 篇，沿用第一張草圖）。
  - **Uber Eats（自動串接）**：菜單與上架狀態同步、訂單自動進入 POS（見 3.6）。
  - Google Maps：商家資訊、評論回覆追蹤。
- 每個客人與訂單記錄來源通路，可比較各通路成效。

### 3.5 客服
- 管道：電話（Tel）＋ Line。
- 每筆客人問題記錄：客人、管道、內容、標籤、是否已回覆。
- 彙整成 FAQ，可回饋給行銷（貼文題材）與 Line 自動回覆。
- 電話只能手動登錄；Line 可由 webhook 自動進入收件匣。

### 3.6 數位（POS、同步、進銷貨、發票、串接）
- 門市 POS：選品／餐點 → 結帳 → 開立電子發票 → 完成。
- **內部同步**：結帳立刻同步庫存與帳務（圖上「POS 內…同步進／銷貨」）。
- **電子發票**：結帳時開立（可選載具、統編），作廢／折讓有對應流程。需選定發票服務商（❓）。
- **Uber Eats 串接**：接收訂單 → 自動建立 `sale`（來源=uber）→ 同樣扣庫存、記帳；同步菜單與可售狀態；平台抽成記為支出。需向 Uber 申請 API 權限，時程不由我們控制，故排在後期，之前可先手動登錄。
- 退貨／作廢：產生反向帳與反向庫存，不刪除。
- 目前以單一店面為前提，資料表保留 `store_id`，日後擴店不必改結構。

## 4. 資料模型

```
store(id, name)                                              -- 店面（目前 1 間）
customer(id, name, phone, line_id, fb_id, source_channel)
inquiry(id, customer_id, channel[tel|line], content, tag, replied, replied_at)  -- 客服 / FAQ
post(id, channel[line|fb|uber|gmaps], topic, scheduled_date, status)            -- 行銷

item(id, name, type[product|meal], category[health|organic|meal], unit, price, cost, safety_stock, batch_tracking, active)
item_listing(item_id, channel, listed)                       -- 各通路可售狀態
user(id, name, role[owner|staff])                            -- 4 位使用者與權限
stock_move(id, store_id, item_id, qty_delta, move_type[purchase|sale|return|adjust|scrap], ref_type, ref_id, batch_no, expiry, created_by, created_at)

purchase(id, supplier, total, created_at); purchase_line(...)
sale(id, store_id, customer_id, channel[pos|uber|line], total, pay_method, status[done|void], invoice_no, external_order_id, created_at); sale_line(...)
invoice(id, sale_id, number, carrier, tax_id, status[issued|void], issued_at)

ledger(id, date, kind[income|cost|expense], amount, item_id?, store_id?, ref_type, ref_id, source[pos|purchase|line|manual], note, voucher_url)
```

規則：
- `sale` / `purchase` 建立時，同一交易內寫入 `ledger` 與 `stock_move`；餐點只寫 `ledger`，不寫 `stock_move`。
- `stock_move` 只新增；更正用反向異動。
- 現有庫存 = `SUM(qty_delta)`，按 `store_id, item_id` 分組。

## 5. 主要流程

- **銷貨**：POS 結帳 → 寫 `sale` + `ledger(收入)` + `stock_move(−)`（餐點不扣庫存；保健品 FEFO）→（日後）開電子發票 → 檢查預警。
- **Uber Eats 訂單（日後）**：平台推送訂單 → 自動建立 `sale` → 同上扣庫存、記帳、開票。
- **進貨**：建立進貨單（或 Line 傳「進貨 …」）→ `ledger(成本)` + `stock_move(+)`。
- **客服循環**：客人電話／Line 詢問 → 登錄與回覆 → 標籤 → FAQ → 行銷題材。
- **月結**：帳務報表匯出給會計。

## 6. 建議技術

| 層 | 建議 |
|---|---|
| 後台 / POS 畫面 | Streamlit 多頁（與現有 repo 一致） |
| 資料庫 | SQLite 起步 → PostgreSQL |
| Line 記帳 / 客服 webhook | FastAPI 小服務（Streamlit 不適合收 webhook） |
| 資料存取 | SQLAlchemy |

注意：現有 `app.py` 內有寫死的 Pixabay API 金鑰，建議改用環境變數並更換金鑰。

## 7. 分期規劃

| 階段 | 內容 | 完成標準 |
|---|---|---|
| **P1** 開發中 | 營運主檔（保健／有機／餐點）、庫存（批號效期＋預警）、POS（平板／手機，錢貨同步）、基本帳務、4 人帳號與權限 | 一天營業的銷售、庫存、帳對得起來 |
| **P2** | 進貨單強化、Line 記帳機器人（電子發票待服務商確定後加入） | 老闆用 Line 記帳與進貨 |
| **P3** | 客服收件匣＋FAQ、行銷排程（Line、Fb） | 客人問題與貼文都有紀錄 |
| **P4** | Uber Eats 自動串接、Google Maps 評論追蹤、報表強化 | Uber 訂單自動入帳扣庫存；看得到各通路毛利 |

> 電子發票排在 P2、Uber Eats 排在 P4：前者是法規需求且與 POS 緊密相關，後者依賴平台 API 核准。若 Uber 訂單量大，可把 P4 的 Uber 部分提前。

## 8. 決議摘要

| 項目 | 決議 |
|---|---|
| 電子發票服務商 | 先略過，之後處理（POS 預留發票欄位） |
| Uber Eats | 已有商家帳號；串接先略過 |
| 餐點配方 | 不建立 |
| 保健品批號／效期 | 要 |
| POS 裝置 | 平板、手機（介面需適合小螢幕） |
| 權限 | 老闆：全部；店員：POS 與庫存（不含成本、不含帳務報表） |

## 9. P1 實作說明

- 入口：`streamlit run ops_app.py`（既有的 `app.py` 不變）。
- 程式：`ops/db.py`（資料表）、`ops/services.py`（錢貨同步等商業規則）、`ops_app.py`（畫面）、`tests/test_services.py`。
- 資料庫：SQLite，預設 `ops.db`，可用環境變數 `OPS_DB` 指定路徑。第一次開啟會要求設定老闆 PIN。
- 金額與數量皆為整數（新台幣、件）。
