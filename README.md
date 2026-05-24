# MAX Exchange Trading Bot

本專案提供基於 MAX 交易所 API 的 BTCUSDT 市場監控與自動化交易腳本。

## 檔案說明
- `check_price.py`: 查詢即時市場價格。
- `test_v3_fixed.py`: 測試 V3 API 下單功能 (Post-Only)。
- `engine.py`: 核心交易邏輯 (開發中)。

## 環境設定
請在 `.env` 檔案中設定您的 API 金鑰：
```
MAX_ACCESS_KEY=your_access_key
MAX_SECRET_KEY=your_secret_key
```

## 執行
查詢價格:
```bash
python3 check_price.py btcusdt
```
