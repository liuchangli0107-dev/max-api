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

def make_request(path, method='GET', extra_params=None):
    """
    發送 MAX API 請求的通用函數
    :param path: API 路徑
    :param method: HTTP 方法 (GET, POST, DELETE, PUT)
    :param extra_params: 額外的請求參數
    :return: 響應數據
    """
    # 1. 準備必要的參數
    params = {
        'nonce': int(time.time() * 1000)  # 使用當前時間戳作為 nonce
    }

    if extra_params:
        params.update(extra_params)  # 添加其他必要參數

    # 2. 構建簽名內容
    params_to_sign = {**params, 'path': path}  # 將參數與路徑合併
    json_str = json.dumps(params_to_sign)  # 轉換為 JSON 字符串

    # 3. 生成 payload
    payload = base64.b64encode(json_str.encode()).decode()  # Base64 編碼

    # 4. 計算簽名
    signature = hmac.new(
        secret_key.encode(),           # 使用 Secret Key 作為密鑰
        payload.encode(),              # 對 payload 進行簽名
        hashlib.sha256                 # 使用 HMAC-SHA256 演算法
    ).hexdigest()                      # 轉換為十六進制字符串

    # 5. 設置 request header
    headers = {
        'X-MAX-ACCESSKEY': access_key,     # 添加 Access Key
        'X-MAX-PAYLOAD': payload,          # 添加 payload
        'X-MAX-SIGNATURE': signature,      # 添加簽名
        'Content-Type': 'application/json'
    }

    # 6. 發送請求
    url = f"https://max-api.maicoin.com{path}"

    if method == 'GET':
        # GET：參數附加在 URL 上
        url += f"?{urlencode(params)}"
        response = requests.get(url, headers=headers)
    else:
        # DELETE/POST/PUT：參數放在 request body 中
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            data=json.dumps(params)
        )

    return response.json()

def main():
    try:
        # 請求 info (GET sample)
        info = make_request('/api/v3/info')
        print("Info response:", info)

        # 請求取消訂單 (DELETE sample)
        # orders = make_request('/api/v3/order', 'DELETE', {'id': xxxxxxx})
        # print("Cancel orders response:", orders)

        # POST sample（已註釋）
        create_order = make_request(
            '/api/v3/wallet/spot/order',
            'POST',
            {
                'market': 'btcusdt',
                'side': 'buy',
                'volume': '0.001',
                'price': '20000'
            }
        )
        print("Create order response:", create_order)

    except Exception as error:
        print("Error:", error)

if __name__ == "__main__":
    main()