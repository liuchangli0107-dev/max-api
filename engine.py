#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MAX 交易所雙邊滾動網格機器人
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import sqlite3
import sys
import time
import httpx
from timed_size_handler import TimedSizeRotatingFileHandler
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from logging.handlers import RotatingFileHandler


async def send_telegram_notification(msg: str):
    # 💡 如果是模擬模式 (DRY_RUN = True)，直接擋掉不發送任何通知
    if getattr(Config, "DRY_RUN", True):
        return
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHANNEL_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    async with httpx.AsyncClient() as client:
        try:
            await client.post(url, json={"chat_id": chat_id, "text": msg})
        except Exception:
            pass

class Config:
    """
    負責載入與解析環境變數（.env 或系統環境變數），並將其實例化為全域配置物件，確保網格參數、API 金鑰及日誌路徑正確載入。
        - DRY_RUN 模式：提供獨立的模擬初始資金配置，並自動隔離模擬與實盤的資料庫與日誌檔案，確保兩者數據完全分離。
        - 網格核心參數：統一以 USDT 為計價基準，簡化跨市場（USDT/TWD）操作邏輯。
        - 交易安全機制：包含價格尖峰偵測、迴路保護、Post-Only 重試冷卻及異常數據閾值設定，提升交易穩定性與安全性。
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
        if DRY_RUN:
            DB_FILE = os.environ.get("DB_FILE", "grid_state_dryrun.db")
            LOG_FILE = os.environ.get("LOG_FILE", "grid_state_dryrun.log")
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
        FEE_RATE = float(os.environ.get("FEE_RATE", 0.00045))

        API_KEY = os.environ.get("MAX_ACCESS_KEY") or os.environ.get("MAX_API_KEY", "")
        API_SECRET = os.environ.get("MAX_SECRET_KEY") or os.environ.get(
            "MAX_API_SECRET", ""
        )

        DECIMALS_BTC_USDT_PRICE = int(os.environ.get("DECIMALS_BTC_USDT_PRICE", 2))
        DECIMALS_BTC_TWD_PRICE = int(os.environ.get("DECIMALS_BTC_TWD_PRICE", 0))
        DECIMALS_BTC_VOLUME = int(os.environ.get("DECIMALS_BTC_VOLUME", 4))

        # --- 日誌 ---
        LOG_TO_FILE = os.environ.get("LOG_TO_FILE", "True").lower() == "true"
        LOG_FILE_NAME = os.environ.get("LOG_FILE_NAME", "grid_bot.log")
        LOG_HISTORY_MAX = int(os.environ.get("LOG_HISTORY_MAX", 80))
        MA50_ENABLED = os.environ.get("MA50_ENABLED", "True").lower() == "true"
        MA50_KLINE_PERIOD = int(os.environ.get("MA50_KLINE_PERIOD", 1440))
        MA50_LENGTH = int(os.environ.get("MA50_LENGTH", 50))

    except KeyError as e:
        print(f"❌ .env 設定錯誤：缺少必要參數 {e}")
        sys.exit(1)


def setup_file_logger(log_filename: str):
    """
    設定全域的檔案日誌記錄器 (File Logger)
        - 具備自動輪轉功能：每個檔案最大 5MB，最多保留 3 份歷史紀錄，防止硬碟爆滿。
    """
    file_logger = logging.getLogger("GridBotFileLogger")
    file_logger.setLevel(logging.INFO)

    # 避免重複綁定 Handler 導致重複印出
    if not file_logger.handlers:
        # 使用自訂的 TimedSizeRotatingFileHandler (最大 5MB，保留 3 個備份，帶時間戳檔名)
        handler = TimedSizeRotatingFileHandler(
            log_filename, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        # 設定日誌格式：[時間] [層級] 訊息內容
        formatter = logging.Formatter(
            fmt="[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
        handler.setFormatter(formatter)
        file_logger.addHandler(handler)

    return file_logger


def _json_compact(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, separators=(",", ":"))


def fmt_usd(price: float, decimals: int = 2) -> str:
    return f"${price:,.{decimals}f}"


def fmt_twd(price: float, decimals: int = 0) -> str:
    return f"NT${price:,.{decimals}f}"


def fmt_btc(volume: float) -> str:
    s = f"{volume:.{Config.DECIMALS_BTC_VOLUME}f}"
    return s.rstrip("0").rstrip(".") or "0"


def fmt_price_for_market(price: float, market: str, decimals: int) -> str:
    if market.lower() == "btctwd":
        return fmt_twd(price, decimals)
    return fmt_usd(price, decimals)


def quote_unit_for_market(market: str) -> str:
    return "TWD" if market.lower().endswith("twd") else "USDT"


class GridDatabaseService:
    """
    提供 SQLite 資料庫互動層，負責網格狀態的 ACID 事務處理、市場快照記錄、帳戶餘額存取及啟動時的狀態還原（Persistence）。
        - 網格狀態表：以複合主鍵 (market, side, price) 確保每個價格點只有一筆狀態記錄，防止重疊買賣。
        - 市場快照表：記錄下單/成交當下的完整市場生態環境。
    """
    
    def __init__(self, db_file: str = "grid_state.db"):
        self.db_file = db_file
        self._init_db()

    def _init_db(self):
        """初始化資料庫表格與高效能查詢索引"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()

            # 1. 網格訂單狀態表 (使用複合主鍵確保一價一狀態，防止重疊買賣)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS grid_order_states (
                    market TEXT,
                    side TEXT,
                    price REAL,
                    volume REAL,
                    status TEXT,
                    order_id INTEGER,
                    frozen_quote REAL,
                    updated_at INTEGER,
                    PRIMARY KEY (market, side, price)
                )
            """)

            # 2. 市場快照表 (記錄下單/成交當下的完整市場生態環境)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT,          -- 'PLACE_SUBMIT' | 'PLACE_SUCCESS' | 'FILLED' | 'CANCEL'
                    market TEXT,
                    side TEXT,
                    price REAL,
                    volume REAL,
                    order_id INTEGER,
                    timestamp TEXT,           -- 人類可讀時間 YYYY-MM-DD HH:MM:SS
                    btc_usdt_price REAL,      -- 當下 MAX 交易所 BTC/USDT 價格
                    btc_twd_price REAL,       -- 當下 MAX 交易所 BTC/TWD 價格
                    usdt_twd_rate REAL,       -- 計算所得的即時美金兌台幣匯率
                    ma50_usdt REAL,           -- 當下 BTC/USDT 的 50MA
                    ma50_twd REAL             -- 當下 BTC/TWD 的 50MA
                )
            """)

            # 3. 帳戶餘額歷史記錄表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS account_balance_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    currency TEXT,
                    balance REAL,
                    timestamp TEXT
                )
            """)

            # 建立索引優化未來的歷史數據查詢
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshot_time ON market_snapshots(timestamp)"
            )
            conn.commit()

    def load_saved_slots(self, market: str, side: str) -> Dict[float, Dict[str, Any]]:
        """啟動時自資料庫還原特定市場與方向的網格記憶"""
        slots = {}
        with sqlite3.connect(self.db_file) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM grid_order_states WHERE market = ? AND side = ?",
                (market.lower(), side.lower()),
            )
            for row in cursor.fetchall():
                slots[row["price"]] = {
                    "price": row["price"],
                    "volume": row["volume"],
                    "status": row["status"],
                    "order_id": row["order_id"],
                    "cooldown_until": 0.0,  # 重啟時冷卻重置
                    "frozen_quote": row["frozen_quote"],
                }
        return slots

    def sync_single_slot(self, market: str, side: str, slot: Dict[str, Any]):
        """以 ACID 事務安全即時寫入網格單一價格點的狀態"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO grid_order_states 
                (market, side, price, volume, status, order_id, frozen_quote, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    market.lower(),
                    side.lower(),
                    slot["price"],
                    slot["volume"],
                    slot["status"],
                    slot["order_id"],
                    slot.get("frozen_quote", 0.0),
                    int(time.time()),
                ),
            )
            conn.commit()

    def sync_all_slots(
        self, market: str, side: str, slots: Dict[float, Dict[str, Any]]
    ):
        """以 ACID 事務安全地批次寫入該市場與方向的所有網格狀態"""
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            data = [
                (
                    market.lower(),
                    side.lower(),
                    slot["price"],
                    slot["volume"],
                    slot["status"],
                    slot["order_id"],
                    slot.get("frozen_quote", 0.0),
                    int(time.time()),
                )
                for slot in slots.values()
            ]
            cursor.executemany(
                """
                INSERT OR REPLACE INTO grid_order_states 
                (market, side, price, volume, status, order_id, frozen_quote, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
                data,
            )
            conn.commit()

    def record_balance(self, currency: str, balance: float):
        """記錄單一幣種餘額"""
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO account_balance_history (currency, balance, timestamp)
                VALUES (?, ?, ?)
            """,
                (currency.lower(), balance, ts_str),
            )
            conn.commit()

    def record_market_snapshot(
        self, event_type: str, leg_spec: Any, slot: Dict[str, Any], engine: Any
    ):
        """核心需求：下單或成交時，抓取引擎當下所有的市場價格與 MA 數據並寫入快照表"""
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())

        # 動態計算即時匯率
        usdt_twd = 0.0
        if engine.btc_usdt_price > 0:
            usdt_twd = engine.btc_twd_price / engine.btc_usdt_price

        with sqlite3.connect(self.db_file) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO market_snapshots 
                (event_type, market, side, price, volume, order_id, timestamp, 
                 btc_usdt_price, btc_twd_price, usdt_twd_rate, ma50_usdt, ma50_twd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    event_type,
                    leg_spec.market.upper(),
                    leg_spec.side.upper(),
                    slot["price"],
                    slot["volume"],
                    slot.get("order_id"),
                    ts_str,
                    engine.btc_usdt_price,
                    engine.btc_twd_price,
                    usdt_twd,
                    engine.current_ma50,
                    engine.current_ma50_twd,
                ),
            )
            conn.commit()



class GridLogger:
    """
    整合終端機 UI 歷史紀錄與檔案日誌（File Logger），針對 API 互動（下單、成交、撤單）提供結構化的日誌輸出，並支援日誌自動輪轉以保護硬碟。
        - history: 供終端機儀表板顯示的近期日誌，保持最新 max_history 條紀錄。
        - file_logger: 實體檔案日誌記錄器，使用自訂的 TimedSizeRotatingFileHandler 實現每日輪轉與大小限制。
        - _emit: 核心輸出函數，統一處理歷史紀錄更新與檔案寫入，確保 API 交易相關事件（下單、成交、撤單）都能被清晰記錄並在終端機與檔案中同步反映。
        - info/warn/error: 一般日誌輸出接口，支援不同層級的訊息分類。
        - api_submit/order_success/order_cancel/order_filled: 專門針對 API 交易事件的結構化日誌方法，提供統一格式化輸出並包含關鍵交易資訊（價格、數量、市價比較、預估手續費等）。   
    """
    
    def __init__(self, max_history: int = 5):
        # 供終端機儀表板顯示的近期日誌
        self.history = []
        self.max_history = max_history

        # 🔌 初始化檔案 Logger (會自動處理檔案輪轉與 DRY_RUN 隔離)
        self.file_logger = setup_file_logger(Config.LOG_FILE)

    def _emit(self, level: str, msg: str) -> None:
        """
        核心輸出函數：
        1. 將文字推入記憶體歷史紀錄 (供終端機看板繪製)
        2. 將文字寫入實體 log 檔案 (取代舊版沒效率的 open(file).write)
        """
        # --- 1. 更新終端機歷史紀錄 ---
        self.history.append(f"[{level}] {msg}")
        if len(self.history) > self.max_history:
            self.history.pop(0)

        # --- 2. 寫入實體日誌檔案 ---
        if level == "INFO":
            self.file_logger.info(msg)
        elif level == "WARN":
            self.file_logger.warning(msg)
        elif level == "ERROR":
            self.file_logger.error(msg)
        else:
            self.file_logger.info(msg)

    # ==================== 一般日誌 ====================
    def info(self, msg: str) -> None:
        self._emit("INFO", msg)

    def warn(self, msg: str) -> None:
        self._emit("WARN", f"⚠️ {msg}")

    def error(self, msg: str) -> None:
        self._emit("ERROR", f"❌ {msg}")

    # ==================== API 交易專屬日誌 ====================
    def api_submit(
        self,
        side: str,
        market: str,
        price: float,
        volume: float,
        decimals: int,
        usdt_twd_price: float = 0.0,
    ) -> None:
        side_u = side.upper()
        mkt = market.upper()
        px = fmt_price_for_market(price, market, decimals)
        twd_info = f" (USDTTWD: {usdt_twd_price:.2f})" if usdt_twd_price > 0 else ""
        self._emit(
            "INFO",
            f"🚀 [API] 送出掛單: {side_u} {mkt} - 價格: {px}，數量: {fmt_btc(volume)} BTC{twd_info}",
        )

    def order_success(
        self,
        order_id: int,
        post_only: bool = True,
        dry_run: bool = False,
        est_fee_twd: float = 0.0,
    ) -> None:
        tag = "Maker - Post-Only" if post_only else "Maker"
        suffix = " (DRY-RUN 模擬)" if dry_run else ""
        fee_str = f" | 預估手續費: NT${est_fee_twd:.2f}" if est_fee_twd > 0 else ""
        self._emit(
            "INFO",
            f"📌 [SUCCESS] MAX 訂單建立成功 - ID: {order_id} ({tag}){suffix}{fee_str}",
        )

    def order_cancel(
        self, market: str, price: float, decimals: int, order_id: Optional[int] = None
    ) -> None:
        oid = f" ID: {order_id}" if order_id else ""
        self._emit(
            "INFO",
            f"↩️ [API] 撤銷掛單: {market.upper()} @ {fmt_price_for_market(price, market, decimals)}{oid}",
        )

    def order_filled(
        self,
        side: str,
        market: str,
        price: float,
        volume: float,
        decimals: int,
        est_fee_twd: float = 0.0,
    ) -> None:
        fee_str = f" (預估手續費: NT${est_fee_twd:.2f})" if est_fee_twd > 0 else ""
        self._emit(
            "INFO",
            f"✅ [FILLED] {side.upper()} {market.upper()} @ {fmt_price_for_market(price, market, decimals)}，數量: {fmt_btc(volume)} BTC{fee_str}",
        )


class MaxExchangeClient:
    """
    封裝與 MAX 交易所 API 的溝通邏輯，處理 Request 簽名（HMAC-SHA256）、認證頭部資訊（Headers）生成及非同步 HTTP 請求執行。
        - get_tickers_batch: 批次獲取多個市場的最新價格，減少 API 請求次數並提高效率。
        - get_klines: 獲取指定市場的 K 線數據，支援不同時間週期與限制數量。
        - get_spot_balances: 查詢現貨帳戶餘額，並自動扣除鎖定中的資金，提供可用餘額資訊。
        - place_order: 下單方法，支援市價單與限價單，並根據交易方向自動計算實際成交量（考慮手續費），同時提供 post-only 選項以確保 Maker 訂單不被吃掉。
    """
    
    def __init__(
        self, api_key: str, api_secret: str, dry_run: bool, engine: Any = None
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.dry_run = dry_run
        self.engine = engine
        self.base_url = "https://max-api.maicoin.com"
        self.client = httpx.AsyncClient(timeout=10.0)

    async def close(self):
        await self.client.aclose()

    def _sign_request(
        self, path: str, params: Optional[Dict[str, Any]] = None
    ) -> tuple[Dict[str, str], Dict[str, Any], str]:
        """
        MAX API 簽名：body 含 nonce（與簽名一致），path 僅出現在簽名 payload 中。
        回傳 (headers, body_params, body_json)。
        """
        if not self.api_key or not self.api_secret:
            raise ValueError("未提供 API Key / Secret")

        body: Dict[str, Any] = dict(params or {})
        body["nonce"] = int(time.time() * 1000)

        to_sign = {**body, "path": path}
        payload_b64 = base64.b64encode(_json_compact(to_sign).encode()).decode()
        signature = hmac.new(
            self.api_secret.encode(),
            payload_b64.encode(),
            hashlib.sha256,
        ).hexdigest()
        headers = {
            "X-MAX-ACCESSKEY": self.api_key,
            "X-MAX-PAYLOAD": payload_b64,
            "X-MAX-SIGNATURE": signature,
            "X-Sub-Account": "main",
            "Content-Type": "application/json",
        }
        return headers, body, _json_compact(body)

    async def get_tickers_batch(self, markets: List[str]) -> Dict[str, float]:
        url = f"{self.base_url}/api/v3/tickers"
        params = [("markets[]", m.lower()) for m in markets]
        try:
            r = await self.client.get(url, params=params)
            if r.status_code == 200:
                return {t["market"]: float(t.get("last", 0.0)) for t in r.json()}
            elif r.status_code == 503:
                self.engine.logger.warn(
                    "交易所維護中 (503 Service Unavailable)，暫停監控..."
                )
                await asyncio.sleep(60)
        except Exception as e:
            self.engine.logger.error(f"獲取市場 Tickers 失敗: {e}")
        return {}

    async def get_klines(
        self, market: str, period: int = 1440, limit: int = 50
    ) -> List[List[Any]]:
        """日 K 等 OHLC，每筆 [timestamp, open, high, low, close, volume]"""
        url = f"{self.base_url}/api/v3/k"
        try:
            r = await self.client.get(
                url,
                params={"market": market.lower(), "period": period, "limit": limit},
            )
            if r.status_code == 200:
                data = r.json()
                return data if isinstance(data, list) else []
            elif r.status_code == 503:
                self.engine.logger.warn(
                    "交易所維護中 (503 Service Unavailable)，暫停監控..."
                )
                await asyncio.sleep(60)
        except Exception as e:
            self.engine.logger.error(f"獲取 K 線數據失敗 ({market}): {e}")
        return []

    async def get_spot_balances(self) -> Dict[str, float]:
        path = "/api/v3/wallet/spot/accounts"
        headers, params, _ = self._sign_request(path, {})
        url = f"{self.base_url}{path}?{urlencode(params)}"
        r = await self.client.get(url, headers=headers)
        if r.status_code != 200:
            raise Exception(f"查詢餘額失敗: {r.status_code} {r.text}")
        out: Dict[str, float] = {}
        for acct in r.json():
            cur = acct.get("currency", "").lower()
            out[cur] = float(acct.get("balance", 0)) - float(acct.get("locked", 0))

        # 記錄餘額變動
        self.engine.logger.info(f"API 餘額查詢成功: {out}")
        return out

    async def place_order(
        self,
        market: str,
        side: str,
        price: float,
        volume: float,
        decimals_price: int,
        *,
        post_only: bool = False,
        current_market_price: float = 0.0,
    ) -> Dict[str, Any]:
        if volume <= 0:
            raise ValueError("交易數量 <= 0")

        formatted_price = f"{price:.{decimals_price}f}"

        # 預估手續費 (0.045%)
        fee_rate = 0.00045
        if side.lower() == "buy":
            # 買單：減少 BTC 數量（BTC = USDT / Price * (1 - fee)）
            actual_volume = volume * (1 - fee_rate)
            formatted_volume = f"{actual_volume:.{Config.DECIMALS_BTC_VOLUME}f}"
        else:
            # 賣單：體積不變，因為手續費是扣除 TWD (Quote)
            formatted_volume = f"{volume:.{Config.DECIMALS_BTC_VOLUME}f}"

        if self.dry_run:
            if (
                post_only
                and side == "buy"
                and current_market_price > 0
                and price >= current_market_price
            ):
                raise Exception("POST_ONLY_REJECTED: 買價 >= 市價")
            if (
                post_only
                and side == "sell"
                and current_market_price > 0
                and price <= current_market_price
            ):
                raise Exception("POST_ONLY_REJECTED: 賣價 <= 市價")
            return {
                "id": int(time.time() * 1000000) + hash(market) % 1000,
                "market": market,
                "side": side,
                "price": formatted_price,
                "volume": formatted_volume,
                "state": "wait",
            }

        path = "/api/v3/wallet/spot/order"
        url = f"{self.base_url}{path}"
        headers, _, body_json = self._sign_request(
            path,
            {
                "market": market.lower(),
                "side": side.lower(),
                "volume": formatted_volume,
                "price": formatted_price,
                "ord_type": "post_only" if post_only else "limit",
            },
        )
        r = await self.client.post(url, content=body_json, headers=headers)
        if r.status_code in (200, 201):
            return r.json()
        err = r.text
        if "post_only" in err.lower() or "post-only" in err.lower():
            raise Exception(f"POST_ONLY_REJECTED: {err}")
        raise Exception(f"下單失敗: {r.status_code} - {err}")

    async def cancel_order(self, order_id: int) -> Dict[str, Any]:
        if self.dry_run:
            return {"id": order_id, "success": True}
        path = "/api/v3/order"
        url = f"{self.base_url}{path}"
        headers, _, body_json = self._sign_request(path, {"id": int(order_id)})
        r = await self.client.request("DELETE", url, content=body_json, headers=headers)
        if r.status_code in (200, 201):
            return r.json()
        raise Exception(f"撤單失敗: {r.status_code} - {r.text}")

    async def get_order_status(self, order_id: int) -> Dict[str, Any]:
        path = "/api/v3/order"
        headers, params, _ = self._sign_request(path, {"id": int(order_id)})
        url = f"{self.base_url}{path}?{urlencode(params)}"
        r = await self.client.get(url, headers=headers)
        if r.status_code == 200:
            return r.json()
        raise Exception(f"查單失敗: {r.status_code}")


@dataclass
class GridLegSpec:
    """單邊滾動網格參數"""

    label: str
    market: str
    side: str  # "buy" | "sell"
    grid_upper: float
    grid_lower: float
    step: float
    order_quote_amount: float  # USDT 或 TWD 名目金額
    quote_currency: str  # "usdt" | "twd"
    active_orders: int
    price_decimals: int

class RollingGridLeg:
    """
    實作單邊滾動網格的邏輯核心，負責計算目標價格區間、監控掛單狀態及執行價格滾動策略。
        - 狀態管理：每個價格點的狀態（IDLE、PLACED、REJECTED_COOLDOWN、CANCEL）與相關訂單資訊。
        - 目標價格計算：根據當前市場價格與設定的網格參數，動態計算應該放置掛單的價格區間。
        - 觸發條件判定：判斷當前市場價格是否觸及網格區間，決定是否需要執行滾動策略。
    """
    
    IDLE = "IDLE"
    PLACED = "PLACED"
    REJECTED_COOLDOWN = "REJECTED_COOLDOWN"
    CANCEL = "CANCEL"

    def __init__(
        self, spec: GridLegSpec, api: MaxExchangeClient, engine: "DualGridEngine"
    ):
        self.spec = spec
        self.api = api
        self.engine = engine
        self.market_price = 0.0
        self.activated = False

        # 優先自 SQLite 資料庫讀取歷史狀態，如果沒有才會在後續動態生成
        # self.db_service = GridDatabaseService(Config.DB_FILE)
        # self.slots_by_price = self.db_service.load_saved_slots(
        #     self.spec.market, self.spec.side
        # )
        
        self.db_service = GridDatabaseService(Config.DB_FILE)
        self.slots_by_price = {}
        self._validate()

    def _validate(self):
        s = self.spec
        if s.grid_lower > s.grid_upper:
            raise ValueError(f"{s.label}: grid_lower 不可大於 grid_upper")
        if s.step <= 0:
            raise ValueError(f"{s.label}: step 必須 > 0")

    def _get_or_create_slot(self, price: float) -> Dict[str, Any]:
        """取得特定價格的網格插槽，若不存在則在記憶體建立並同步寫入 SQLite 資料庫。"""
        key = self._round_price(price)
        if key not in self.slots_by_price:
            self.slots_by_price[key] = {
                "price": key,
                "volume": self._volume_for_price(key),
                "status": self.IDLE,
                "order_id": None,
                "cooldown_until": 0.0,
                "frozen_quote": 0.0,
            }
            # 🔌 同步點：新插槽一旦初始化，立刻以 ACID 事務寫入 SQLite 存檔備份
            self.db_service.sync_single_slot(
                self.spec.market, self.spec.side, self.slots_by_price[key]
            )
        return self.slots_by_price[key]

    def _round_price(self, p: float) -> float:
        return round(p, self.spec.price_decimals)

    def _volume_for_price(self, price: float) -> float:
        """依名目金額（USDT 或 TWD）換算 BTC 數量"""
        return round(
            self.spec.order_quote_amount / price,
            Config.DECIMALS_BTC_VOLUME,
        )

    def _all_grid_prices(self) -> List[float]:
        s = self.spec
        prices: List[float] = []
        if s.side == "buy":
            p = s.grid_upper
            while p >= s.grid_lower - 1e-9:
                prices.append(self._round_price(p))
                p -= s.step
        else:
            p = s.grid_lower
            while p <= s.grid_upper + 1e-9:
                prices.append(self._round_price(p))
                p += s.step
        return prices

    def _candidate_prices(self) -> List[float]:
        """所有網格的計算基準，強制錨定 BTC/USDT 價格，防止跨幣種匯差失真"""

        # 💡 核心修正：不論是買方(USDT)還是賣方(TWD)，一律看 USDT 的市價與步長來判定區間
        base_market_price = self.engine.btc_usdt_price
        if base_market_price <= 0:
            return []

        # 這裡的 self.spec.step 必須設定為 USDT 的步長 (例如 500)
        buffer = self.spec.step * 1.0

        if self.spec.side == "buy":
            # 買單：必須低於 USDT市價 - 步長 * N%，確保有空間放置第一檔買單
            return [
                p for p in self._all_grid_prices() if p <= (base_market_price - buffer)
            ]
        else:
            # 賣單：必須高於 USDT市價 + 步長 * N%，確保有空間放置第一檔賣單
            return [
                p for p in self._all_grid_prices() if p >= (base_market_price + buffer)
            ]

    def compute_target_prices(self) -> List[float]:
        """滾動 N 檔：買取最接近市價的下方 N 檔；賣取最接近市價的上方 N 檔"""
        cands = self._candidate_prices()
        if self.spec.side == "buy":
            cands.sort(reverse=True)
        else:
            cands.sort()
        return cands[: self.spec.active_orders]

    def is_triggered(self) -> bool:
        m = self.market_price
        u = self.spec.grid_upper + self.spec.step
        l = self.spec.grid_lower - self.spec.step
        return m >= l or m <= u

    def _get_or_create_slot(self, price: float) -> Dict[str, Any]:
        key = self._round_price(price)
        if key not in self.slots_by_price:
            self.slots_by_price[key] = {
                "price": key,
                "volume": self._volume_for_price(key),
                "status": self.IDLE,
                "order_id": None,
                "cooldown_until": 0.0,
                "frozen_quote": 0.0,
            }
        return self.slots_by_price[key]

    async def _quote_available(self) -> float:
        bal = await self.engine.get_balances()
        if self.spec.side == "buy":
            frozen = self.engine.frozen_usdt
            return max(0.0, bal.get("usdt", 0.0) - frozen)
        frozen = self.engine.frozen_btc
        return max(0.0, bal.get("btc", 0.0) - frozen)

    def _quote_needed(self, slot: Dict[str, Any]) -> float:
        buf = 1.0 + Config.FEE_BUFFER_PCT
        if self.spec.side == "buy":
            return self.spec.order_quote_amount * buf
        return slot["volume"] * buf

    async def _has_balance(self, slot: Dict[str, Any]) -> bool:
        return await self._quote_available() >= self._quote_needed(slot)

    def _on_place_success(self, slot: Dict[str, Any]):
        if not Config.DRY_RUN:
            return
        if self.spec.side == "buy":
            self.engine.frozen_usdt += self.spec.order_quote_amount
            slot["frozen_quote"] = self.spec.order_quote_amount
        else:
            self.engine.frozen_btc += slot["volume"]
            slot["frozen_quote"] = slot["volume"]

    def _on_cancel_place(self, slot: Dict[str, Any]):
        if not Config.DRY_RUN:
            self.engine.logger.info(f"🚨 [撤單] 正在撤銷訂單 ID: {slot['order_id']}, 價位: {slot['price']}, side: {self.spec.side}")
            return
        if self.spec.side == "buy":
            self.engine.frozen_usdt = max(
                0.0,
                self.engine.frozen_usdt
                - slot.get("frozen_quote", self.spec.order_quote_amount),
            )
        else:
            self.engine.frozen_btc = max(
                0.0, self.engine.frozen_btc - slot.get("frozen_quote", slot["volume"])
            )
        slot["frozen_quote"] = 0.0

    def _on_fill(self, slot: Dict[str, Any]):
        vol = slot["volume"]
        if Config.DRY_RUN:
            if self.spec.side == "buy":
                self.engine.frozen_usdt = max(
                    0.0,
                    self.engine.frozen_usdt
                    - slot.get("frozen_quote", self.spec.order_quote_amount),
                )
                self.engine.balance_usdt -= self.spec.order_quote_amount
                self.engine.balance_btc += vol
            else:
                self.engine.frozen_btc = max(
                    0.0, self.engine.frozen_btc - slot.get("frozen_quote", vol)
                )
                self.engine.balance_btc -= vol
                self.engine.balance_twd += self.spec.order_quote_amount
            slot["frozen_quote"] = 0.0

    async def monitor_fills(self):
        for slot in list(self.slots_by_price.values()):
            if slot["status"] != self.PLACED or not slot["order_id"]:
                continue
            filled = await self._check_filled(slot["order_id"], slot["price"])
            if not filled:
                continue

            # 🔌 SQLite 調整：趁 order_id 尚未被 _on_fill 沖掉前，先錄製成交快照 (FILLED)
            self.db_service.record_market_snapshot(
                "FILLED", self.spec, slot, self.engine
            )

            self._on_fill(slot)

            # 💡 修正：動態匯率轉換與精準手續費計算
            rate = (
                self.engine.btc_twd_price / self.engine.btc_usdt_price
                if self.engine.btc_usdt_price > 0
                else 0
            )
            fee_rate = getattr(self.engine.config, "FEE_RATE", 0.00045)

            if self.spec.side == "buy":
                est_fee_twd = slot["volume"] * self.engine.btc_twd_price * fee_rate
            else:
                # 賣單的名目是 50 USDT，必須先乘上即時匯率轉成台幣，才能精準算出台幣手續費
                est_fee_twd = (self.spec.order_quote_amount * rate) * fee_rate

            self.engine.total_fee_twd += est_fee_twd

            self.engine.logger.order_filled(
                self.spec.side,
                self.spec.market,
                slot["price"],
                slot["volume"],
                self.spec.price_decimals,
                est_fee_twd=est_fee_twd,
            )

            # 🔌 SQLite 調整：記憶體 Slot 重置為 IDLE
            slot["status"] = self.IDLE
            slot["order_id"] = None

        # 🔌 SQLite 優化：在監控成交後統一同步
        self.db_service.sync_all_slots(
            self.spec.market, self.spec.side, self.slots_by_price
        )

    async def _check_filled(self, order_id: int, order_price: float) -> bool:
        if Config.DRY_RUN:
            if self.spec.side == "buy":
                return self.market_price <= order_price
            return self.market_price >= order_price
        try:
            info = await self.api.get_order_status(order_id)
            if info.get("state") == "done":
                return True
            if info.get("state") == "cancel":
                return False
            executed = float(info.get("executed_volume", 0) or 0)
            volume = float(info.get("volume", 0) or 0)
            return volume > 0 and executed >= volume * 0.999
        except Exception:
            return False

    async def sync_orders(self):
        now = time.time()

        # 1. 決定合法的目標價：如果未啟動 (OFF)，目標清單就是空的 []
        targets = self.compute_target_prices() if self.activated else []
        target_set = set(targets)

        if self.activated and not targets:
            self.engine.logger.warn(
                f"[{self.spec.label}] 無可用掛單價（市價: {self.market_price}）"
            )

        # ------------------------------------------------------------
        # 1. 滾動撤單 (兼具清道夫功能)：不管啟動了沒，只要不在合法目標內的，無情撤銷！
        # ------------------------------------------------------------
        for slot in list(self.slots_by_price.values()):
            if slot["status"] != self.PLACED:
                continue
            if slot["price"] in target_set:
                continue

            oid = slot["order_id"]
            if oid:
                try:
                    # 記錄當下的市場價格快照 (CANCEL)
                    self.db_service.record_market_snapshot(
                        "CANCEL", self.spec, slot, self.engine
                    )
                    self.engine.logger.info(f"🚨 [滾動撤單] 正在撤銷訂單 ID: {oid}, 價位: {slot['price']}")
                    await self.api.cancel_order(oid)
                    self._on_cancel_place(slot)
                    self.engine.logger.order_cancel(
                        self.spec.market, slot["price"], self.spec.price_decimals, oid
                    )
                except Exception as e:
                    # 💡 修正優化：如果 404 (訂單已在交易所消失)，不報錯，而且「不寫 continue」！
                    # 這樣程式才會繼續往下走，把記憶體跟 DB 裡的殭屍狀態清掉。
                    if "404" in str(e) or "Not Found" in str(e):
                        self._on_cancel_place(slot)
                    else:
                        self.engine.logger.error(
                            f"[{self.spec.label}] 撤單 @{slot['price']} 失敗: {e}"
                        )
                        continue  # 只有遇到網路斷線等真異常，才保留 PLACED 狀態等下回合重試

            # 記憶體狀態重置
            slot["status"] = self.IDLE
            slot["order_id"] = None

        # ------------------------------------------------------------
        # ⛔️ 攔截點：掃蕩完舊單後，如果網格根本還沒啟動 (OFF)，就不准往下掛新單！
        # ------------------------------------------------------------
        if not self.activated:
            # 🔌 SQLite 優化：在 sync_orders 結束時，將所有變動狀態一次性批次寫入
            self.db_service.sync_all_slots(
                self.spec.market, self.spec.side, self.slots_by_price
            )
            return

        # ------------------------------------------------------------
        # 2. 動態補單：針對新進入區間或冷卻結束的檔位嘗試重新掛單
        # ------------------------------------------------------------
        for price in targets:
            # 這裡內部會調用 _get_or_create_slot，新 Slot 的初始狀態會自動同步入庫
            slot = self._get_or_create_slot(price)

            if slot["status"] == self.REJECTED_COOLDOWN:
                if now < slot["cooldown_until"]:
                    continue
                slot["status"] = self.IDLE

            if slot["status"] != self.IDLE:
                continue
            if self.spec.side == "buy" and price >= self.market_price:
                continue
            if self.spec.side == "sell" and price <= self.market_price:
                continue

            if not await self._has_balance(slot):
                cur = self.spec.quote_currency.upper()
                px = fmt_price_for_market(
                    price, self.spec.market, self.spec.price_decimals
                )
                self.engine.logger.warn(
                    f"[{self.spec.label}] {cur} 餘額不足，略過 {px}"
                )
                continue

            # 執行下單
            await self._try_place(slot)

    async def _try_place(self, slot: Dict[str, Any]) -> bool:
        price = slot["price"]  # 這裡的 slot["price"] 永遠是 USDT 計價標準 (例如 73000)
        vol = slot["volume"]
        log = self.engine.logger

        # 1. 取得即時匯率
        usdt_twd = 0.0
        if getattr(self.engine, "btc_usdt_price", 0) > 0:
            usdt_twd = self.engine.btc_twd_price / self.engine.btc_usdt_price

        # 💡 核心修正：動態匯率轉換 (Just-In-Time Conversion)
        actual_place_price = price
        if self.spec.quote_currency.lower() == "twd":
            if usdt_twd <= 0:
                log.error(f"[{self.spec.label}] 取得匯率異常，拒絕下單以保護資金！")
                return False
            # 將 USDT 價格轉換為實際的 TWD 掛單價
            actual_place_price = price * usdt_twd

        log.api_submit(
            self.spec.side,
            self.spec.market,
            actual_place_price,
            vol,
            self.spec.price_decimals,
            usdt_twd_price=usdt_twd,
        )

        # 🔌 SQLite 調整：送出掛單前的市場狀態快照
        self.db_service.record_market_snapshot(
            "PLACE_SUBMIT", self.spec, slot, self.engine
        )

        # 💡 修正：計算該訂單建立時的預估台幣手續費金額
        if self.spec.side == "buy":
            est_fee_twd = vol * self.engine.btc_twd_price * self.engine.config.FEE_RATE
        else:
            # 賣單的名目是 50 USDT，必須先乘上即時匯率轉成台幣，再算手續費
            est_fee_twd = (
                self.spec.order_quote_amount * usdt_twd
            ) * self.engine.config.FEE_RATE

        try:
            order = await self.api.place_order(
                market=self.spec.market,
                side=self.spec.side,
                price=actual_place_price,  # 💡 實際打 API 的價格
                volume=vol,
                decimals_price=self.spec.price_decimals,
                post_only=True,
                current_market_price=self.market_price,
            )
            self._on_place_success(slot)
            oid = int(order["id"])
            slot["order_id"] = oid
            slot["status"] = self.PLACED
            slot["cooldown_until"] = 0.0

            # 🔌 SQLite 調整：掛單成功，即時同步 Slot 狀態並補發 SUCCESS 快照
            self.db_service.sync_single_slot(self.spec.market, self.spec.side, slot)
            self.db_service.record_market_snapshot(
                "PLACE_SUCCESS", self.spec, slot, self.engine
            )

            log.order_success(
                oid,
                post_only=True,
                dry_run=self.engine.config.DRY_RUN,
                est_fee_twd=est_fee_twd,
            )
            await send_telegram_notification(
                f"🚀 已建立掛單: {self.spec.side.upper()} {self.spec.market.upper()} @ {fmt_price_for_market(actual_place_price, self.spec.market, self.spec.price_decimals)}"
            )
            return True

        except Exception as e:
            err = str(e)
            px = fmt_price_for_market(
                actual_place_price, self.spec.market, self.spec.price_decimals
            )
            if "POST_ONLY_REJECTED" in err:
                slot["status"] = self.REJECTED_COOLDOWN
                slot["cooldown_until"] = (
                    time.time() + self.engine.config.POST_ONLY_RETRY_COOLDOWN
                )
                slot["order_id"] = None

                # 🔌 SQLite 調整：即使是冷卻狀態，也要同步進資料庫
                self.db_service.sync_single_slot(self.spec.market, self.spec.side, slot)

                log.warn(
                    f"[{self.spec.label}] Post-Only 被拒絕 {px}，"
                    f"冷卻 {self.engine.config.POST_ONLY_RETRY_COOLDOWN}s"
                )
            else:
                log.error(f"[{self.spec.label}] 下單失敗 {px}: {err}")
            return False

    def placed_count(self) -> int:
        return sum(
            1 for s in self.slots_by_price.values() if s["status"] == self.PLACED
        )

    async def cancel_all(self):
        for slot in self.slots_by_price.values():
            if slot["status"] != self.PLACED or not slot["order_id"]:
                continue
            try:
                oid = slot["order_id"]
                await self.api.cancel_order(oid)
                self._on_cancel_place(slot)
                self.engine.logger.order_cancel(
                    self.spec.market, slot["price"], self.spec.price_decimals, oid
                )
                await send_telegram_notification(
                    f"↩️ 已撤銷掛單: {self.spec.market.upper()} @ {fmt_price_for_market(slot['price'], self.spec.market, self.spec.price_decimals)}"
                )
            except Exception as e:
                self.engine.logger.error(
                    f"[{self.spec.label}] 撤單 @{slot['price']} 失敗: {e}"
                )
            slot["status"] = self.IDLE
            slot["order_id"] = None

class DualGridEngine:
    """
    機器人的主引擎（Main Engine），負責協調買賣雙邊（Buy/Sell Leg）、處理市場數據更新、執行儀表板渲染及控制自動化交易的運行循環（Run Loop）。
        - 買賣雙邊協調：同時管理買方和賣方兩套獨立的滾動網格邏輯，確保它們根據各自的市場價格和配置正確運行。
        - 市場數據更新：定期從交易所獲取最新的市場價格（BTC/USDT、BTC/TWD）和技術指標（如 MA50），並將這些數據傳遞給買賣雙邊以供其計算目標價格區間。
        - 儀表板渲染：整合來自買賣雙邊的狀態資訊，渲染成清晰易讀的儀表板，提供即時的策略執行情況、資金狀態和市場行情概覽。
        - 自動化交易控制：在主循環中定期觸發買賣雙邊的同步方法，實現自動化的滾動網格交易策略，同時監控成交狀態並進行必要的調整。
        - 風險管理與熔斷機制：監控市場價格與技術指標變化，當出現極端行情或異常情況時，啟動熔斷機制暫停交易活動，以保護資金安全。
        - 資金狀態管理：追蹤可用餘額、凍結資金和預估手續費等關鍵財務指標，確保每次下單前都有足夠的資金支持，並在儀表板上實時反映這些資訊。
        - 日誌與通知系統：記錄所有重要事件（如下單、撤單、成交、錯誤等），並通過 Telegram 發送關鍵通知，讓使用者隨時掌握機器人的運行情況。
        - 配置驗證與啟動檢查：在啟動階段對配置參數進行全面檢查，確保所有設定合理且不會導致策略失效或資金風險，只有通過檢查後才允許機器人正式運行。
        - 整合 SQLite 資料
    """
    
    def __init__(self):
        self.config = Config()
        self.api = MaxExchangeClient(
            getattr(self.config, "API_KEY", ""),
            getattr(self.config, "API_SECRET", ""),
            self.config.DRY_RUN,
            engine=self,
        )

        # 初始化買方網格
        self.buy_leg = RollingGridLeg(
            GridLegSpec(
                label="BTCTWD買",
                market=self.config.BUY_MARKET,
                side="buy",
                grid_upper=self.config.BUY_GRID_UPPER,
                grid_lower=self.config.BUY_GRID_LOWER,
                step=self.config.GRID_STEP,
                order_quote_amount=self.config.BUY_ORDER_AMOUNT,
                quote_currency="usdt",
                active_orders=self.config.BUY_ACTIVE_ORDERS,
                price_decimals=getattr(self.config, "DECIMALS_BTC_USDT_PRICE", 2),
            ),
            self.api,
            self,
        )

        # 初始化賣方網格
        self.sell_leg = RollingGridLeg(
            GridLegSpec(
                label="BTCTWD賣",
                market=self.config.SELL_MARKET,
                side="sell",
                grid_upper=self.config.SELL_GRID_UPPER,
                grid_lower=self.config.SELL_GRID_LOWER,
                step=self.config.GRID_STEP,
                order_quote_amount=self.config.SELL_ORDER_AMOUNT,
                quote_currency=(
                    "twd" if self.config.SELL_MARKET.lower() == "btctwd" else "usdt"
                ),
                active_orders=self.config.SELL_ACTIVE_ORDERS,
                price_decimals=(
                    getattr(self.config, "DECIMALS_BTC_TWD_PRICE", 1)
                    if self.config.SELL_MARKET.lower() == "btctwd"
                    else getattr(self.config, "DECIMALS_BTC_USDT_PRICE", 2)
                ),
            ),
            self.api,
            self,
        )

        self.is_running = True
        self.btc_usdt_price = 0.0
        self.last_btc_usdt_price = 0.0
        self.btc_twd_price = 0.0
        self.current_ma50 = 0.0
        self.current_ma50_price = 0.0
        self.current_ma50_twd = 0.0
        self.current_ma50_twd_price = 0.0

        # 資金狀態
        self.balance_usdt = getattr(self.config, "DRY_RUN_INITIAL_USDT", 0.0)
        self.balance_btc = getattr(self.config, "DRY_RUN_INITIAL_BTC", 0.0)
        self.balance_twd = getattr(self.config, "DRY_RUN_INITIAL_TWD", 0.0)
        self.frozen_usdt = 0.0
        self.frozen_btc = 0.0
        self.total_fee_twd = 0.0  # 用於統計已成交訂單的預估累計台幣手續費

        # 熔斷狀態
        self.circuit_breaker_active = False
        self.circuit_breaker_until = 0.0

        # 💡 使用新版且乾淨的 Logger 實例化
        self.logger = GridLogger(max_history=5)
        self._ma50_logged = False

        self._validate_startup_config()

        self.logger.info("雙邊滾動網格機器人就緒")
        self.logger.info(
            f"{self.config.BUY_MARKET.upper()} 買入網格: {self.config.BUY_GRID_LOWER:.0f} ~ {self.config.BUY_GRID_UPPER:.0f}，"
            f"步長 {self.config.GRID_STEP:.0f}，每單 {self.config.BUY_ORDER_AMOUNT:.0f} USDT，"
            f"範圍 {(self.config.BUY_GRID_UPPER + self.config.GRID_STEP):.0f} ~ {(self.config.BUY_GRID_LOWER - self.config.GRID_STEP):.0f}"
        )
        self.logger.info(
            f"{self.config.SELL_MARKET.upper()} 賣出網格: {self.config.SELL_GRID_LOWER:.0f} ~ {self.config.SELL_GRID_UPPER:.0f}，"
            f"步長 {self.config.GRID_STEP:.0f}，每單 {self.config.SELL_ORDER_AMOUNT:.0f} USDT，"
            f"範圍 {(self.config.SELL_GRID_UPPER + self.config.GRID_STEP):.0f} ~ {(self.config.SELL_GRID_LOWER - self.config.GRID_STEP):.0f}"
        )

    def _validate_startup_config(self) -> None:
        """
        啟動時配置摘要檢查：
            - 致命錯誤：直接 raise，避免帶錯參數進實盤
            - 可疑配置：記錄 warning，提醒使用者
        """
        c = self.config
        errors: List[str] = []
        warns: List[str] = []

        def _levels(lower: float, upper: float, step: float) -> int:
            return int(round((upper - lower) / step)) + 1

        # ---- 💡 基本數值檢查 (已移除重複並換上共用參數) ----
        if getattr(c, "GRID_STEP", 0) <= 0:
            errors.append("GRID_STEP 必須 > 0")
        if c.BUY_GRID_LOWER > c.BUY_GRID_UPPER:
            errors.append("BUY_GRID_LOWER 不可大於 BUY_GRID_UPPER")
        if c.SELL_GRID_LOWER > c.SELL_GRID_UPPER:
            errors.append("SELL_GRID_LOWER 不可大於 SELL_GRID_UPPER")

        if getattr(c, "BUY_ORDER_AMOUNT", 0) <= 0:
            errors.append("BUY_ORDER_AMOUNT 必須 > 0")
        if getattr(c, "SELL_ORDER_AMOUNT", 0) <= 0:
            errors.append("SELL_ORDER_AMOUNT 必須 > 0")

        if c.BUY_ACTIVE_ORDERS <= 0:
            errors.append("BUY_ACTIVE_ORDERS 必須 > 0")
        if c.SELL_ACTIVE_ORDERS <= 0:
            errors.append("SELL_ACTIVE_ORDERS 必須 > 0")
        if getattr(c, "FEE_BUFFER_PCT", 0) < 0:
            errors.append("FEE_BUFFER_PCT 不可為負數")
        if getattr(c, "FEE_RATE", 0) < 0:
            errors.append("FEE_RATE 不可為負數")

        buy_levels = _levels(
            c.BUY_GRID_LOWER, c.BUY_GRID_UPPER, getattr(c, "GRID_STEP", 1)
        )
        sell_levels = _levels(
            c.SELL_GRID_LOWER, c.SELL_GRID_UPPER, getattr(c, "GRID_STEP", 1)
        )
        if buy_levels and c.BUY_ACTIVE_ORDERS > buy_levels:
            errors.append(
                f"BUY_ACTIVE_ORDERS={c.BUY_ACTIVE_ORDERS} 大於可用買網格檔數 {buy_levels}"
            )
        if sell_levels and c.SELL_ACTIVE_ORDERS > sell_levels:
            errors.append(
                f"SELL_ACTIVE_ORDERS={c.SELL_ACTIVE_ORDERS} 大於可用賣網格檔數 {sell_levels}"
            )

        # ---- 實盤安全檢查 ----
        if not c.DRY_RUN:
            if not getattr(c, "API_KEY", "") or not getattr(c, "API_SECRET", ""):
                errors.append("實盤模式需要設定 API_KEY / API_SECRET")
            if (
                getattr(c, "BUY_MARKET", "").lower()
                == getattr(c, "SELL_MARKET", "").lower()
            ):
                warns.append(
                    "BUY_MARKET 與 SELL_MARKET 相同，將在同一市場同時掛買/賣，請確認策略意圖"
                )
        else:
            if getattr(c, "DRY_RUN_INITIAL_USDT", 0) <= 0:
                warns.append("DRY_RUN_INITIAL_USDT <= 0，買網格可能無法掛單")
            if getattr(c, "DRY_RUN_INITIAL_BTC", 0) <= 0:
                warns.append("DRY_RUN_INITIAL_BTC <= 0，賣網格可能無法掛單")

        # ---- 輸出摘要 ----
        if warns:
            for w in warns:
                self.logger.warn(w)
        if errors:
            for e in errors:
                self.logger.error(e)
            sys.exit(1)

        for w in warns:
            self.logger.warn(f"[CONFIG] {w}")

        if errors:
            for e in errors:
                self.logger.error(f"[CONFIG] {e}")
            raise ValueError("配置檢查失敗，請修正配置後重啟")

    async def _log_ma50_context(
        self, market_price: float, market: str
    ) -> tuple[float, float]:
        if not getattr(self.config, "MA50_ENABLED", False):
            return 0.0, 0.0
        klines = await self.api.get_klines(
            market,
            period=getattr(self.config, "MA50_KLINE_PERIOD", 15),
            limit=getattr(self.config, "MA50_LENGTH", 50),
        )
        ma50_len = getattr(self.config, "MA50_LENGTH", 50)
        if len(klines) < ma50_len:
            if market == self.config.BUY_MARKET:
                self.logger.warn(f"[MA50對齊偵測] {market} K 線資料不足，略過")
            return 0.0, 0.0

        closes = [float(k[4]) for k in klines[-ma50_len:]]
        ma50 = sum(closes) / len(closes)

        if not hasattr(self, "_ma50_logged_markets"):
            self._ma50_logged_markets = set()

        if market not in self._ma50_logged_markets:
            diff_pct = (market_price - ma50) / ma50 * 100 if ma50 else 0.0
            rel = f"{diff_pct:+.2f}%"
            self.logger.info(
                f"[MA50對齊偵測] {market.upper()} 50MA: {ma50:.0f}，現價 {market_price:.2f} ({rel})"
            )
            self._ma50_logged_markets.add(market)

        return market_price, ma50

    async def get_balances(self) -> Dict[str, float]:
        if self.config.DRY_RUN:
            return {
                "usdt": self.balance_usdt,
                "btc": self.balance_btc,
                "twd": self.balance_twd,
            }
        return await self.api.get_spot_balances()

    async def run_loop(self):
        self.logger.info("開始監控 BTCUSDT / BTCTWD 行情與網格調度...")
        while self.is_running:
            try:
                tickers = await self.api.get_tickers_batch(
                    [self.config.BUY_MARKET, self.config.SELL_MARKET]
                )
                usdt_p = tickers.get(self.config.BUY_MARKET, 0.0)
                twd_p = tickers.get(self.config.SELL_MARKET, 0.0)
                if not usdt_p or not twd_p:
                    await asyncio.sleep(1.0)
                    continue

                now = time.time()
                # 熔斷保護判定
                if self.btc_usdt_price > 0:
                    chg = abs(usdt_p - self.btc_usdt_price)
                    pct = chg / self.btc_usdt_price
                    bad_threshold = getattr(self.config, "BAD_DATA_THRESHOLD_PCT", 0.15)
                    spike_threshold = getattr(self.config, "SPIKE_THRESHOLD_USDT", 1000)

                    if pct >= bad_threshold:
                        self.logger.warn(
                            f"疑似 API 髒數據，單輪變動 {pct*100:.1f}%，略過報價"
                        )
                        await asyncio.sleep(1.0)
                        continue
                    if chg >= spike_threshold and not self.circuit_breaker_active:
                        self.circuit_breaker_active = True
                        self.circuit_breaker_until = now + getattr(
                            self.config, "CIRCUIT_BREAKER_COOLDOWN", 15
                        )
                        self.logger.warn(
                            f"波動熔斷觸發：單輪變動 {chg:.0f} USDT，暫停交易 {getattr(self.config, 'CIRCUIT_BREAKER_COOLDOWN', 15)}s"
                        )

                self.last_btc_usdt_price = self.btc_usdt_price or usdt_p
                self.btc_usdt_price = usdt_p
                self.btc_twd_price = twd_p

                # 更新 MA50 數據
                self.current_ma50_price, self.current_ma50 = (
                    await self._log_ma50_context(usdt_p, self.config.BUY_MARKET)
                )
                self.current_ma50_twd, self.current_ma50_twd_price = (
                    await self._log_ma50_context(twd_p, self.config.SELL_MARKET)
                )

                # 💡 核心修正：因為網格上下限與觸發價皆已統一為 USDT，所以雙邊都必須餵入 usdt_p 來做比較！
                self.buy_leg.market_price = usdt_p
                self.sell_leg.market_price = usdt_p

                if self.circuit_breaker_active:
                    if now >= self.circuit_breaker_until:
                        self.circuit_breaker_active = False
                        self.logger.info("波動熔斷已解除，恢復網格調度")
                    else:
                        self.draw_dashboard()
                        await asyncio.sleep(1.0)
                        continue

                # 買方網格啟動判定
                if not self.buy_leg.activated and self.buy_leg.is_triggered():
                    self.buy_leg.activated = True
                    self.logger.info(f"當前 {usdt_p:.2f}，啟動買入網格調度...")
                    await self._log_ma50_context(usdt_p, self.config.BUY_MARKET)
                    targets = self.buy_leg.compute_target_prices()
                    if targets:
                        px_list = ", ".join(f"{p:.0f}" for p in targets)
                        self.logger.info(f"買入滾動目標價: {px_list}")

                # 賣方網格啟動判定
                if not self.sell_leg.activated and self.sell_leg.is_triggered():
                    self.sell_leg.activated = True
                    self.logger.info(f"當前 {usdt_p:.2f}U，啟動賣出網格調度...")
                    targets = self.sell_leg.compute_target_prices()
                    if targets:
                        px_list = ", ".join(f"{p:.0f}" for p in targets)
                        self.logger.info(f"賣出滾動目標價: {px_list}")

                await self.buy_leg.monitor_fills()
                await self.sell_leg.monitor_fills()
                await self.buy_leg.sync_orders()
                await self.sell_leg.sync_orders()

                self.draw_dashboard()
                await asyncio.sleep(1.0)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"主循環異常: {e}")
                await asyncio.sleep(2.0)

    def draw_dashboard(self):
        try:
            os.system("cls" if os.name == "nt" else "clear")
            mode = "🧪 模擬" if getattr(self.config, "DRY_RUN", True) else "🔴 實盤"
            now = time.time()

            if self.circuit_breaker_active and now < self.circuit_breaker_until:
                status = f"🚨 熔斷 ({self.circuit_breaker_until - now:.0f}s)"
            else:
                b_on = "ON" if self.buy_leg.activated else "OFF"
                s_on = "ON" if self.sell_leg.activated else "OFF"
                status = (
                    f"買網格[{b_on}] {self.buy_leg.placed_count()}/{self.config.BUY_ACTIVE_ORDERS} | "
                    f"賣網格[{s_on}] {self.sell_leg.placed_count()}/{self.config.SELL_ACTIVE_ORDERS}"
                )

            sec = (
                self.btc_usdt_price - self.last_btc_usdt_price
                if self.last_btc_usdt_price
                else 0
            )
            sec_s = f"+{sec:.2f}" if sec >= 0 else f"{sec:.2f}"

            # 💡 動態抓取市場名稱，確保大寫顯示正確
            buy_mkt = getattr(self.config, "BUY_MARKET", "btcusdt").upper()
            sell_mkt = getattr(self.config, "SELL_MARKET", "btctwd").upper()

            # 計算有效匯率
            usdt_twd_rate = (
                self.btc_twd_price / self.btc_usdt_price
                if (self.btc_usdt_price > 0 and sell_mkt.endswith("TWD"))
                else 0
            )

            print("=" * 88)
            print(f"   MAX 雙邊滾動網格（{buy_mkt} 買 / {sell_mkt} 賣）")
            print("=" * 88)
            print(f" {status} | {mode}")
            print(
                f" {buy_mkt} {self.btc_usdt_price:.2f} ({sec_s}) | {sell_mkt} {self.btc_twd_price:.0f}"
            )

            if getattr(self.config, "MA50_ENABLED", False):
                if self.current_ma50 > 0:
                    print(
                        f" {buy_mkt} 50MA: {self.current_ma50:.0f} | 現價: {self.current_ma50_price:.2f}"
                    )
                if self.current_ma50_twd > 0:
                    print(
                        f" {sell_mkt}  50MA: {self.current_ma50_twd:.0f} | 現價: {self.current_ma50_twd_price:.0f}"
                    )
            print("-" * 88)

            buy_t = self.buy_leg.compute_target_prices()
            sell_t = self.sell_leg.compute_target_prices()

            # 💡 強制轉型 (float)：預防 .env 讀取為字串時，字串格式化崩潰的致命 Bug
            b_lower = float(getattr(self.config, "BUY_GRID_LOWER", 0))
            b_upper = float(getattr(self.config, "BUY_GRID_UPPER", 0))
            s_lower = float(getattr(self.config, "SELL_GRID_LOWER", 0))
            s_upper = float(getattr(self.config, "SELL_GRID_UPPER", 0))
            step = float(getattr(self.config, "GRID_STEP", 0))
            b_amt = float(getattr(self.config, "BUY_ORDER_AMOUNT", 0))
            s_amt = float(getattr(self.config, "SELL_ORDER_AMOUNT", 0))

            if buy_t:
                print(
                    f" [買] 區間 {b_lower:.0f}~{b_upper:.0f} 步長{step:.0f} 每單{b_amt:.0f}U"
                )
                print(f"      目標: {', '.join(f'{p:.0f}' for p in buy_t)}")

            if sell_t:
                is_twd_market = sell_mkt.endswith("TWD")
                print(
                    f" [賣] 區間 {s_lower:.0f}~{s_upper:.0f} 步長{step:.0f} 每單{s_amt:.0f}U"
                )
                if is_twd_market and usdt_twd_rate > 0:
                    targets_str = ", ".join(
                        f"{p:.0f}U(約{p * usdt_twd_rate:.0f}TWD)" for p in sell_t
                    )
                else:
                    targets_str = ", ".join(f"{p:.0f}U" for p in sell_t)
                print(f"      目標: {targets_str}")

            print("-" * 88)

            if getattr(self.config, "DRY_RUN", True):
                print(
                    f" USDT {self.balance_usdt:.2f} (凍結{self.frozen_usdt:.0f}) | "
                    f"BTC {self.balance_btc:.6f} (凍結{self.frozen_btc:.6f}) | "
                    f"TWD {self.balance_twd:.0f}"
                )
            print(
                f" 📊 累計預估手續費: NT${self.total_fee_twd:.2f} (以 MAX Token 支付金額等值折算)"
            )
            print("-" * 88)

            self._print_leg_table(f"{buy_mkt} 買入", self.buy_leg, buy_t)
            print("-" * 88)
            self._print_leg_table(f"{sell_mkt} 賣出", self.sell_leg, sell_t)
            print("-" * 88)

            print(" 【即時日誌】")
            for line in self.logger.history[-8:]:
                print(f" {line}")
            print("=" * 88)
            print(" Ctrl+C 安全退出並撤單")

        except Exception as e:
            # 💡 終極防護：萬一未來畫面上再出錯，直接把錯誤追蹤印在螢幕上，而不是吞掉
            import traceback

            print("\n❌ 儀表板渲染崩潰 (Dashboard Crash):")
            print(traceback.format_exc())

    def _print_leg_table(self, title: str, leg: RollingGridLeg, targets: List[float]):
        print(f" 【{title}】")
        is_twd_market = (
            leg.spec.quote_currency.lower() == "twd"
            or leg.spec.market.lower().endswith("twd")
        )

        # 💡 智慧切換表頭
        if is_twd_market:
            print(
                f" {'基準價格(預估TWD)':<19} | {'BTC量':<9} | {'名目(預估回收)':<19} | {'狀態':<20}"
            )
        else:
            print(
                f" {'價格(USDT)':<22} | {'BTC量':<9} | {'名目(USDT)':<22} | {'狀態':<20}"
            )

        now = time.time()
        shown = set()
        for p in targets:
            slot = leg._get_or_create_slot(p)
            shown.add(p)
            print(f" {_row(slot, leg, now)}")
        for slot in leg.slots_by_price.values():
            if slot["price"] in shown:
                continue
            if slot["status"] == leg.PLACED:
                print(f" {_row(slot, leg, now)}")

    async def shutdown(self):
        self.is_running = False
        self.logger.info("收到關閉指令，正在撤銷所有掛單...")
        await self.buy_leg.cancel_all()
        await self.sell_leg.cancel_all()
        await self.api.close()
        self.logger.info("機器人已安全退出")


def _row(slot: Dict[str, Any], leg: RollingGridLeg, now: float) -> str:
    st = slot["status"]
    if st == leg.IDLE:
        zh = "💤 待掛"
    elif st == leg.PLACED:
        zh = "📌 已掛"
    elif st == leg.REJECTED_COOLDOWN:
        zh = f"⏳ PO {max(0, slot['cooldown_until'] - now):.0f}s"
    else:
        zh = st

    nominal = leg.spec.order_quote_amount

    # 💡 智慧判斷：只有當該網格是台幣市場時，才進行台幣換算顯示
    is_twd_market = (
        leg.spec.quote_currency.lower() == "twd"
        or leg.spec.market.lower().endswith("twd")
    )

    if is_twd_market:
        # 動態匯率
        rate = (
            leg.engine.btc_twd_price / leg.engine.btc_usdt_price
            if leg.engine.btc_usdt_price > 0
            else 0
        )
        est_twd = slot["price"] * rate
        price_disp = f"{slot['price']:.0f} (約{est_twd:.0f}TWD)"

        est_nominal_twd = nominal * rate
        nominal_disp = f"{nominal:.0f}U (約{est_nominal_twd:.0f}TWD)"
    else:
        # 純 USDT 市場，直接顯示美金
        price_disp = f"{slot['price']:.0f}"
        nominal_disp = f"{nominal:.0f} USDT"

    return f"{price_disp:<24} | {slot['volume']:<10.4f} | " f"{nominal_disp:<24} | {zh}"


async def _main():
    engine = DualGridEngine()
    try:
        await engine.run_loop()
    except asyncio.CancelledError:
        pass
    finally:
        # 確保無論如何都會執行安全清理（撤單與寫入 SQLite）
        await engine.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        pass
