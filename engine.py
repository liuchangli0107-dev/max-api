#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MAX 交易所雙邊滾動網格機器人
"""

import asyncio
import os
import sys
import time
from typing import Any, Dict, List

from config import Config
from models import GridLegSpec, fmt_price_for_market
from logger import GridLogger
from exchange import MaxExchangeClient
from grid_leg import RollingGridLeg, _row


class DualGridEngine:
    """
    機器人的主引擎（Main Engine），負責協調買賣雙邊（Buy/Sell Leg）、處理市場數據更新、執行儀表板渲染及控制自動化交易的運行循環（Run Loop）。
    """

    def __init__(self):
        self.config = Config()
        self.api = MaxExchangeClient(
            getattr(self.config, "API_KEY", ""),
            getattr(self.config, "API_SECRET", ""),
            self.config.DRY_RUN,
            engine=self,
        )
        
        self.start_time = time.time()

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
        self.balance_max = 0.0
        self.frozen_usdt = 0.0
        self.frozen_btc = 0.0
        self.total_fee_twd = 0.0  # 用於統計已成交訂單的預估累計台幣手續費

        # 熔斷狀態
        self.circuit_breaker_active = False
        self.circuit_breaker_until = 0.0

        # 💡 使用新版且乾淨的 Logger 實例化
        self.logger = GridLogger(max_history=5)
        self._ma50_logged = False
        self.initial_balances = {}

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
        if getattr(c, "FEE_RATE_MAX_TOKEN", 0) < 0:
            errors.append("FEE_RATE_MAX_TOKEN 不可為負數")

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
        if not getattr(c, "API_KEY", "") or not getattr(c, "API_SECRET", ""):
            errors.append("實盤模式需要設定 API_KEY / API_SECRET")
        if (
            getattr(c, "BUY_MARKET", "").lower()
            == getattr(c, "SELL_MARKET", "").lower()
        ):
            warns.append(
                "BUY_MARKET 與 SELL_MARKET 相同，將在同一市場同時掛買/賣，請確認策略意圖"
            )

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
            if (
                self.balance_usdt <= 0
                and self.balance_btc <= 0
                and self.balance_twd <= 0
            ):
                balance_info = await self.api.get_spot_balances()
                self.balance_usdt = balance_info.get("usdt", 0.0)
                self.balance_btc = balance_info.get("btc", 0.0)
                self.balance_twd = balance_info.get("twd", 0.0)
                self.balance_max = balance_info.get("max", 0.0)
            return {
                "usdt": self.balance_usdt,
                "btc": self.balance_btc,
                "twd": self.balance_twd,
                "max": self.balance_max,
            }
        return await self.api.get_spot_balances()

    async def run_loop(self):
        self.logger.info("開始監控 BTCUSDT / BTCTWD 行情與網格調度...")
        self.initial_balances = await self.get_balances()
        self.initial_balances['total_twd'] = 0.0
        self.logger.info(f"初始資金記錄完成: {self.initial_balances}")
        while self.is_running:
            try:
                tickers = await self.api.get_tickers_batch(
                    [
                        self.config.BUY_MARKET,
                        self.config.SELL_MARKET,
                        "maxtwd",
                        "maxusdt",
                        "usdttwd",
                    ]
                )
                usdt_p = tickers.get(self.config.BUY_MARKET, 0.0)
                twd_p = tickers.get(self.config.SELL_MARKET, 0.0)
                btc_usdt_p = tickers.get("btcusdt", 0.0)
                btc_twd_p = tickers.get("btctwd", 0.0)
                max_twd_p = tickers.get("maxtwd", 0.0)
                max_usdt_p = tickers.get("maxusdt", 0.0)
                usdt_twd_p = tickers.get("usdttwd", 0.0)
                if not usdt_p or not twd_p:
                    self.logger.error("無法獲取必要的市場價格，略過本輪更新")
                    await asyncio.sleep(1.0)
                    continue
                
                if self.initial_balances['total_twd'] == 0.0:
                    self.initial_balances['total_twd'] = self.initial_balances['usdt'] * usdt_twd_p
                    self.initial_balances['total_twd'] += self.initial_balances['twd']
                    self.initial_balances['total_twd'] += self.initial_balances['btc'] * btc_twd_p
                    self.initial_balances['total_twd'] += self.initial_balances['max'] * max_twd_p
                    self.logger.info(f"初始資金記錄完成: {self.initial_balances}")

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

                self.last_btc_usdt_price = self.btc_usdt_price or btc_usdt_p
                self.btc_usdt_price = btc_usdt_p
                self.btc_twd_price = btc_twd_p
                self.max_twd_price = max_twd_p
                self.max_usdt_price = max_usdt_p
                self.usdt_twd_price = usdt_twd_p

                # 更新 MA50 數據
                self.current_ma50_price, self.current_ma50 = (
                    await self._log_ma50_context(btc_usdt_p, self.config.BUY_MARKET)
                )
                self.current_ma50_twd, self.current_ma50_twd_price = (
                    await self._log_ma50_context(twd_p, self.config.SELL_MARKET)
                )

                # 因為網格上下限與觸發價皆已統一為 USDT，所以雙邊都必須餵入 usdt_p 來做比較！
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
                self.logger.info("主循環已停止")
                break
            except Exception as e:
                self.logger.error(f"主循環異常: {e}")
                await asyncio.sleep(2.0)

    def draw_dashboard(self):
        try:
            os.system("cls" if os.name == "nt" else "clear")
            mode = "🧪 模擬" if getattr(self.config, "DRY_RUN", True) else "🔴 實盤"
            now = time.time()
            elapsed_seconds = int(now - self.start_time)
            hours, remainder = divmod(elapsed_seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            uptime_str = f"⏱️ 運行時間: {hours:02d}:{minutes:02d}:{seconds:02d}"

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

            print("=" * 88)
            print(f"   MAX 雙邊滾動網格（{buy_mkt} 買 / {sell_mkt} 賣）")
            print("=" * 88)
            print(f" {status} | {mode} | {uptime_str}")
            if getattr(self.config, "MA50_ENABLED", False):
                if self.current_ma50 > 0:
                    print(
                        f" {buy_mkt} 50MA: {self.current_ma50:.2f} | 現價: {self.btc_usdt_price:.2f}"
                    )
                if self.current_ma50_twd > 0:
                    print(
                        f" {sell_mkt}  50MA: {self.current_ma50_twd:.1f} | 現價: {self.btc_twd_price:.1f}"
                    )
            else:
                print(
                    f" {buy_mkt} {self.btc_usdt_price:.2f} ({sec_s}) | {sell_mkt} {self.btc_twd_price:.0f}"
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
                if is_twd_market and self.usdt_twd_price > 0:
                    targets_str = ", ".join(
                        f"{p:.0f}U(約{p * self.usdt_twd_price:.0f}TWD)" for p in sell_t
                    )
                else:
                    targets_str = ", ".join(f"{p:.0f}U" for p in sell_t)
                print(f"      目標: {targets_str}")

            print("-" * 88)
            print(
                f" USDT {self.balance_usdt:.2f} (凍結{self.frozen_usdt:.2f}) | "
                f"BTC {self.balance_btc:.6f} (凍結{self.frozen_btc:.6f}) | "
                f"TWD {self.balance_twd:.1f}"
            )
            print(
                f" 📊 累計預估手續費: NT${self.total_fee_twd:.2f} (以 MAX Token 支付金額等值折算)"
            )
            print("-" * 88)

            self._print_leg_table(f"{buy_mkt} 買入", self.buy_leg, buy_t)
            print("-" * 88)
            self._print_leg_table(f"{sell_mkt} 賣出", self.sell_leg, sell_t)
            print("-" * 88)
            print(
                f"   初始資金: USDT: {self.initial_balances['usdt']:.6f} | BTC: {self.initial_balances['btc']:.6f} | "
                f"TWD: {self.initial_balances['twd']:.6f} | MAX: {self.initial_balances['max']:.6f} | "
                f"估值約 {self.initial_balances['total_twd']:.2f}TWD"
            )
            print(
                f"   目前資金: USDT: {self.balance_usdt:.6f} | BTC: {self.balance_btc:.6f} | "
                f"TWD: {self.balance_twd:.6f} | MAX: {self.balance_max:.6f} | "
                f"估值約 {(self.balance_btc * self.btc_twd_price) + (self.balance_usdt * self.usdt_twd_price) + self.balance_twd + (self.balance_max * self.max_twd_price):.2f}TWD | "
                f"預估手續費折算約 {(self.initial_balances['max'] - self.balance_max) * self.max_twd_price:.2f}TWD"
            )
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
