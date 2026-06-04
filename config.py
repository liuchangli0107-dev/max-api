#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from pathlib import Path


class Config:
    """
    負責載入與解析環境變數（.env 或系統環境變數），並將其實例化為全域配置物件，確保網格參數、API 金鑰及日誌路徑正確載入。
    """

    _env_file = Path(__file__).parent / ".env"
    if _env_file.exists():
        with open(_env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    # 不覆蓋啟動時傳入的環境變數（例如 SELL_MARKET=... python3 engine.py）
                    os.environ.setdefault(k, v)
    try:
        # 優先決定當前是否為模擬模式
        DRY_RUN = os.environ.get("DRY_RUN", "True").lower() == "true"
        DRY_RUN_INITIAL_USDT = float(os.environ.get("DRY_RUN_INITIAL_USDT"))
        DRY_RUN_INITIAL_BTC = float(os.environ.get("DRY_RUN_INITIAL_BTC"))
        DRY_RUN_INITIAL_TWD = float(os.environ.get("DRY_RUN_INITIAL_TWD"))

        # 根據運行模式，自動物理隔離資料庫，確保模擬與實盤數據絕不污染
        if DRY_RUN == True:
            DB_FILE = 'grid_state_dryrun.db'
            LOG_FILE = 'grid_state_dryrun.log'
        else:
            DB_FILE = os.environ.get("DB_FILE", "grid_state_live.db")
            LOG_FILE = os.environ.get("LOG_FILE", "grid_state_live.log")

        # --- 網格共用核心參數 (強制以 USDT 為計價基準) ---
        GRID_STEP = float(os.environ["GRID_STEP"])  # 買賣共用的網格間距 (USDT)
        BUY_ORDER_AMOUNT = float(
            os.environ.get("BUY_ORDER_AMOUNT")
        )  # 買單名目價值 (USDT)
        SELL_ORDER_AMOUNT = float(
            os.environ.get("SELL_ORDER_AMOUNT")
        )  # 賣單名目價值 (USDT)

        # --- 買入網格 ---
        BUY_MARKET = os.environ.get("BUY_MARKET", "btcusdt")
        BUY_GRID_UPPER = float(os.environ["BUY_GRID_UPPER"])
        BUY_GRID_LOWER = float(os.environ["BUY_GRID_LOWER"])
        BUY_ACTIVE_ORDERS = int(os.environ["BUY_ACTIVE_ORDERS"])

        # --- 賣出網格 ---
        SELL_MARKET = os.environ.get("SELL_MARKET", "btctwd")
        SELL_GRID_LOWER = float(os.environ["SELL_GRID_LOWER"])
        SELL_GRID_UPPER = float(os.environ["SELL_GRID_UPPER"])
        SELL_ACTIVE_ORDERS = int(os.environ["SELL_ACTIVE_ORDERS"])

        # --- 共用 ---
        SPIKE_THRESHOLD_USDT = float(os.environ.get("SPIKE_THRESHOLD_USDT", 1000.0))
        CIRCUIT_BREAKER_COOLDOWN = int(os.environ.get("CIRCUIT_BREAKER_COOLDOWN", 15))
        POST_ONLY_RETRY_COOLDOWN = int(os.environ.get("POST_ONLY_RETRY_COOLDOWN", 10))
        BAD_DATA_THRESHOLD_PCT = float(os.environ.get("BAD_DATA_THRESHOLD_PCT", 0.15))

        # 手續費相關設定
        FEE_BUFFER_PCT = float(os.environ.get("FEE_BUFFER_PCT", 0.0))
        FEE_RATE_MAX_TOKEN = float(os.environ.get("FEE_RATE_MAX_TOKEN", 0.00045))

        API_KEY = os.environ.get("MAX_ACCESS_KEY") or os.environ.get("MAX_API_KEY", "")
        API_SECRET = os.environ.get("MAX_SECRET_KEY") or os.environ.get(
            "MAX_API_SECRET", ""
        )

        DECIMALS_BTC_USDT_PRICE = int(os.environ.get("DECIMALS_BTC_USDT_PRICE", 2))
        DECIMALS_BTC_TWD_PRICE = int(os.environ.get("DECIMALS_BTC_TWD_PRICE", 1))
        DECIMALS_BTC_VOLUME = int(os.environ.get("DECIMALS_BTC_VOLUME", 6))

        # --- 日誌 ---
        LOG_TO_FILE = os.environ.get("LOG_TO_FILE", "True").lower() == "true"
        LOG_FILE_NAME = os.environ.get("LOG_FILE_NAME", "grid_bot.log")
        LOG_HISTORY_MAX = int(os.environ.get("LOG_HISTORY_MAX", 80))
        MA50_ENABLED = os.environ.get("MA50_ENABLED", "True").lower() == "true"
        MA50_KLINE_PERIOD = int(os.environ.get("MA50_KLINE_PERIOD", 1440))
        MA50_LENGTH = int(os.environ.get("MA50_LENGTH", 50))
        
        COST_PRICE = float(os.environ.get("COST_PRICE", 0.0))
        SLEEP_INTERVAL = int(os.environ.get("SLEEP_INTERVAL", 3600))
    except KeyError as e:
        print(f"❌ .env 設定錯誤：缺少必要參數 {e}")
        sys.exit(1)
