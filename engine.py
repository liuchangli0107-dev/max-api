#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MAX 交易所雙邊滾動網格機器人
- BTCUSDT：觸發後滾動維持 N 筆 Post-Only 買單（報價 USDT，自動換算 BTC）
- BTCTWD：觸發後滾動維持 N 筆 Post-Only 賣單（報價 TWD，自動換算 BTC）
"""

import asyncio
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

try:
    import httpx
except ImportError:
    print("❌ 未安裝 httpx，請執行: pip install httpx")
    sys.exit(1)

_env_file = Path(__file__).parent / ".env"
if _env_file.exists():
    with open(_env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ[k] = v


# ==================== 配置 ====================
class Config:
    # --- BTCUSDT 買入網格 ---
    BUY_MARKET = "btcusdt"
    BUY_TRIGGER_PRICE = 76500.0       # 市價 < 此值時啟動買入網格
    BUY_GRID_UPPER = 76000.0          # 買單最高價（含）
    BUY_GRID_LOWER = 68000.0          # 買單最低價（含）
    BUY_GRID_STEP = 500.0
    BUY_ORDER_AMOUNT_USDT = 50.0      # 每單花費 USDT（自動換算 BTC 數量）
    BUY_ACTIVE_ORDERS = 5

    # --- BTCTWD 賣出網格 ---
    SELL_MARKET = "btctwd"
    SELL_TRIGGER_PRICE = 2440000.0    # 市價 > 此值時啟動賣出網格
    SELL_GRID_LOWER = 2450000.0       # 賣單最低價（含）
    SELL_GRID_UPPER = 2950000.0       # 賣單最高價（含）
    SELL_GRID_STEP = 5000.0
    SELL_ORDER_AMOUNT_TWD = 1200.0    # 每單賣出 TWD 名目（自動換算 BTC 數量）
    SELL_ACTIVE_ORDERS = 5

    # --- 共用 ---
    SPIKE_THRESHOLD_USDT = 1000.0
    CIRCUIT_BREAKER_COOLDOWN = 15
    POST_ONLY_RETRY_COOLDOWN = 10
    BAD_DATA_THRESHOLD_PCT = 0.15
    
    # 手續費相關設定
    FEE_BUFFER_PCT = 0.0              # 改為 0.0，因為外扣 MAX Token，不需多鎖定主資產餘額
    FEE_RATE_MAX_TOKEN = 0.00045      # 0.045% 預估 Maker 手續費率 (使用 MAX Token 支付)

    API_KEY = os.environ.get("MAX_ACCESS_KEY") or os.environ.get("MAX_API_KEY", "")
    API_SECRET = os.environ.get("MAX_SECRET_KEY") or os.environ.get("MAX_API_SECRET", "")

    DRY_RUN = False
    DRY_RUN_INITIAL_USDT = 2000.0
    DRY_RUN_INITIAL_BTC = 0.05
    DRY_RUN_INITIAL_TWD = 0.0

    DECIMALS_BTC_USDT_PRICE = 2
    DECIMALS_BTC_TWD_PRICE = 0
    DECIMALS_BTC_VOLUME = 4

    # --- 日誌 ---
    LOG_TO_FILE = True
    LOG_FILE_NAME = "grid_bot.log"
    LOG_HISTORY_MAX = 80
    MA50_ENABLED = True
    MA50_KLINE_PERIOD = 1440   # 日 K（分鐘）
    MA50_LENGTH = 50


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


class GridLogger:
    """結構化日誌：寫入檔案 + 儀表板顯示緩衝"""

    def __init__(self, log_path: Optional[Path], max_history: int, write_file: bool):
        self.log_path = log_path
        self.max_history = max_history
        self.write_file = write_file
        self.history: List[str] = []

    def _emit(self, line: str) -> None:
        self.history.append(line)
        if len(self.history) > self.max_history:
            self.history.pop(0)
        if self.write_file and self.log_path:
            ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
            try:
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(f"{ts} {line}\n")
            except OSError:
                pass

    def info(self, msg: str) -> None:
        self._emit(f"[INFO] {msg}")

    def warn(self, msg: str) -> None:
        self._emit(f"[WARN] {msg}")

    def error(self, msg: str) -> None:
        self._emit(f"[ERROR] {msg}")

    def api_submit(self, side: str, market: str, price: float, volume: float, decimals: int) -> None:
        side_u = side.upper()
        mkt = market.upper()
        px = fmt_price_for_market(price, market, decimals)
        self._emit(
            f"🚀 [API] 送出掛單: {side_u} {mkt} - 價格: {px}，數量: {fmt_btc(volume)} BTC"
        )

    def order_success(self, order_id: int, post_only: bool = True, dry_run: bool = False, est_fee_twd: float = 0.0) -> None:
        tag = "Maker - Post-Only" if post_only else "Maker"
        suffix = " (DRY-RUN 模擬)" if dry_run else ""
        fee_str = f" | 預估手續費: NT${est_fee_twd:.2f}" if est_fee_twd > 0 else ""
        self._emit(f"📌 [SUCCESS] MAX 訂單建立成功 - ID: {order_id} ({tag}){suffix}{fee_str}")

    def order_cancel(self, market: str, price: float, decimals: int, order_id: Optional[int] = None) -> None:
        oid = f" ID: {order_id}" if order_id else ""
        self._emit(
            f"↩️ [API] 撤銷掛單: {market.upper()} @ "
            f"{fmt_price_for_market(price, market, decimals)}{oid}"
        )

    def order_filled(self, side: str, market: str, price: float, volume: float, decimals: int, est_fee_twd: float = 0.0) -> None:
        fee_str = f" (預估手續費: NT${est_fee_twd:.2f})" if est_fee_twd > 0 else ""
        self._emit(
            f"✅ [FILLED] {side.upper()} {market.upper()} @ "
            f"{fmt_price_for_market(price, market, decimals)}，數量: {fmt_btc(volume)} BTC{fee_str}"
        )


class MaxExchangeClient:
    def __init__(self, api_key: str, api_secret: str, dry_run: bool):
        self.api_key = api_key
        self.api_secret = api_secret
        self.dry_run = dry_run
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
        except Exception:
            pass
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
        except Exception:
            pass
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
        formatted_volume = f"{volume:.{Config.DECIMALS_BTC_VOLUME}f}"

        if self.dry_run:
            if post_only and side == "buy" and current_market_price > 0 and price >= current_market_price:
                raise Exception("POST_ONLY_REJECTED: 買價 >= 市價")
            if post_only and side == "sell" and current_market_price > 0 and price <= current_market_price:
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
    side: str                    # "buy" | "sell"
    grid_upper: float
    grid_lower: float
    step: float
    order_quote_amount: float    # USDT 或 TWD 名目金額
    quote_currency: str          # "usdt" | "twd"
    trigger_price: float
    trigger_above: bool          # False=市價低於觸發啟動買; True=市價高於觸發啟動賣
    active_orders: int
    price_decimals: int


class RollingGridLeg:
    IDLE = "IDLE"
    PLACED = "PLACED"
    REJECTED_COOLDOWN = "REJECTED_COOLDOWN"

    def __init__(self, spec: GridLegSpec, api: MaxExchangeClient, engine: "DualGridEngine"):
        self.spec = spec
        self.api = api
        self.engine = engine
        self.market_price = 0.0
        self.activated = False
        self.slots_by_price: Dict[float, Dict[str, Any]] = {}
        self._validate()

    def _validate(self):
        s = self.spec
        if s.grid_lower > s.grid_upper:
            raise ValueError(f"{s.label}: grid_lower 不可大於 grid_upper")
        if s.step <= 0:
            raise ValueError(f"{s.label}: step 必須 > 0")

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
        """買：市價下方；賣：市價上方"""
        m = self.market_price
        if m <= 0:
            return []
        if self.spec.side == "buy":
            return [p for p in self._all_grid_prices() if p < m]
        return [p for p in self._all_grid_prices() if p > m]

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
        t = self.spec.trigger_price
        if self.spec.trigger_above:
            return m > t
        return m < t

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
            return
        if self.spec.side == "buy":
            self.engine.frozen_usdt = max(
                0.0, self.engine.frozen_usdt - slot.get("frozen_quote", self.spec.order_quote_amount)
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
                    self.engine.frozen_usdt - slot.get("frozen_quote", self.spec.order_quote_amount),
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
            self._on_fill(slot)
            
            # 計算成交時的預估台幣手續費並累加到 Engine 統計中
            if self.spec.side == "buy":
                est_fee_twd = slot["volume"] * self.engine.btc_twd_price * Config.FEE_RATE_MAX_TOKEN
            else:
                est_fee_twd = self.spec.order_quote_amount * Config.FEE_RATE_MAX_TOKEN
            
            self.engine.total_fee_twd += est_fee_twd

            self.engine.logger.order_filled(
                self.spec.side,
                self.spec.market,
                slot["price"],
                slot["volume"],
                self.spec.price_decimals,
                est_fee_twd=est_fee_twd
            )
            slot["status"] = self.IDLE
            slot["order_id"] = None

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
        if not self.activated:
            return

        now = time.time()
        targets = self.compute_target_prices()
        target_set = set(targets)

        if not targets:
            hint = "低於下限" if self.spec.side == "buy" else "高於上限"
            self.engine.logger.warn(
                f"[{self.spec.label}] 無可用掛單價（市價可能已{hint}）"
            )
            return

        for slot in list(self.slots_by_price.values()):
            if slot["status"] != self.PLACED:
                continue
            if slot["price"] in target_set:
                continue
            oid = slot["order_id"]
            if oid:
                try:
                    await self.api.cancel_order(oid)
                    self._on_cancel_place(slot)
                    self.engine.logger.order_cancel(
                        self.spec.market, slot["price"], self.spec.price_decimals, oid
                    )
                except Exception as e:
                    self.engine.logger.error(
                        f"[{self.spec.label}] 撤單 @{slot['price']} 失敗: {e}"
                    )
                    continue
            slot["status"] = self.IDLE
            slot["order_id"] = None

        for price in targets:
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
                px = fmt_price_for_market(price, self.spec.market, self.spec.price_decimals)
                self.engine.logger.warn(f"[{self.spec.label}] {cur} 餘額不足，略過 {px}")
                continue
            await self._try_place(slot)

    async def _try_place(self, slot: Dict[str, Any]) -> bool:
        price = slot["price"]
        vol = slot["volume"]
        log = self.engine.logger
        log.api_submit(
            self.spec.side, self.spec.market, price, vol, self.spec.price_decimals
        )

        # 計算該訂單建立時的預估台幣手續費金額
        if self.spec.side == "buy":
            est_fee_twd = vol * self.engine.btc_twd_price * Config.FEE_RATE_MAX_TOKEN
        else:
            est_fee_twd = self.spec.order_quote_amount * Config.FEE_RATE_MAX_TOKEN

        try:
            order = await self.api.place_order(
                market=self.spec.market,
                side=self.spec.side,
                price=price,
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
            log.order_success(oid, post_only=True, dry_run=Config.DRY_RUN, est_fee_twd=est_fee_twd)
            return True
        except Exception as e:
            err = str(e)
            px = fmt_price_for_market(price, self.spec.market, self.spec.price_decimals)
            if "POST_ONLY_REJECTED" in err:
                slot["status"] = self.REJECTED_COOLDOWN
                slot["cooldown_until"] = time.time() + Config.POST_ONLY_RETRY_COOLDOWN
                slot["order_id"] = None
                log.warn(
                    f"[{self.spec.label}] Post-Only 被拒絕 {px}，"
                    f"冷卻 {Config.POST_ONLY_RETRY_COOLDOWN}s"
                )
            else:
                log.error(f"[{self.spec.label}] 下單失敗 {px}: {err}")
            return False

    def placed_count(self) -> int:
        return sum(1 for s in self.slots_by_price.values() if s["status"] == self.PLACED)

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
            except Exception as e:
                self.engine.logger.error(
                    f"[{self.spec.label}] 撤單 @{slot['price']} 失敗: {e}"
                )
            slot["status"] = self.IDLE
            slot["order_id"] = None


class DualGridEngine:
    def __init__(self):
        self.config = Config()
        self.api = MaxExchangeClient(
            self.config.API_KEY, self.config.API_SECRET, self.config.DRY_RUN
        )

        self.buy_leg = RollingGridLeg(
            GridLegSpec(
                label="BTCUSDT買",
                market=self.config.BUY_MARKET,
                side="buy",
                grid_upper=self.config.BUY_GRID_UPPER,
                grid_lower=self.config.BUY_GRID_LOWER,
                step=self.config.BUY_GRID_STEP,
                order_quote_amount=self.config.BUY_ORDER_AMOUNT_USDT,
                quote_currency="usdt",
                trigger_price=self.config.BUY_TRIGGER_PRICE,
                trigger_above=False,
                active_orders=self.config.BUY_ACTIVE_ORDERS,
                price_decimals=self.config.DECIMALS_BTC_USDT_PRICE,
            ),
            self.api,
            self,
        )
        self.sell_leg = RollingGridLeg(
            GridLegSpec(
                label="BTCTWD賣",
                market=self.config.SELL_MARKET,
                side="sell",
                grid_upper=self.config.SELL_GRID_UPPER,
                grid_lower=self.config.SELL_GRID_LOWER,
                step=self.config.SELL_GRID_STEP,
                order_quote_amount=self.config.SELL_ORDER_AMOUNT_TWD,
                quote_currency="twd",
                trigger_price=self.config.SELL_TRIGGER_PRICE,
                trigger_above=True,
                active_orders=self.config.SELL_ACTIVE_ORDERS,
                price_decimals=self.config.DECIMALS_BTC_TWD_PRICE,
            ),
            self.api,
            self,
        )

        self.is_running = True
        self.btc_usdt_price = 0.0
        self.last_btc_usdt_price = 0.0
        self.btc_twd_price = 0.0

        self.balance_usdt = self.config.DRY_RUN_INITIAL_USDT
        self.balance_btc = self.config.DRY_RUN_INITIAL_BTC
        self.balance_twd = self.config.DRY_RUN_INITIAL_TWD
        self.frozen_usdt = 0.0
        self.frozen_btc = 0.0
        self.total_fee_twd = 0.0      # 新增：用於統計已成交訂單的預估累計台幣手續費

        self.circuit_breaker_active = False
        self.circuit_breaker_until = 0.0

        log_path = Path(__file__).parent / self.config.LOG_FILE_NAME
        self.logger = GridLogger(
            log_path if self.config.LOG_TO_FILE else None,
            self.config.LOG_HISTORY_MAX,
            self.config.LOG_TO_FILE,
        )
        self._ma50_logged = False

        self.logger.info("雙邊滾動網格機器人就緒")
        self.logger.info(
            f"BTCUSDT 買入網格: {fmt_usd(self.config.BUY_GRID_LOWER, 0)}"
            f" ~ {fmt_usd(self.config.BUY_GRID_UPPER, 0)}，"
            f"步長 {fmt_usd(self.config.BUY_GRID_STEP, 0)}，"
            f"每單 {fmt_usd(self.config.BUY_ORDER_AMOUNT_USDT)}，"
            f"BUY_TRIGGER {fmt_usd(self.config.BUY_TRIGGER_PRICE, 0)}"
        )
        self.logger.info(
            f"BTCTWD 賣出網格: {fmt_twd(self.config.SELL_GRID_LOWER)}"
            f" ~ {fmt_twd(self.config.SELL_GRID_UPPER)}，"
            f"步長 {fmt_twd(self.config.SELL_GRID_STEP)}，"
            f"每單 {fmt_twd(self.config.SELL_ORDER_AMOUNT_TWD)}，"
            f"SELL_TRIGGER {fmt_twd(self.config.SELL_TRIGGER_PRICE)}"
        )
        if self.config.LOG_TO_FILE:
            self.logger.info(f"日誌寫入: {log_path}")

    async def _log_ma50_context(self, market_price: float) -> None:
        if not self.config.MA50_ENABLED or self._ma50_logged:
            return
        klines = await self.api.get_klines(
            self.config.BUY_MARKET,
            period=self.config.MA50_KLINE_PERIOD,
            limit=self.config.MA50_LENGTH,
        )
        if len(klines) < self.config.MA50_LENGTH:
            self.logger.warn(
                f"[MA50對齊偵測] K 線資料不足 ({len(klines)}/{self.config.MA50_LENGTH})，略過"
            )
            return
        closes = [float(k[4]) for k in klines[-self.config.MA50_LENGTH :]]
        ma50 = sum(closes) / len(closes)
        diff_pct = (market_price - ma50) / ma50 * 100 if ma50 else 0.0
        if market_price < ma50:
            rel = f"低於均線 {abs(diff_pct):.2f}%"
        elif market_price > ma50:
            rel = f"高於均線 {diff_pct:.2f}%"
        else:
            rel = "貼近均線"
        self.logger.info(
            f"[MA50對齊偵測] 當前 50 日均線位於 {fmt_usd(ma50, 0)}，"
            f"現價 {fmt_usd(market_price)}（{rel}）"
        )
        self._ma50_logged = True

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
                if self.btc_usdt_price > 0:
                    chg = abs(usdt_p - self.btc_usdt_price)
                    pct = chg / self.btc_usdt_price
                    if pct >= self.config.BAD_DATA_THRESHOLD_PCT:
                        self.logger.warn(f"疑似 API 髒數據，單輪變動 {pct*100:.1f}%，略過報價")
                        await asyncio.sleep(1.0)
                        continue
                    if chg >= self.config.SPIKE_THRESHOLD_USDT and not self.circuit_breaker_active:
                        self.circuit_breaker_active = True
                        self.circuit_breaker_until = now + self.config.CIRCUIT_BREAKER_COOLDOWN
                        self.logger.warn(
                            f"波動熔斷觸發：單輪變動 {fmt_usd(chg)}，"
                            f"暫停交易 {self.config.CIRCUIT_BREAKER_COOLDOWN}s"
                        )

                self.last_btc_usdt_price = self.btc_usdt_price or usdt_p
                self.btc_usdt_price = usdt_p
                self.btc_twd_price = twd_p
                self.buy_leg.market_price = usdt_p
                self.sell_leg.market_price = twd_p

                if self.circuit_breaker_active:
                    if now >= self.circuit_breaker_until:
                        self.circuit_breaker_active = False
                        self.logger.info("波動熔斷已解除，恢復網格調度")
                    else:
                        self.draw_dashboard()
                        await asyncio.sleep(1.0)
                        continue

                if not self.buy_leg.activated and self.buy_leg.is_triggered():
                    self.buy_leg.activated = True
                    self.logger.info(
                        f"價格低於 BUY_TRIGGER_PRICE ({fmt_usd(self.config.BUY_TRIGGER_PRICE, 0)})，"
                        f"當前 {fmt_usd(usdt_p)}，啟動買入網格調度..."
                    )
                    await self._log_ma50_context(usdt_p)
                    targets = self.buy_leg.compute_target_prices()
                    if targets:
                        px_list = ", ".join(fmt_usd(p, 0) for p in targets)
                        self.logger.info(f"買入滾動目標價: {px_list}")

                if not self.sell_leg.activated and self.sell_leg.is_triggered():
                    self.sell_leg.activated = True
                    self.logger.info(
                        f"價格高於 SELL_TRIGGER_PRICE ({fmt_twd(self.config.SELL_TRIGGER_PRICE)})，"
                        f"當前 {fmt_twd(twd_p)}，啟動賣出網格調度..."
                    )
                    targets = self.sell_leg.compute_target_prices()
                    if targets:
                        px_list = ", ".join(fmt_twd(p) for p in targets)
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
        os.system("cls" if os.name == "nt" else "clear")
        mode = "🧪 模擬" if self.config.DRY_RUN else "🔴 實盤"
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

        sec = self.btc_usdt_price - self.last_btc_usdt_price if self.last_btc_usdt_price else 0
        sec_s = f"+{sec:.2f}" if sec >= 0 else f"{sec:.2f}"

        print("=" * 88)
        print("   MAX 雙邊滾動網格（BTCUSDT 買 / BTCTWD 賣）")
        print("=" * 88)
        print(f" {status} | {mode}")
        print(
            f" BTCUSDT {self.btc_usdt_price:.2f} ({sec_s}) "
            f"觸發<{self.config.BUY_TRIGGER_PRICE:.0f} | "
            f"BTCTWD {self.btc_twd_price:.0f} 觸發>{self.config.SELL_TRIGGER_PRICE:.0f}"
        )
        print("-" * 88)
        buy_t = self.buy_leg.compute_target_prices()
        sell_t = self.sell_leg.compute_target_prices()
        if buy_t:
            print(
                f" [買] 區間 {self.config.BUY_GRID_LOWER:.0f}~{self.config.BUY_GRID_UPPER:.0f} "
                f"步長{self.config.BUY_GRID_STEP:.0f} 每單{self.config.BUY_ORDER_AMOUNT_USDT:.0f}U"
            )
            print(f"      目標: {', '.join(f'{p:.0f}' for p in buy_t)}")
        if sell_t:
            print(
                f" [賣] 區間 {self.config.SELL_GRID_LOWER:.0f}~{self.config.SELL_GRID_UPPER:.0f} "
                f"步長{self.config.SELL_GRID_STEP:.0f} 每單{self.config.SELL_ORDER_AMOUNT_TWD:.0f}TWD"
            )
            print(f"      目標: {', '.join(f'{p:.0f}' for p in sell_t)}")
        print("-" * 88)
        if self.config.DRY_RUN:
            print(
                f" USDT {self.balance_usdt:.2f} (凍結{self.frozen_usdt:.0f}) | "
                f"BTC {self.balance_btc:.6f} (凍結{self.frozen_btc:.6f}) | "
                f"TWD {self.balance_twd:.0f}"
            )
        print(f" 📊 累計預估手續費: NT${self.total_fee_twd:.2f} (以 MAX Token 支付金額等值折算)")
        print("-" * 88)
        self._print_leg_table("BTCUSDT 買入", self.buy_leg, buy_t)
        print("-" * 88)
        self._print_leg_table("BTCTWD 賣出", self.sell_leg, sell_t)
        print("-" * 88)
        print(" 【即時日誌】")
        for line in self.logger.history[-8:]:
            print(f" {line}")
        print("=" * 88)
        print(" Ctrl+C 安全退出並撤單")

    def _print_leg_table(self, title: str, leg: RollingGridLeg, targets: List[float]):
        print(f" 【{title}】")
        print(f" {'價格':<12} | {'BTC量':<10} | {'名目':<12} | {'狀態':<20}")
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
    unit = "USDT" if leg.spec.quote_currency == "usdt" else "TWD"
    nominal = leg.spec.order_quote_amount
    return (
        f"{slot['price']:<12} | {slot['volume']:<10.4f} | "
        f"{nominal:.0f} {unit:<6} | {zh}"
    )


async def _main():
    engine = DualGridEngine()
    try:
        await engine.run_loop()
    except KeyboardInterrupt:
        pass
    finally:
        await engine.shutdown()


if __name__ == "__main__":
    asyncio.run(_main())