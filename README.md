# MAX Exchange Trading Bot

本專案提供基於 MAX 交易所 API 的 BTC 雙邊滾動網格自動化交易系統，支援 USDT 與 TWD 市場。專案已完成全面重構，導入模組化架構，強調交易穩定性、安全性、代碼複用性（DRY）與自動化風控。

---

## 🏗️ 系統架構說明

專案採用高内聚、低耦合的模組化設計，拆分為以下核心模組：

- [config.py](file:///Users/nangei/max-api/config.py): **配置管理模組**，載入 `.env` 環境變數並驗證參數類型。
- [db.py](file:///Users/nangei/max-api/db.py): **資料庫持久化模組**，負責 SQLite 資料庫初始化、網格狀態同步（sync slots）與市場快照（market snapshots）紀錄。
- [logger.py](file:///Users/nangei/max-api/logger.py): **日誌與通知模組**，提供檔案自動輪轉日誌紀錄（`GridLogger`）與 Telegram 異動通知發送。
- [exchange.py](file:///Users/nangei/max-api/exchange.py): **交易所 API 客戶端**，封裝與 MAX 交易所的 HTTP 溝通（含 HMAC-SHA256 簽名簽章與 Dry-Run 模擬機制）。
- [models.py](file:///Users/nangei/max-api/models.py): **資料結構與格式化工具**，定義 `GridLegSpec` 與通用計價格式化輔助函數。
- [grid_leg.py](file:///Users/nangei/max-api/grid_leg.py): **網格策略邏輯**，實現單邊網格的滾動（Rolling Grid）核心演算法。
- [engine.py](file:///Users/nangei/max-api/engine.py): **主引擎調度器**，負責協調買賣雙邊網格、定時撈取行情、風控熔斷判定，並渲染終端機 UI 儀表板。

---

## 📂 主要檔案清單

- [engine.py](file:///Users/nangei/max-api/engine.py): 主常駐程序，內含 `DualGridEngine` 控制核心與 Dashboard。
- [analyzer.py](file:///Users/nangei/max-api/analyzer.py): 市場趨勢分析工具，基於 MA20/50/100 均線排列提供多空趨勢判斷與操作啟示。
- [balance_monitor.py](file:///Users/nangei/max-api/balance_monitor.py): 餘額監控腳本，定時查詢並將餘額持久化至 SQLite 資料庫。
- [order.py](file:///Users/nangei/max-api/order.py): CLI 測試下單工具，已重構為 async 架構並複用核心簽名邏輯。
- [wallet.py](file:///Users/nangei/max-api/wallet.py): CLI 錢包餘額查詢工具，已重構為 async 架構並複用核心簽名邏輯。
- `grid_state_dryrun.db` / `grid_state_live.db`: 自動生成的 SQLite 資料庫，儲存網格狀態、餘額歷史與市場快照。

---

## ⚙️ 環境設定 (`.env`)

請建立 `.env` 檔案並設定您的 API 金鑰與網格策略參數。請確保 `.env` 已加入 `.gitignore` 且**絕不提交至公開的 Git 倉庫**。

### 參數說明清單
| 參數類別 | 參數名稱 | 說明 |
| :--- | :--- | :--- |
| **交易所認證** | `MAX_ACCESS_KEY`, `MAX_SECRET_KEY` | 您的 Maicoin MAX API 金鑰與金鑰密碼 |
| **Telegram 通知** | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` | 通知機器人 Token 與頻道 ID |
| **運行模式** | `DRY_RUN` | `True` (模擬測試) 或 `False` (實盤下單) |
| **初始資產** | `DRY_RUN_INITIAL_USDT/BTC/TWD` | 模擬模式下的初始資產配置 |
| **網格核心** | `GRID_STEP` | 買賣共用的網格間距 (USDT) |
| **下單金額** | `BUY_ORDER_AMOUNT`, `SELL_ORDER_AMOUNT` | 買單與賣單各自的單筆掛單名目金額 (USDT) |
| **市場設定** | `BUY_MARKET`, `SELL_MARKET` | 指定交易對 (如 `btcusdt` 或 `btctwd`) |
| **網格邊界** | `BUY_GRID_UPPER/LOWER`, `SELL_GRID_UPPER/LOWER` | 買賣單的價格區間邊界 |
| **交易精度** | `DECIMALS_BTC_USDT_PRICE`, `DECIMALS_BTC_TWD_PRICE` | 價格小數點精度設定 |
| **移動平均線** | `MA50_ENABLED`, `MA50_KLINE_PERIOD`, `MA50_LENGTH` | MA50 趨勢檢查開關、週期與均線長度 |
| **風控與費用** | `SPIKE_THRESHOLD_USDT`, `BAD_DATA_THRESHOLD_PCT` | 波動熔斷、髒數據過濾閾值 |
| **費用與延遲** | `FEE_RATE_MAX_TOKEN`, `POST_ONLY_RETRY_COOLDOWN` | 交易手續費率、掛單失敗重試冷卻秒數 |
| **成本價格** | `COST_PRICE` | analyzer.py 中成本價比對 |

---

## 🚀 功能執行說明

1. **啟動網格交易機器人**:
   ```bash
   python3 engine.py
   ```
2. **手動市場趨勢分析**:
   ```bash
   python3 analyzer.py
   ```
3. **單次紀錄現貨錢包餘額**:
   ```bash
   python3 balance_monitor.py
   ```
4. **測試 API 下單**:
   ```bash
   python3 order.py
   ```
5. **手動查詢餘額**:
   ```bash
   python3 wallet.py
   ```

---

## 🛡️ 系統特性

- **優雅關閉機制 (Graceful Shutdown)**: 內建 `try-finally` 與信號處理，偵測到 `Ctrl+C` 時自動呼叫 API 取消所有掛單並安全關閉資料庫，防止掛單殘留。
- **資料庫持久化設計**: 重啟後自動從 `grid_state.db` 恢復網格插槽狀態與掛單進度，無縫接軌。
- **物理隔離安全**: 透過 `DRY_RUN` 變數自動區隔模擬（`grid_state_dryrun.db`）與實盤（`grid_state_live.db`）資料庫與日誌檔案，保證數據互不污染。
- **保護與熔斷機制**: 具備即時價格尖峰波動熔斷（Circuit Breaker）、髒數據過濾與啟動前配置完整性校驗。
