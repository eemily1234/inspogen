# 部署指南：讓系統不用靠一台電腦一直開著

目標：系統放在一台**一直開著的主機**上，平板、手機用網址就能連，Line 與 Uber Eats 的通知也能送進來。

> 先說清楚我驗證了什麼、沒驗證什麼：
> - **已驗證**：程式本身（用自動測試與真實瀏覽器）——包含後台放在 `/admin` 路徑、webhook 驗簽、裝置登入連結、登入鎖定、每日備份；
>   `docker-compose.yml` 的格式也用 `docker compose config` 檢查通過。
> - **沒驗證**：我的開發環境雖有 Docker 用戶端，但**沒有執行中的 Docker 服務**，所以**沒有真的建置映像檔或啟動容器**；
>   Caddy 的設定與自動 HTTPS 也沒有實際跑過。這一層是依標準做法寫的，
>   **第一次上線請預留時間照著步驟試，遇到問題把錯誤訊息貼給我**。

## 您需要準備

| 項目 | 說明 |
|---|---|
| 一台主機 | 一台常開的 Linux 主機（雲端小主機 VPS，或店裡的迷你電腦）。這個系統很輕量，最小規格（約 1 核心、1 GB 記憶體）應該夠用，實際以您試跑為準 |
| 網域 | 一個網址，例如 `shop.example.com`，DNS 的 A 記錄指向主機的對外 IP（HTTPS 憑證會自動申請） |
| 開放連接埠 | 主機要開 80 與 443 |

> **沒有固定對外 IP 的店內主機**（例如店裡電腦）：可改用 Cloudflare Tunnel 之類的方式把網址轉進來；
> 這種做法 Caddy 那一層要調整，需要的話告訴我，我再改設定檔。

## 步驟（Ubuntu 主機）

**1. 安裝 Docker**（照 Docker 官方文件安裝 Docker Engine 與 compose 外掛）

**2. 下載程式**
```
git clone https://github.com/eemily1234/inspogen.git
cd inspogen
git checkout claude/operations-system-design-t48agn
```

**3. 設定 `.env`**
```
cp .env.example .env
nano .env
```
至少要填：
- `DOMAIN`：您的網域
- `ADMIN_PASSWORD_HASH`：後台的第二道密碼。產生方式：
  ```
  docker run --rm caddy:2 caddy hash-password --plaintext '你的長密碼'
  ```
  把輸出整串貼進去，**前後一定要加單引號**。
- `OPS_MIN_PIN_LENGTH=6`：新帳號 PIN 至少 6 位（已預設）

**4. 啟動**
```
docker compose up -d --build
docker compose ps
```
**5. 開啟網站**
- 餐點系統（平板、手機）：`https://你的網域/`
- 營運後台：`https://你的網域/admin`（先輸入 Caddy 的帳密，再用系統帳號登入）
- 第一次開啟會要求建立老闆帳號。

## 平板免輸入 PIN：裝置登入連結

廚房與服務生的平板每次重新整理都要重新登入，很不方便。老闆可以替**店員或廚房帳號**產生「裝置登入連結」：

1. 後台 →「使用者」→ 最下面「裝置登入連結」→ 選帳號、取名（例：廚房平板）、填餐點系統網址 → 產生。
2. 把顯示的連結（`https://你的網域/?device=…`）在平板上打開，存成書籤或「加入主畫面」。以後點一下就自動登入。
3. 連結**等於密碼**，只顯示一次；平板遺失或換人時，在同一頁按「撤銷」立刻失效。
4. 老闆帳號不能使用裝置連結。

## 登入安全（已內建）

- 同一姓名 10 分鐘內輸入錯誤 5 次，或所有姓名合計錯誤 30 次，就鎖定 10 分鐘（鎖定期間連正確的 PIN 也不接受）。
- 營運後台有兩層：Caddy 密碼 + 系統登入；廚房帳號不能進後台。
- 全程 HTTPS，憑證自動申請與更新。
- Line、Uber 的 webhook 不加密碼，但每一筆都會驗證對方的簽章，簽章不對一律拒絕。
- 程式以非 root 使用者執行；只有 Caddy 對外開放連接埠。
- **這不是銀行等級的安全**：PIN 是簡單密碼。建議老闆 PIN 設 6 位以上、不要與其他密碼相同、不要讓店員知道。

## 備份與還原

- `backup` 服務每天凌晨約 3:30 把資料庫備份到主機上的 `./backups/`（用 SQLite 內建備份，營業中也一致），保留 30 天，並檢查備份檔完整。
- **備份放在同一台主機上，主機壞掉會一起沒有。** 請定期把 `backups/` 資料夾複製到別處（例如雲端硬碟，或主機商的快照功能）。
- 還原：
  ```
  docker compose down
  # 把要還原的備份檔（例如 backups/ops-2026-11-04.db）複製進資料卷：
  docker run --rm -v inspogen_ops-data:/data -v "$PWD/backups":/b alpine cp /b/ops-2026-11-04.db /data/ops.db
  docker compose up -d
  ```
  （資料卷名稱前綴依資料夾名稱而定，可用 `docker volume ls` 確認。）

## 更新程式
```
git pull
docker compose up -d --build
```
資料庫會自動升級，舊資料保留。建議更新前先手動備份一次：`docker compose exec backup python deploy/backup.py --once`。

## 常用指令
```
docker compose logs -f meal        # 看餐點系統的紀錄（也可換成 admin / hooks / caddy / backup）
docker compose restart meal
docker compose down                # 停止（資料不會消失）
```

## Webhook 網址（填到 Line / Uber 後台）
- Line：`https://你的網域/line/webhook`（設定見 `docs/line-setup.md`）
- Uber Eats：`https://你的網域/uber/webhook`（設定見 `docs/uber-setup.md`）
