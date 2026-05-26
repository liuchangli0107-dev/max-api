import requests
import sys

def get_ticker_v2(market="btcusdt"):
    url = f"https://max-api.maicoin.com/api/v2/tickers/{market}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json()
        last_price = data['last']
        print(f"[{market.upper()}] 當前市場價格: {last_price}")
        return last_price
    except Exception as e:
        print(f"查詢失敗: {e}")
        return None

def get_ticker(market="btcusdt"):
    url = "https://max-api.maicoin.com/api/v3/ticker"
    params = {"market": market.lower()}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        last_price = data.get('last')
        print(f"[V3] [{market.upper()}] 當前市場價格: {last_price}")
        return data
    except Exception as e:
        print(f"[V3] 查詢失敗: {e}")
        return None


def get_tickers(markets=None):
    if markets is None:
        markets = ["btcusdt", "btctwd", "usdttwd"]
    elif isinstance(markets, str):
        markets = [markets]

    url = "https://max-api.maicoin.com/api/v3/tickers"
    params = {"markets[]": [m.lower() for m in markets]}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        print(f"[V3 Tickers] 成功取得 {len(data)} 個市場的資訊")
        for ticker in data:
            print(f" - [{ticker['market'].upper()}] 最新價格: {ticker.get('last')}")
        return data
    except Exception as e:
        print(f"[V3 Tickers] 查詢失敗: {e}")
        return None

def get_klines(market="btcusdt", period="1440", limit=100):
    url = f"https://max-api.maicoin.com/api/v3/k"
    params = {"market": market.lower(), "period": period, "limit": limit}
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"[Klines] 查詢失敗: {e}")
        return None

def calculate_ma(klines, period):
    if not klines or len(klines) < period:
        return None
    # kline: [timestamp, open, high, low, close, volume]
    closes = [float(k[4]) for k in klines[-period:]]
    return sum(closes) / period

if __name__ == "__main__":
    market = sys.argv[1] if len(sys.argv) > 1 else "btcusdt"
    print("--- 呼叫 V2 Ticker ---")
    get_ticker_v2(market)
    print("\n--- 呼叫 V3 Ticker ---")
    get_ticker(market)
    
    print("\n--- 計算 MA (20, 50, 100) ---")
    klines = get_klines(market, period="1440", limit=100)
    if klines:
        for p in [20, 50, 100]:
            ma = calculate_ma(klines, p)
            if ma:
                print(f"MA{p}: {ma:.2f}")
