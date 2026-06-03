#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import time
from typing import Any, Dict, List
from config import Config

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
        if engine.usdt_twd_price > 0:
            usdt_twd = engine.usdt_twd_price / engine.usdt_twd_price

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
