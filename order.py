# MAX API v3 下單範例
import base64
import hashlib
import hmac
import json
import os
import time
import requests
from pathlib import Path

# 讀取 .env 檔案
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

# 設定環境變數
MAX_ACCESS_KEY = os.getenv("MAX_ACCESS_KEY")
MAX_SECRET_KEY = os.getenv("MAX_SECRET_KEY")
MAX_URL = "https://max-api.maicoin.com"

def create_order(
    wallet_type='spot',
    market='btcusdt',
    side='buy',
    volume='0.001',
    price='20000',
    ord_type='limit',
    client_oid=None,
    stop_price=None,
    group_id=None
):
    """
    提交買入/賣出訂單 (POST /api/v3/wallet/{path_wallet_type}/order)
    """
    # 檢查環境變數
    if not MAX_ACCESS_KEY or not MAX_SECRET_KEY:
        print("錯誤：請先在 .env 檔案中設置 MAX_ACCESS_KEY 與 MAX_SECRET_KEY")
        return None

    # 依據截圖，API 路徑包含 wallet_type 參數
    path = f"/api/v3/wallet/{wallet_type}/order"

    # 1. 準備必要的參數 (包含 nonce 與請求 Body 內容)
    params = {
        "nonce": int(time.time() * 1000),
        "market": market.lower(),
        "side": side.lower(),
        "volume": str(volume)
    }

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

    # 2. 構建簽名內容 (將參數字典與 path 合併)
    params_to_sign = {**params, "path": path}
    
    # 轉換為 JSON 字串 (去除多餘空格以確保雜湊值一致)
    json_str = json.dumps(params_to_sign, separators=(',', ':'))

    # 3. 生成 payload (Base64 編碼)
    payload = base64.b64encode(json_str.encode()).decode()

    # 4. 計算簽章 (HMAC-SHA256)
    signature = hmac.new(
        MAX_SECRET_KEY.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    # 5. 設置 request header
    headers = {
        "X-MAX-ACCESSKEY": MAX_ACCESS_KEY,
        "X-MAX-PAYLOAD": payload,
        "X-MAX-SIGNATURE": signature,
        "X-Sub-Account": "main",  # 預設為主帳號
        "Content-Type": "application/json"
    }

    # 6. 發送 POST 請求 (參數放在 request body 中)
    url = f"{MAX_URL}{path}"
    
    try:
        response = requests.post(
            url,
            headers=headers,
            data=json.dumps(params, separators=(',', ':'))
        )
        print(f"Status Code: {response.status_code}")
        result = response.json()
        print("Response:")
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return result
    except Exception as e:
        print(f"發生錯誤: {e}")
        return None

if __name__ == "__main__":
    # 測試下單範例：限價買入 btcusdt
    print("正在提交測試訂單...")
    create_order(
        wallet_type='spot',
        market='btcusdt',
        side='buy',
        volume='0.001',
        price='20000',
        ord_type='limit'
    )
