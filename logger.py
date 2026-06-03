#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import logging
import httpx
from typing import Optional
from timed_size_handler import TimedSizeRotatingFileHandler
from config import Config
from models import fmt_btc, fmt_price_for_market

async def send_telegram_notification(msg: str, force: bool = False):
    # 💡 如果是模擬模式 (DRY_RUN = True) 且未設定強制發送，則直接擋掉
    if not force and getattr(Config, "DRY_RUN", True):
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


def setup_file_logger(log_filename: str):
    """
    設定全域的檔案日誌記錄器 具備自動輪轉功能：每個檔案最大 5MB，最多保留 3 份歷史紀錄，防止硬碟爆滿。
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
