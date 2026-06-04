#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
from dataclasses import dataclass
from typing import Any, Dict
from config import Config


@dataclass
class GridLegSpec:
    """單邊滾動網格參數"""

    label: str
    market: str
    side: str  # "buy" | "sell"
    grid_upper: float
    grid_lower: float
    step: float
    order_quote_amount: float  # USDT 或 TWD 名目金額
    quote_currency: str  # "usdt" | "twd"
    active_orders: int
    price_decimals: int


def _json_compact(obj: Dict[str, Any]) -> str:
    return json.dumps(obj, separators=(",", ":"))


def fmt_usd(price: float, decimals: int = 2) -> str:
    return f"${price:,.{decimals}f}"


def fmt_twd(price: float, decimals: int = 1) -> str:
    return f"NT${price:,.{decimals}f}"


def fmt_btc(volume: float) -> str:
    s = f"{volume:.{Config.DECIMALS_BTC_VOLUME}f}"
    return s.rstrip("0").rstrip(".") or "0"


def fmt_price_for_market(price: float, market: str, decimals: int) -> str:
    if market.lower() == "btctwd":
        return fmt_twd(price, decimals)
    return fmt_usd(price, decimals)


def quote_unit_for_market(market: str) -> str:
    return "TWD" if market.lower().endswith("twd") else "USDT"
