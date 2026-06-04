#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
MAX 錢包餘額查詢範例
"""

import asyncio
import json
from urllib.parse import urlencode
from config import Config
from exchange import MaxExchangeClient


async def get_wallet_balance(wallet_type="spot", currency=None):
    """
    取得錢包餘額
    :param wallet_type: 錢包類型 ('spot' 或 'm')
    :param currency: 幣種篩選，例如 'twd', 'btc' (選填，若不傳則取得所有幣種)
    :return: 響應數據
    """
    client = MaxExchangeClient(
        api_key=Config.API_KEY, api_secret=Config.API_SECRET, dry_run=Config.DRY_RUN
    )

    # API 請求路徑
    path = f"/api/v3/wallet/{wallet_type}/accounts"

    # 1. 準備必要的參數
    params = {}
    if currency:
        params["currency"] = currency.lower()

    # 2. 呼叫 client 封裝的簽名與請求機制，避免重複造輪子 (DRY 原則)
    try:
        if client.dry_run:
            print(f"[DRY RUN] 模擬查詢餘額...")
            # 模擬回傳
            simulated = [
                {"currency": "usdt", "balance": "6500.11", "locked": "0.0"},
                {"currency": "btc", "balance": "0.019629", "locked": "0.003618"},
                {"currency": "twd", "balance": "0.6", "locked": "0.0"},
                {"currency": "max", "balance": "176.86", "locked": "0.0"},
            ]
            if currency:
                return [
                    item for item in simulated if item["currency"] == currency.lower()
                ]
            return simulated

        headers, signed_params, _ = client._sign_request(path, params)
        url = f"{client.base_url}{path}?{urlencode(signed_params)}"

        response = await client.client.get(url, headers=headers)
        if response.status_code == 200:
            return response.json()
        else:
            try:
                return response.json()
            except Exception:
                return {
                    "error": f"HTTP status code: {response.status_code}",
                    "message": response.text,
                }
    except Exception as e:
        print(f"發生錯誤: {e}")
        return None
    finally:
        await client.close()


async def main():
    try:
        print("正在取得現貨錢包所有餘額...")
        balances = await get_wallet_balance(wallet_type="spot")

        print("\n現貨錢包餘額結果 (全幣種):")
        print(json.dumps(balances, indent=2, ensure_ascii=False))

        print("\n正在取得現貨錢包 TWD 餘額...")
        twd_balance = await get_wallet_balance(wallet_type="spot", currency="twd")
        print("TWD 餘額結果:")
        print(json.dumps(twd_balance, indent=2, ensure_ascii=False))

    except Exception as error:
        print("發生錯誤:", error)


if __name__ == "__main__":
    asyncio.run(main())
