#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import asyncio
from typing import TYPE_CHECKING, Any, Dict, List, Optional
from config import Config
from db import GridDatabaseService
from models import GridLegSpec, fmt_btc, fmt_price_for_market
from logger import send_telegram_notification

if TYPE_CHECKING:
    from engine import DualGridEngine
    from exchange import MaxExchangeClient

class RollingGridLeg:
    """
    實作單邊滾動網格的邏輯核心，負責計算目標價格區間、監控掛單狀態及執行價格滾動策略。
    """

    IDLE = "IDLE"
    PLACED = "PLACED"
    REJECTED_COOLDOWN = "REJECTED_COOLDOWN"
    CANCEL = "CANCEL"

    def __init__(
        self, spec: GridLegSpec, api: "MaxExchangeClient", engine: "DualGridEngine"
    ):
        self.spec = spec
        self.api = api
        self.engine = engine
        self.market_price = 0.0
        self.activated = False
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

        # 不論是買方(USDT)還是賣方(TWD)，一律看 USDT 的市價與步長來判定區間
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
            self.engine.logger.info(
                f"🚨 [撤單] 正在撤銷訂單 ID: {slot['order_id']}, 價位: {slot['price']}, side: {self.spec.side}"
            )
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

            # 趁 order_id 尚未被 _on_fill 沖掉前，先錄製成交快照 (FILLED)
            self.db_service.record_market_snapshot(
                "FILLED", self.spec, slot, self.engine
            )

            self._on_fill(slot)

            # 動態匯率轉換與精準手續費計算
            rate = (
                self.engine.usdt_twd_price
                if self.engine.usdt_twd_price > 0
                else 0
            )
            fee_rate = getattr(self.engine.config, "FEE_RATE_MAX_TOKEN", 0.00045)

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

    def update_simulated_wallet(
        self, slot: Dict[str, Any], price: float, side: str
    ):
        self.engine.logger.warn(
            f"模擬錢包變動前: price={price}, side={side}, volume={slot['volume']}"
        )
        if not Config.DRY_RUN:
            return
        self.engine.logger.warn(
            f"模擬錢包變動前: USDT={self.engine.balance_usdt:.2f}, BTC={self.engine.balance_btc:.6f}, TWD={self.engine.balance_twd:.2f}, MAX={self.engine.balance_max:.6f}"
        )
        if side == "buy":
            cost = price * slot["volume"]
            fee = cost * Config.FEE_RATE_MAX_TOKEN  # 手續費
            # 買入：扣 USDT (或 TWD)，加 BTC
            if Config.BUY_MARKET.lower() == "btctwd":
                self.engine.balance_twd -= cost
                fee_max = (
                    fee * self.engine.max_twd_price
                    if self.engine.max_twd_price > 0
                    else 0
                )
            else:
                self.engine.balance_usdt -= cost
                fee_max = (
                    fee * self.engine.max_usdt_price
                    if self.engine.max_usdt_price > 0
                    else 0
                )
            self.engine.balance_btc += slot["volume"]
        else:
            # 賣出：減 BTC，加 USDT (或 TWD)
            revenue = price * slot["volume"]
            fee = revenue * Config.FEE_RATE_MAX_TOKEN  # 手續費
            if Config.SELL_MARKET.lower() == "btctwd":
                self.engine.balance_twd += revenue
                fee_max = (
                    fee * self.engine.max_twd_price
                    if self.engine.max_twd_price > 0
                    else 0
                )
            else:
                self.engine.balance_usdt += revenue
                fee_max = (
                    fee * self.engine.max_usdt_price
                    if self.engine.max_usdt_price > 0
                    else 0
                )
            self.engine.balance_btc -= slot["volume"]
        self.engine.balance_max -= fee_max  # 以最大價換算的手續費預留
        self.engine.total_fee_twd += fee_max * self.engine.btc_twd_price  # 累積預估手續費

        # 隨後將最新餘額寫入 DB 或印在 Console
        self.engine.logger.warn(
            f"模擬錢包變動後: USDT={self.engine.balance_usdt:.2f}, BTC={self.engine.balance_btc:.6f}, TWD={self.engine.balance_twd:.2f}, MAX={self.engine.balance_max:.6f}"
        )

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

            if Config.DRY_RUN:
                # 判斷成交條件
                if self.spec.side == "buy":
                    current_price = (
                        self.engine.btc_usdt_price
                        if Config.BUY_MARKET == "btcusdt"
                        else self.engine.btc_twd_price
                    )
                    if current_price <= slot["price"]:
                        self.engine.logger.warn(
                            f"成交條件 {self.spec.side.upper()} @ {slot['price']} 已觸及 (市價: {current_price})，模擬成交中..."
                        )
                        slot["status"] = "filled"
                        self.update_simulated_wallet(slot, current_price, side="buy")

                if self.spec.side == "sell":
                    current_price = (
                        self.engine.btc_usdt_price
                        if Config.SELL_MARKET == "btcusdt"
                        else self.engine.btc_twd_price
                    )
                    if current_price >= slot["price"]:
                        self.engine.logger.warn(
                            f"成交條件 {self.spec.side.upper()} @ {slot['price']} 已觸及 (市價: {current_price})，模擬成交中..."
                        )
                        slot["status"] = "filled"
                        self.update_simulated_wallet(slot, current_price, side="sell")

            oid = slot["order_id"]
            if oid:
                try:
                    # 記錄當下的市場價格快照 (CANCEL)
                    self.db_service.record_market_snapshot(
                        "CANCEL", self.spec, slot, self.engine
                    )
                    self.engine.logger.info(
                        f"🚨 [滾動撤單] 正在撤銷訂單 ID: {oid}, 價位: {slot['price']}"
                    )
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
            # 在 sync_orders 結束時，將所有變動狀態一次性批次寫入
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
            side = self.spec.side.upper()

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
                cur = self.spec.market.upper()
                px = fmt_price_for_market(
                    price, self.spec.market, self.spec.price_decimals
                )
                self.engine.logger.warn(
                    f"[{self.spec.label}] {cur} {side} 餘額不足，略過 {px}"
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
            est_fee_twd = vol * self.engine.btc_twd_price * self.engine.config.FEE_RATE_MAX_TOKEN
        else:
            # 賣單的名目是 50 USDT，必須先乘上即時匯率轉成台幣，再算手續費
            est_fee_twd = (
                self.spec.order_quote_amount * usdt_twd
            ) * self.engine.config.FEE_RATE_MAX_TOKEN

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

    return f"{price_disp:<24} | {slot['volume']:<10.6f} | " f"{nominal_disp:<24} | {zh}"
