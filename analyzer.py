#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import time
from config import Config
from exchange import MaxExchangeClient
from logger import send_telegram_notification


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

    def get_ma(length, offset=0):
        # 確保數據量足夠計算
        if len(closes) < length + offset:
            return 0
        # 如果是當前最新 (offset=0)
        if offset == 0:
            return sum(closes[-length:]) / length
        # 如果是歷史數據 (例如 offset=1 就是退後一根 K 線)
        return sum(closes[-(length + offset) : -offset]) / length

    # 今天 (最新) 的 MA
    ma20 = get_ma(20, 0)
    ma50 = get_ma(50, 0)
    ma100 = get_ma(100, 0)

    # 昨天 (前一根 K 線) 的 MA
    prev_ma20 = get_ma(20, 1)
    prev_ma50 = get_ma(50, 1)

    # 判斷是否觸發交叉訊號
    cross_signal = ""
    if prev_ma20 <= prev_ma50 and ma20 > ma50:
        cross_signal = "🟢 觸發訊號: MA20 向上穿越 MA50 (黃金交叉)"
    elif prev_ma20 >= prev_ma50 and ma20 < ma50:
        cross_signal = "🔴 觸發訊號: MA20 向下穿越 MA50 (死亡交叉)"

    # 排列 (大到小)
    data = [("MA20", ma20), ("MA50", ma50), ("MA100", ma100), ("Price", price)]
    sorted_data = sorted(data, key=lambda x: x[1], reverse=True)

    now = time.time()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))

    report = f"\n--- 市場趨勢分析報告({now_str}) ---\n"

    cost_price = Config.COST_PRICE
    if cost_price:
        cost_diff_pct = (price - cost_price) / cost_price * 100
        cost_rel = f"{cost_diff_pct:+.2f}%"
        report += f"當前價格: ${price:,.2f} (成本價: ${cost_price:,.2f}, {cost_rel})\n"
    else:
        report += f"當前價格: ${price:,.2f}\n"

    report += f"均線排列 (由大到小):\n"
    for name, val in sorted_data:
        diff_pct = (price - val) / val * 100 if val else 0.0
        rel = f"{diff_pct:+.2f}%"
        report += f"  {name}: ${val:,.2f} ({rel})\n"

    # 狀態判斷
    status = "震盪整理"
    advice = "觀察為主"

    # 多頭排列
    if price > ma20 > ma50 > ma100:
        status = "多頭排列 (Bullish)"
        advice = "市場處於主升段，趨勢極強，支撐力道通常在 MA20 或 MA50 附近就會出現。這時的操作策略通常是「順勢而為」或「分批止盈」。"
    # 空頭排列
    elif price < ma20 < ma50 < ma100:
        status = "空頭排列 (Bearish)"
        advice = "空頭趨勢明顯，反彈即是賣點。如果現價正在逼近 MA20，代表短期回檔壓力大，容易「遇線下殺」。"
    # 多頭修正
    elif ma20 > ma50 > price > ma100:
        status = "多頭修正中"
        advice = "這通常是洗盤區間。只要現價不跌破 MA100，長線牛市架構就沒壞。您的網格機器人將買單區間設在 MA50 以下到 MA100 之間，正好就是利用了這種「修正行情」進行分批接單。"
    # 均線糾結
    elif abs(ma20 - ma100) / ma100 < 0.02:
        status = "均線糾結 (Convergence)"
        advice = "此時不適合預測漲跌，而應採取「觀望」或「突破策略」（等待價格強勢突破糾結區間後再進場）。"

    report += f"\n狀態: {status}\n"
    report += f"操作啟示: {advice}\n"

    # 如果有交叉訊號，特別標註出來
    if cross_signal:
        report += f"{cross_signal}\n"

    print(report)

    # 發送 Telegram 通知
    await send_telegram_notification(report, force=True)

    await client.close()


async def main_loop():
    print("啟動 MAX 交易所監控服務...")
    while True:
        try:
            # 執行原本的分析邏輯
            await analyze_market()

        except Exception as e:
            print(f"執行發生錯誤: {e}")

        # 暫停一段時間再執行下一次 (例如 3600 秒 = 1 小時)
        print("進入休眠，等待下一次分析...")
        await asyncio.sleep(Config.SLEEP_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main_loop())
