#!/usr/bin/env python3
import asyncio
import httpx
import os
from engine import MaxExchangeClient, Config

async def analyze_market():
    client = MaxExchangeClient(Config.API_KEY, Config.API_SECRET, Config.DRY_RUN)
    market = "btcusdt"
    
    # 獲取價格
    tickers = await client.get_tickers_batch([market])
    price = tickers.get(market, 0.0)
    
    # 獲取 K 線數據計算 MA (以 1440 分鐘為單元，即日 K)
    klines = await client.get_klines(market, period=1440, limit=100)
    if not klines:
        print("無法獲取 K 線數據")
        return

    # klines: [timestamp, open, high, low, close, volume]
    closes = [float(k[4]) for k in klines]
    
    def get_ma(length):
        return sum(closes[-length:]) / length if len(closes) >= length else 0

    ma20 = get_ma(20)
    ma50 = get_ma(50)
    ma100 = get_ma(100)

    # 排列 (大到小)
    data = [("MA20", ma20), ("MA50", ma50), ("MA100", ma100), ("Price", price)]
    sorted_data = sorted(data, key=lambda x: x[1], reverse=True)

    print(f"\n--- 市場趨勢分析報告 ---")
    print(f"當前價格: ${price:,.2f}")
    print(f"均線排列 (由大到小):")
    for name, val in sorted_data:
        print(f"  {name}: ${val:,.2f}")

    # 狀態判斷
    status = "震盪整理"
    advice = "觀察為主"

    # 多頭排列
    if price > ma20 > ma50 > ma100:
        status = "多頭排列 (Bullish)"
        advice = "市場主升段，順勢操作或分批止盈。"
    # 空頭排列
    elif price < ma20 < ma50 < ma100:
        status = "空頭排列 (Bearish)"
        advice = "空頭趨勢明顯，反彈即是賣點。"
    # 多頭修正
    elif ma20 > ma50 > price > ma100:
        status = "多頭修正中"
        advice = "牛市修正，MA50為天花板，MA100為支撐地板。"
    # 均線糾結
    elif abs(ma20 - ma100) / ma100 < 0.02:
        status = "均線糾結"
        advice = "多空分歧嚴重，變盤前夕，等待突破再進場。"
    
    print(f"\n狀態: {status}")
    print(f"操作啟示: {advice}")
    await client.close()

if __name__ == "__main__":
    asyncio.run(analyze_market())
