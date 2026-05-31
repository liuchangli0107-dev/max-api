#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MAX 餘額監控腳本
定時查詢並將餘額持久化至 SQLite
"""

import asyncio
import os
import time
from pathlib import Path
from engine import Config, MaxExchangeClient, GridDatabaseService

async def record_balances():
    # 初始化資料庫服務
    db_service = GridDatabaseService(db_file=Config.DB_FILE)
    
    # 初始化交易所客戶端
    client = MaxExchangeClient(
        api_key=Config.API_KEY,
        api_secret=Config.API_SECRET,
        dry_run=Config.DRY_RUN
    )

    try:
        # 取得餘額
        balances = await client.get_spot_balances()
        
        # 寫入資料庫
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        for currency, balance in balances.items():
            db_service.record_balance(currency, balance)
        
        print(f"[{timestamp}] 成功紀錄餘額: {balances}")

    except Exception as e:
        print(f"紀錄餘額失敗: {e}")
    finally:
        await client.close()

if __name__ == "__main__":
    asyncio.run(record_balances())
