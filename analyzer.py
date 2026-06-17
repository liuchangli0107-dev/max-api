#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import os
import time
from config import Config
from exchange import MaxExchangeClient
from logger import GridLogger, send_telegram_notification


async def analyze_market(price_list=[]):
    client = MaxExchangeClient(Config.API_KEY, Config.API_SECRET, Config.DRY_RUN)

    markets = list(price_list.keys())
    tickers = await client.get_tickers_batch(markets)

    log_file = f"analyzer_{time.strftime('%Y%m%d%H%M%S')}.log"
    logger = GridLogger(max_history=5, log_file=log_file)

    import html
    report = "MAX 市場趨勢分析報告\n\n"
    tg_report = "<b>MAX 市場趨勢分析報告</b>\n\n"
    
    for market in markets:

        report += f" 📊 {market.upper()}\n"
        tg_report += f"<b>📊 {market.upper()}</b>\n"

        # 獲取價格
        price = tickers.get(market, 0.0)
        report += f"最新價格: ${price:,.2f}\n"
        tg_report += f"最新價格: <code>${price:,.2f}</code>\n"

        cost_price = Config.COST_PRICE
        if cost_price and market == Config.BUY_MARKET:
            cost_diff_pct = (price - cost_price) / cost_price * 100
            cost_rel = f"{cost_diff_pct:+.2f}%"
            report += f"成本價: ${cost_price:,.2f} ({cost_rel})\n"
            tg_report += f"成本價: <code>${cost_price:,.2f} ({cost_rel})</code>\n"

        if price_list[market] is not None:
            one_hour_ago_price = price_list[market]
            price_diff_pct = (
                (price - one_hour_ago_price) / one_hour_ago_price * 100
                if one_hour_ago_price
                else 0.0
            )
            price_rel = f"{price_diff_pct:+.2f}%"
            report += f"1h前價格: ${one_hour_ago_price:,.2f} ({price_rel})\n"
            tg_report += f"1h前價格: <code>${one_hour_ago_price:,.2f} ({price_rel})</code>\n"

        # 獲取 K 線數據計算 MA (以 1440 分鐘為單元，即日 K)
        klines = await client.get_klines(market, period=1440, limit=200)
        if not klines:
            print("無法獲取 K 線數據")
            return

        # klines: [timestamp, open, high, low, close, volume]
        closes = [float(k[4]) for k in klines]
        yesterday_close = closes[-2] if len(closes) > 1 else 0.0
        before_yesterday_close = closes[-3] if len(closes) > 2 else 0.0
        latest_vol = float(klines[-1][5])
        yesterday_vol = float(klines[-2][5])
        before_yesterday_vol = float(klines[-3][5]) if len(klines) > 2 else 0.0
        three_days_prior_vol = float(klines[-4][5]) if len(klines) > 3 else 0.0
        vol_diff_pct = (
            ((latest_vol - yesterday_vol) / yesterday_vol * 100)
            if yesterday_vol
            else 0.0
        )
        vol_diff2_pct = (
            ((yesterday_vol - before_yesterday_vol) / before_yesterday_vol * 100)
            if before_yesterday_vol
            else 0.0
        )
        vol_diff3_pct = (
            ((before_yesterday_vol - three_days_prior_vol) / three_days_prior_vol * 100)
            if three_days_prior_vol
            else 0.0
        )

        # 計算均線的函式
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
        ma200 = get_ma(200, 0)

        # 昨天 (前一根 K 線) 的 MA
        prev_ma20 = get_ma(20, 1)
        prev_ma50 = get_ma(50, 1)
        
        convergence = abs(ma20 - ma100) / ma100 if ma100 else 0.0
        report += f"均線寬度: {convergence:.2%}\n"
        tg_report += f"均線寬度: <code>{convergence:.2%}</code>\n"

        # 排列 (大到小)
        data = [("MA20", ma20), ("MA50", ma50), ("MA100", ma100), ("MA200", ma200), ("Price", price)]
        sorted_data = sorted(data, key=lambda x: x[1], reverse=True)
        report += f"均線排列 (由大到小):\n"
        tg_report += f"均線排列:\n"
        for name, val in sorted_data:
            diff_pct = (price - val) / val * 100 if val else 0.0
            if name == "Price":
                rel = ""
            else:
                rel = f"({diff_pct:+.2f}%)"
            report += f"  {name}: ${val:,.2f} {rel}\n"
            tg_report += f"  {name}: <code>${val:,.2f} {rel}</code>\n"

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
        elif convergence < 0.02:
            status = f"均線糾結 (Convergence)"
            advice = "此時不適合預測漲跌，而應採取「觀望」或「突破策略」（等待價格強勢突破糾結區間後再進場）。"

        report += f"狀態: {status}\n"
        report += f"操作啟示: {advice}\n"
        
        tg_report += f"狀態: <b>{html.escape(status)}</b>\n"
        tg_report += f"操作啟示: <i>{html.escape(advice)}</i>\n\n"

        # 報表 Table - Console version
        report += "| 項目 | 價格 | 交易量 | 變動率 |\n"
        report += (
            f"| 最新 | ${price:,.2f} | {latest_vol:,.4f} | {vol_diff_pct:+.2f}% |\n"
        )
        report += f"| 昨天 | ${yesterday_close:,.2f} | {yesterday_vol:,.4f} | {vol_diff2_pct:+.2f}% |\n"
        report += f"| 前天 | ${before_yesterday_close:,.2f} | {before_yesterday_vol:,.4f} | {vol_diff3_pct:+.2f}% |\n"

        # 報表 Table - Telegram HTML version (wrapped in <pre> for monospaced alignment)
        table_str = "| 項目 |    價格   | 交易量 |  變動率  |\n"
        table_str += f"| 最新 | ${price:9,.2f} | {latest_vol:6.4f} | {vol_diff_pct:>+7.2f}% |\n"
        table_str += f"| 昨天 | ${yesterday_close:9,.2f} | {yesterday_vol:6.4f} | {vol_diff2_pct:>+7.2f}% |\n"
        table_str += f"| 前天 | ${before_yesterday_close:9,.2f} | {before_yesterday_vol:6.4f} | {vol_diff3_pct:>+7.2f}% |\n"
        
        tg_report += f"<pre>{table_str}</pre>\n"

        # 判斷是否觸發交叉訊號
        cross_signal = ""
        if prev_ma20 <= prev_ma50 and ma20 > ma50:
            cross_signal = "🟢 觸發訊號: MA20 向上穿越 MA50 (黃金交叉)"
        elif prev_ma20 >= prev_ma50 and ma20 < ma50:
            cross_signal = "🔴 觸發訊號: MA20 向下穿越 MA50 (死亡交叉)"

        # 如果有交叉訊號，特別標註出來
        if cross_signal:
            report += f"{cross_signal}\n"
            tg_report += f"<b>{html.escape(cross_signal)}</b>\n"

        report += "\n\n"
        tg_report += "\n\n"
        price_list[market] = price

    now = time.time()
    now_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    report += f"報告生成時間: {now_str}\n\n"
    tg_report += f"<i>報告生成時間: {now_str}</i>\n"
    
    print(report)

    logger.info(report)  # 同時記錄到檔案日誌

    # 發送 Telegram 通知
    await send_telegram_notification(tg_report, force=True, parse_mode="HTML")
    await client.close()
    return price_list


async def main_loop():
    print("啟動 MAX 交易所監控服務...")
    price_list = {
        "btcusdt": None,
        "btctwd": None,
        "usdttwd": None,
    }
    while True:
        try:
            os.system("cls" if os.name == "nt" else "clear")
            # 執行原本的分析邏輯
            price_list = await analyze_market(price_list)
            # 暫停一段時間再執行下一次 (例如 3600 秒 = 1 小時)
            print("進入休眠，等待下一次分析...")
            await asyncio.sleep(Config.SLEEP_INTERVAL)

        except Exception as e:
            print(f"執行發生錯誤: {e}")
        finally:
            print("服務已停止。")


if __name__ == "__main__":
    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        pass
