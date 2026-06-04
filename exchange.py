#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import asyncio
import base64
import hashlib
import hmac
import time
import httpx
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode
from config import Config
from models import _json_compact


class MaxExchangeClient:
    """
    封裝與 MAX 交易所 API 的溝通邏輯，處理 Request 簽名（HMAC-SHA256）、認證頭部資訊（Headers）生成及非同步 HTTP 請求執行。
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
            r = await self.client.get(
                url, params=params, headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code == 200:
                return {t["market"]: float(t.get("last", 0.0)) for t in r.json()}
            elif r.status_code == 503:
                if self.engine and hasattr(self.engine, "logger"):
                    self.engine.logger.warn(
                        "交易所維護中 (503 Service Unavailable)，暫停監控..."
                    )
                await asyncio.sleep(60)
            else:
                if self.engine and hasattr(self.engine, "logger"):
                    self.engine.logger.error(f"獲取失敗: {r.status_code} - {r.text}")

        except Exception as e:
            if self.engine and hasattr(self.engine, "logger"):
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
                if self.engine and hasattr(self.engine, "logger"):
                    self.engine.logger.warn(
                        "交易所維護中 (503 Service Unavailable)，暫停監控..."
                    )
                await asyncio.sleep(60)
        except Exception as e:
            if self.engine and hasattr(self.engine, "logger"):
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
        if self.engine and hasattr(self.engine, "logger"):
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
        if side.lower() == "buy":
            # 買單：減少 BTC 數量（BTC = USDT / Price * (1 - fee)）
            actual_volume = volume * (1 - Config.FEE_RATE_MAX_TOKEN)
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
