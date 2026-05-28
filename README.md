# MAX Exchange Trading Bot

本專案提供基於 MAX 交易所 API 的 BTC 雙邊滾動網格自動化交易系統，支援 USDT 與 TWD 市場。

## 主要檔案說明
- `engine.py`: 核心交易引擎。
    - `GridDatabaseService`: 負責 SQLite 持久化狀態 (網格記憶與市場快照)，確保重啟後無縫銜接。
    - `RollingGridLeg`: 網格執行單元，負責監控市場價格、計算滾動掛單並執行 API 下單。
    - `_validate_startup_config`: 啟動前置檢查函數，會對網格範圍、步長、名目價值與觸發價進行邏輯驗證，防範錯誤設定。
- `analyzer.py`: 市場分析工具。基於均線 (MA20, MA50, MA100) 提供多空趨勢判斷與操作啟示。
- `MA.md`: 技術分析邏輯參考文件。
- `grid_state.db`: 自動生成的 SQLite 資料庫，儲存網格掛單狀態與市場快照。

## 環境設定 (`.env`)
請建立 `.env` 檔案並設定您的 API 金鑰與網格策略參數。請確保 `.env` 已加入 `.gitignore` 且**絕不提交至 Git 版本控制系統**。

### 參數說明清單
| 參數類別 | 參數名稱 | 說明 |
| :--- | :--- | :--- |
| **交易所認證** | `MAX_ACCESS_KEY`, `MAX_SECRET_KEY` | 您的 API 金鑰，請妥善保管 |
| **Telegram 通知** | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` | Telegram 通知機器人 Token 與頻道 ID |
| **運行模式** | `DRY_RUN` | 設定 `True` (模擬) 或 `False` (實盤) |
| **網格核心** | `GRID_STEP`, `ORDER_AMOUNT` | 網格間距與每單名目金額 (USDT) |
| **市場設定** | `BUY_MARKET`, `SELL_MARKET` | 指定交易對 (如 btcusdt, btctwd) |
| **網格範圍** | `BUY_GRID_UPPER/LOWER`, `SELL_GRID_UPPER/LOWER` | 買賣單的價格邊界 |
| **風控機制** | `SPIKE_THRESHOLD_USDT`, `BAD_DATA_THRESHOLD_PCT` | 波動熔斷與髒數據過濾閾值 |

## 核心功能執行
1. **啟動網格機器人**:
   ```bash
   python3 engine.py
   ```
2. **市場趨勢分析**:
   ```bash
   python3 analyzer.py
   ```

## 系統特性
- **持久化**: 重啟後自動讀取 `grid_state.db` 恢復掛單狀態。
- **隔離安全**: 透過 `DRY_RUN` 變數自動隔離模擬/實盤資料庫與日誌。
- **日誌管理**: 內建 `RotatingFileHandler` 防止日誌檔案過大。
- **通知服務**: 支援 Telegram 即時狀態與訂單建立通知。
- **保護機制**: 具備 API 波動熔斷機制與啟動前配置檢查。
