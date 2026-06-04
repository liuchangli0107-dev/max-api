#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MAX API v3 下單範例
"""

import asyncio
import json
from config import Config
from exchange import MaxExchangeClient


async def create_order(
    wallet_type="spot",
    market="btcusdt",
    side="buy",
    volume="0.001",
    price="20000",
    ord_type="limit",
    client_oid=None,
    stop_price=None,
    group_id=None,
):
    """
    提交買入/賣出訂單 (POST /api/v3/wallet/{path_wallet_type}/order)
    """
    client = MaxExchangeClient(
        api_key=Config.API_KEY, api_secret=Config.API_SECRET, dry_run=Config.DRY_RUN
    )

    path = f"/api/v3/wallet/{wallet_type}/order"

    # 1. 準備必要的參數 (包含 nonce 與請求 Body 內容)
    params = {"market": market.lower(), "side": side.lower(), "volume": str(volume)}

    # 根據訂單類型與傳入參數，動態添加選填欄位
    if ord_type:
        params["ord_type"] = ord_type
    if price is not None:
        params["price"] = str(price)
    if client_oid is not None:
        params["client_oid"] = str(client_oid)
    if stop_price is not None:
        params["stop_price"] = str(stop_price)
    if group_id is not None:
        params["group_id"] = int(group_id)

    # 2. 呼叫 client 封裝的簽名與請求機制，避免重複造輪子 (DRY 原則)
    try:
        if client.dry_run:
            print(f"[DRY RUN] 模擬下單: {params}")
            return {"state": "wait", "id": 123456}

        headers, _, body_json = client._sign_request(path, params)
        url = f"{client.base_url}{path}"

        response = await client.client.post(url, content=body_json, headers=headers)
        print(f"Status Code: {response.status_code}")
        result = response.json()
        print("Response:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result
    except Exception as e:
        print(f"發生錯誤: {e}")
        return None
    finally:
        await client.close()


async def main():
    print("正在提交測試訂單...")
    await create_order(
        wallet_type="spot",
        market="btcusdt",
        side="buy",
        volume="0.001",
        price="20000",
        ord_type="limit",
    )


if __name__ == "__main__":
    asyncio.run(main())
