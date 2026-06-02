# MAX Exchange Trading Bot

本專案提供基於 MAX 交易所 API 的 BTC 雙邊滾動網格自動化交易系統，支援 USDT 與 TWD 市場。本系統已重構為模組化多代理架構，強調交易穩定性、安全性與自動化風控。

## 系統架構說明
本專案採用多代理架構 (Multi-agent architecture)，透過分工確保交易執行與監控的解耦與穩定：
- `engine.py`: 核心交易引擎。負責調度各模組，並透過 `GridDatabaseService` 進行 SQLite 持久化管理，確保重啟後無縫銜接。
- `api-worker`: 專責與 MAX API 互動，處理掛單、撤單與即時狀態同步。
- `log-manager`: 統籌日誌紀錄與輪替 (RotatingFileHandler)，確保系統運作軌跡可追溯。
- `market-monitor`: 持續監控市場價格與波動，並即時更新「邏輯真空 (Logical Vacuum)」監控 Dashboard，供使用者評估掛單效率。

## 主要檔案說明
- `engine.py`: 核心引擎，內含 `RollingGridLeg` 執行單元。
    - `_validate_startup_config`: 啟動前置檢查，對網格範圍、步長、名目價值與觸發價進行嚴格邏輯驗證。
- `analyzer.py`: 市場分析工具，基於 MA20/50/100 提供多空趨勢判斷。
- `grid_state.db`: 自動生成的 SQLite 資料庫，儲存網格狀態與市場快照。

## 環境設定 (`.env`)
請建立 `.env` 檔案並設定您的 API 金鑰與網格策略參數。請確保 `.env` 已加入 `.gitignore` 且**絕不提交至 Git 版本控制系統**。

### 參數說明清單
| 參數類別 | 參數名稱 | 說明 |
| :--- | :--- | :--- |
| **交易所認證** | `MAX_ACCESS_KEY`, `MAX_SECRET_KEY` | 您的 API 金鑰 |
| **Telegram 通知** | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` | 通知機器人 Token 與頻道 ID |
| **運行模式** | `DRY_RUN` | `True` (模擬) 或 `False` (實盤) |
| **初始資產** | `DRY_RUN_INITIAL_USDT/BTC/TWD` | 模擬模式下的初始餘額，設置 0.0 會自動以實際錢包蓋過 |
| **網格核心** | `GRID_STEP` | 網格間距 (USDT) |
| **下單金額** | `BUY_ORDER_AMOUNT`, `SELL_ORDER_AMOUNT` | 買單與賣單各自的單筆掛單名目金額 (USDT) |
| **市場設定** | `BUY_MARKET`, `SELL_MARKET` | 指定交易對 (如 btcusdt) |
| **網格邊界** | `BUY_GRID_UPPER/LOWER`, `SELL_GRID_UPPER/LOWER` | 買賣單的價格區間邊界 |
| **交易精度** | `DECIMALS_BTC_USDT_PRICE`, `DECIMALS_BTC_TWD_PRICE` | 價格小數點精度設定 |
| **移動平均線** | `MA50_ENABLED`, `MA50_KLINE_PERIOD`, `MA50_LENGTH` | MA50 趨勢檢查開關、週期與均線長度 |
| **風控與費用** | `SPIKE_THRESHOLD_USDT`, `BAD_DATA_THRESHOLD_PCT` | 波動熔斷、髒數據過濾閾值 |
| **費用與延遲** | `FEE_RATE_MAX_TOKEN`, `POST_ONLY_RETRY_COOLDOWN` | 交易手續費率、掛單失敗重試冷卻秒數 |

## 核心功能執行
1. **啟動網格機器人**:
   ```bash
   python3 engine.py
   ```
2. **市場趨勢分析**:
   ```bash
   python3 analyzer.py
   ```
*建議使用 `launchd` 將 `engine.py` 設定為背景常駐服務，以確保系統長期穩定運行。*

## 系統特性
- **優雅關閉機制**: 內建 `try-finally` 處理，偵測到 `Ctrl+C` 時自動呼叫 API 取消所有掛單並安全關閉資料庫，防止掛單殘留。
- **持久化設計**: 重啟後自動從 `grid_state.db` 恢復掛單與市場狀態。
- **隔離安全**: 透過 `DRY_RUN` 變數自動區隔模擬與實盤環境，模擬模式下初始資產設置為 0.0 時會與實際錢包同步。
- **邏輯真空監控**: 即時監控市場價格與網格邊界間的邏輯真空狀態。
- **保護機制**: 具備 API 波動熔斷、臟數據過濾與啟動前配置檢查。
