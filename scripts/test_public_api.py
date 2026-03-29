"""
Local test script for public API connectivity.
Tests each data.go.kr API endpoint independently.

Usage:
    python3 scripts/test_public_api.py
"""
import asyncio
import os
import sys
from urllib.parse import urlencode, quote_plus

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import httpx
except ImportError:
    print("ERROR: httpx not installed. Run: pip install httpx")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# Load from .env file manually
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    env = {}
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    env[key.strip()] = val.strip()
    return env


ENV = load_env()
DECODING_KEY = ENV.get("DATA_GO_KR_API_KEY", "")
REB_KEY = ENV.get("REB_API_KEY", "")
SEOUL_KEY = ENV.get("SEOUL_API_KEY", "")
GYEONGGI_KEY = ENV.get("GYEONGGI_API_KEY", "")

# Test region: 강남구
TEST_REGION = "11680"
TEST_DEAL_YM = "202602"  # Use a recent past month
TIMEOUT = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def print_header(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def print_result(success: bool, detail: str):
    icon = "PASS" if success else "FAIL"
    print(f"  [{icon}] {detail}")


async def test_url_manual(client: httpx.AsyncClient, label: str, url: str, params: dict, key: str):
    """Test API by manually building URL (avoiding double-encoding of serviceKey)."""
    params_copy = dict(params)
    if key:
        # Build URL manually — serviceKey must NOT be percent-encoded again
        query = f"serviceKey={key}&{urlencode(params_copy)}"
    else:
        query = urlencode(params_copy)
    full_url = f"{url}?{query}"

    try:
        resp = await client.get(full_url, timeout=TIMEOUT)
        status = resp.status_code
        if status == 200:
            data = resp.json()
            # Check for data.go.kr error codes in response body
            header = data.get("response", {}).get("header", {})
            result_code = header.get("resultCode", "")
            result_msg = header.get("resultMsg", "")
            if result_code == "00":
                body = data.get("response", {}).get("body", {})
                total = body.get("totalCount", 0)
                print_result(True, f"{label}: HTTP 200, resultCode=00, totalCount={total}")
                return True, int(total)
            else:
                print_result(False, f"{label}: HTTP 200 but resultCode={result_code} ({result_msg})")
                return False, 0
        else:
            text = resp.text[:200]
            print_result(False, f"{label}: HTTP {status} — {text}")
            return False, 0
    except Exception as e:
        print_result(False, f"{label}: Exception — {e}")
        return False, 0


async def test_url_httpx_params(client: httpx.AsyncClient, label: str, url: str, params: dict):
    """Test API by passing params to httpx (lets httpx encode)."""
    try:
        resp = await client.get(url, params=params, timeout=TIMEOUT)
        status = resp.status_code
        if status == 200:
            data = resp.json()
            header = data.get("response", {}).get("header", {})
            result_code = header.get("resultCode", "")
            result_msg = header.get("resultMsg", "")
            if result_code == "00":
                body = data.get("response", {}).get("body", {})
                total = body.get("totalCount", 0)
                print_result(True, f"{label}: HTTP 200, resultCode=00, totalCount={total}")
                return True, int(total)
            else:
                print_result(False, f"{label}: HTTP 200 but resultCode={result_code} ({result_msg})")
                return False, 0
        else:
            print_result(False, f"{label}: HTTP {status}")
            return False, 0
    except Exception as e:
        print_result(False, f"{label}: Exception — {e}")
        return False, 0


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

async def test_molit_sale():
    print_header("1. MOLIT Apartment Sale (국토부 아파트매매 실거래)")
    print(f"  API ID: 15126469")
    print(f"  Region: {TEST_REGION}, Deal YM: {TEST_DEAL_YM}")

    url = "http://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
    params = {"LAWD_CD": TEST_REGION, "DEAL_YMD": TEST_DEAL_YM, "type": "json", "pageNo": 1, "numOfRows": 10}

    async with httpx.AsyncClient() as client:
        # Method A: Manual URL (what molit.py does)
        ok_a, count_a = await test_url_manual(client, "Manual URL (Decoding key)", url, params, DECODING_KEY)

        # Method B: Encoding key via manual URL
        encoding_key = quote_plus(DECODING_KEY)
        ok_b, count_b = await test_url_manual(client, "Manual URL (Encoding key)", url, params, encoding_key)

        # Method C: httpx params (Decoding key)
        params_c = {**params, "serviceKey": DECODING_KEY}
        ok_c, count_c = await test_url_httpx_params(client, "httpx params (Decoding key)", url, params_c)

        # Method D: httpx params (Encoding key)
        params_d = {**params, "serviceKey": encoding_key}
        ok_d, count_d = await test_url_httpx_params(client, "httpx params (Encoding key)", url, params_d)

    return ok_a or ok_b or ok_c or ok_d


async def test_molit_jeonse():
    print_header("2. MOLIT Apartment Jeonse (국토부 아파트전월세 실거래)")
    print(f"  API ID: 15126474")

    url = "http://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent"
    params = {"LAWD_CD": TEST_REGION, "DEAL_YMD": TEST_DEAL_YM, "type": "json", "pageNo": 1, "numOfRows": 10}

    async with httpx.AsyncClient() as client:
        ok, count = await test_url_manual(client, "Manual URL (Decoding key)", url, params, DECODING_KEY)
        if not ok:
            encoding_key = quote_plus(DECODING_KEY)
            ok, count = await test_url_manual(client, "Manual URL (Encoding key)", url, params, encoding_key)
    return ok


async def test_building():
    print_header("3. Building Ledger (건축물대장)")
    print(f"  API ID: 15134735")

    url = "http://apis.data.go.kr/1613000/BldRgstHubService/getBrTitleInfo"
    params = {
        "sigunguCd": TEST_REGION, "bjdongCd": "00000", "platGbCd": "0",
        "buldKindCd": "2", "pageNo": 1, "numOfRows": 10, "type": "json",
    }

    async with httpx.AsyncClient() as client:
        # Current code uses httpx params= which double-encodes the key
        ok_a, _ = await test_url_manual(client, "Manual URL (Decoding key)", url, params, DECODING_KEY)
        if not ok_a:
            encoding_key = quote_plus(DECODING_KEY)
            ok_a, _ = await test_url_manual(client, "Manual URL (Encoding key)", url, params, encoding_key)

        # Also test httpx params way (what building.py currently does)
        params_httpx = {**params, "serviceKey": DECODING_KEY}
        ok_b, _ = await test_url_httpx_params(client, "httpx params (current code)", url, params_httpx)

    return ok_a or ok_b


async def test_official_price():
    print_header("4. Official Price (공시가격)")
    print(f"  API ID: 15124003")

    url = "http://apis.data.go.kr/1613000/AptBasisInfoService1/getAppraisedPriceAttr"
    params = {
        "sigunguCd": TEST_REGION, "bjdongCd": "00000", "stddYear": "2025",
        "pageNo": 1, "numOfRows": 10, "type": "json",
    }

    async with httpx.AsyncClient() as client:
        ok, _ = await test_url_manual(client, "Manual URL (Decoding key)", url, params, DECODING_KEY)
        if not ok:
            encoding_key = quote_plus(DECODING_KEY)
            ok, _ = await test_url_manual(client, "Manual URL (Encoding key)", url, params, encoding_key)
    return ok


async def test_reb():
    print_header("5. Korea Real Estate Board (한국부동산원 통계)")
    print(f"  API: real-estate-info/getRealEstatePriceIndex")

    url = "http://apis.data.go.kr/B553547/real-estate-info/getRealEstatePriceIndex"
    params = {
        "tradeType": "01", "regionNm": "서울", "startDt": "202501", "endDt": "202602",
        "pageNo": 1, "numOfRows": 10, "type": "json",
    }

    async with httpx.AsyncClient() as client:
        ok, _ = await test_url_manual(client, "Manual URL (REB key)", url, params, REB_KEY)
        if not ok:
            # Try with data.go.kr key instead
            ok, _ = await test_url_manual(client, "Manual URL (data.go.kr key)", url, params, DECODING_KEY)
        if not ok:
            encoding_key = quote_plus(DECODING_KEY)
            ok, _ = await test_url_manual(client, "Manual URL (Encoding key)", url, params, encoding_key)
    return ok


async def test_seoul():
    print_header("6. Seoul Open Data (서울 열린데이터광장)")
    print(f"  Region filter: 11xxx only")

    # Seoul API format: openapi.seoul.go.kr:8088/{KEY}/json/{SERVICE}/{startIdx}/{endIdx}/{params...}
    key = SEOUL_KEY
    cgg_cd = TEST_REGION[2:]  # "680" from "11680"
    url = f"http://openapi.seoul.go.kr:8088/{key}/json/tbLnOpendataRtmsarss/1/10/2025/{cgg_cd}"

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, timeout=TIMEOUT)
            status = resp.status_code
            if status == 200:
                data = resp.json()
                # Seoul wraps under service name key or "RESULT"
                if "tbLnOpendataRtmsarss" in data:
                    rows = data["tbLnOpendataRtmsarss"].get("row", [])
                    total = data["tbLnOpendataRtmsarss"].get("list_total_count", len(rows))
                    print_result(True, f"Seoul API: HTTP 200, total={total}, rows={len(rows)}")
                    return True
                elif "RESULT" in data:
                    code = data["RESULT"].get("CODE", "")
                    msg = data["RESULT"].get("MESSAGE", "")
                    print_result(False, f"Seoul API: {code} — {msg}")
                    return False
                else:
                    print_result(False, f"Seoul API: Unexpected response shape: {list(data.keys())}")
                    return False
            else:
                print_result(False, f"Seoul API: HTTP {status}")
                return False
        except Exception as e:
            print_result(False, f"Seoul API: Exception — {e}")
            return False


async def test_gyeonggi():
    print_header("7. Gyeonggi Data Dream (경기데이터드림)")
    print(f"  Region: 41135 (성남시분당구)")

    url = "https://openapi.gg.go.kr/AptTradeSvc"
    params = {
        "KEY": GYEONGGI_KEY,
        "SIGUN_CD": "41135",
        "pIndex": 1,
        "pSize": 10,
        "Type": "json",
    }

    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, params=params, timeout=TIMEOUT)
            status = resp.status_code
            if status == 200:
                data = resp.json()
                if "AptTradeSvc" in data:
                    svc = data["AptTradeSvc"]
                    if isinstance(svc, list) and len(svc) >= 1:
                        head = svc[0].get("head", [{}])
                        total = head[0].get("list_total_count", 0) if head else 0
                        code = head[1].get("RESULT", {}).get("CODE", "") if len(head) > 1 else ""
                        if code == "INFO-000":
                            rows = svc[1].get("row", []) if len(svc) > 1 else []
                            print_result(True, f"Gyeonggi API: total={total}, rows={len(rows)}")
                            return True
                        else:
                            msg = head[1].get("RESULT", {}).get("MESSAGE", "") if len(head) > 1 else ""
                            print_result(False, f"Gyeonggi API: {code} — {msg}")
                            return False
                    else:
                        print_result(False, f"Gyeonggi API: Unexpected AptTradeSvc shape")
                        return False
                else:
                    print_result(False, f"Gyeonggi API: No 'AptTradeSvc' key — {list(data.keys())}")
                    return False
            else:
                print_result(False, f"Gyeonggi API: HTTP {status}")
                return False
        except Exception as e:
            print_result(False, f"Gyeonggi API: Exception — {e}")
            return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main():
    print("Public API Connectivity Test")
    print(f"  data.go.kr key: {'SET' if DECODING_KEY else 'MISSING'} ({len(DECODING_KEY)} chars)")
    print(f"  REB key: {'SET' if REB_KEY else 'MISSING'}")
    print(f"  Seoul key: {'SET' if SEOUL_KEY else 'MISSING'}")
    print(f"  Gyeonggi key: {'SET' if GYEONGGI_KEY else 'MISSING'}")

    results = {}
    results["molit_sale"] = await test_molit_sale()
    results["molit_jeonse"] = await test_molit_jeonse()
    results["building"] = await test_building()
    results["official_price"] = await test_official_price()
    results["reb"] = await test_reb()
    results["seoul"] = await test_seoul()
    results["gyeonggi"] = await test_gyeonggi()

    print_header("SUMMARY")
    for name, ok in results.items():
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {name}")

    passed = sum(1 for v in results.values() if v)
    total = len(results)
    print(f"\n  Result: {passed}/{total} passed")


if __name__ == "__main__":
    asyncio.run(main())
