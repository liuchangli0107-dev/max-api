import base64
import hmac
import hashlib
import json
import os
from pathlib import Path
import time
import requests
from urllib.parse import urlencode

# 讀取 .env 檔案
env_file = Path(__file__).parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                os.environ[key] = value

access_key = os.getenv("MAX_ACCESS_KEY")
secret_key = os.getenv("MAX_SECRET_KEY")

def get_wallet_balance(wallet_type='spot', currency=None):
    """
    取得錢包餘額
    :param wallet_type: 錢包類型 ('spot' 或 'm')
    :param currency: 幣種篩選，例如 'twd', 'btc' (選填，若不傳則取得所有幣種)
    :return: 響應數據
    """
    if not access_key or not secret_key:
        raise ValueError("請先在 .env 檔案中設置 MAX_ACCESS_KEY 與 MAX_SECRET_KEY")

    # API 請求路徑
    path = f"/api/v3/wallet/{wallet_type}/accounts"

    # 1. 準備必要的參數
    params = {
        'nonce': int(time.time() * 1000)  # 使用當前時間戳作為 nonce
    }

    # 如果有指定幣種，則加入 query 參數
    if currency:
        params['currency'] = currency.lower()

    # 2. 構建簽名內容
    # 將包含 nonce (及其他參數) 的 object 與請求路徑合併（添加 path 字段）
    params_to_sign = {**params, 'path': path}
    
    # 將合併後的字典轉換為 JSON 字符串 (去除空格確保格式一致)
    json_str = json.dumps(params_to_sign, separators=(',', ':'))

    # 3. 生成 payload
    # 對 JSON 字符串進行 Base64 編碼
    payload = base64.b64encode(json_str.encode()).decode()

    # 4. 計算簽名
    # 使用 HMAC-SHA256 演算法，以 Secret Key 作為密鑰，對 payload 進行加密
    signature = hmac.new(
        secret_key.encode(),
        payload.encode(),
        hashlib.sha256
    ).hexdigest()

    # 5. 設置 request header
    headers = {
        'X-MAX-ACCESSKEY': access_key,
        'X-MAX-PAYLOAD': payload,
        'X-MAX-SIGNATURE': signature,
        'X-Sub-Account': 'main',           # 指定要操作的子帳號，預設為主帳號 "main"
        'Content-Type': 'application/json'
    }

    # 6. 發送 GET 請求 (參數附加在 URL 上)
    url = f"https://max-api.maicoin.com{path}"
    url_with_params = f"{url}?{urlencode(params)}"

    response = requests.get(url_with_params, headers=headers)
    
    if response.status_code == 200:
        return response.json()
    else:
        # 若發生錯誤，回傳錯誤訊息
        try:
            return response.json()
        except Exception:
            return {
                "error": f"HTTP status code: {response.status_code}",
                "message": response.text
            }

def main():
    try:
        print("正在取得現貨錢包所有餘額...")
        balances = get_wallet_balance(wallet_type='spot')
        
        # 美化輸出 JSON
        print("\n現貨錢包餘額結果 (全幣種):")
        print(json.dumps(balances, indent=2, ensure_ascii=False))

        # 範例二：只查詢單一幣種（以 TWD 為例）
        print("\n正在取得現貨錢包 TWD 餘額...")
        twd_balance = get_wallet_balance(wallet_type='spot', currency='twd')
        print("TWD 餘額結果:")
        print(json.dumps(twd_balance, indent=2, ensure_ascii=False))

    except Exception as error:
        print("發生錯誤:", error)

if __name__ == "__main__":
    main()
