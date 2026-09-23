# paypayu.py
import aiohttp
import datetime
import os
import json
import asyncio
from useragent_changer import UserAgent

ua = UserAgent('iphone')

PROXY_FILE = "proxy_data.json"

def load_proxy():
    if os.path.exists(PROXY_FILE):
        try:
            with open(PROXY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("proxy_url")
        except:
            pass
    return ""

def save_proxy(proxy_url: str):
    with open(PROXY_FILE, "w", encoding="utf-8") as f:
        json.dump({"proxy_url": proxy_url}, f, indent=4, ensure_ascii=False)

# --- send login request ---
async def login(phoneNumber: str, password: str, uuid: str):
    proxy = load_proxy()
    headers = {
        'User-Agent': ua.set(),
        'Accept' : 'application/json, text/plain, */*',
        'Content-Type' : 'application/json',
        'Origin': 'https://www.paypay.ne.jp',
        'Referer':'https://www.paypay.ne.jp/app/account/sign-in',
    }
    payload = {
        "scope":"SIGN_IN",
        "client_uuid":f"{uuid}",
        "grant_type":"password",
        "username":phoneNumber,
        "password":password,
        "add_otp_prefix": True,
        "language":"ja"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://www.paypay.ne.jp/app/v1/oauth/token", headers=headers, json=payload, proxy=proxy) as login_request_response:
            return await login_request_response.json()

# --- one-time-password authentication ---
async def login_otp(set_uuid,otp,otpid,otp_pre):
    proxy = load_proxy()
    otp_number=otp
    headers = {
        'User-Agent': ua.set(),
        'Accept' : 'application/json, text/plain, */*',
        'Content-Type' : 'application/json',
        'Origin': 'https://www.paypay.ne.jp',
        'Referer':'https://www.paypay.ne.jp/app/account/sign-in',
    }
    payload = {
            "scope":"SIGN_IN",
            "client_uuid":f"{set_uuid}",
            "grant_type":"otp",
            "otp_prefix": str(otp_pre),
            "otp":otp_number,
            "otp_reference_id":otpid,
            "username_type":"MOBILE",
            "language":"ja"
    }
    async with aiohttp.ClientSession() as session:
        async with session.post("https://www.paypay.ne.jp/app/v1/oauth/token", headers=headers, json=payload, proxy=proxy) as response:
            login_response = await response.json()
            try:
                if login_response["response_type"]=="ErrorResponse":
                    return "ERR"
            except:
                return "OK"

async def check_link(cd):
    proxy = load_proxy()
    if "https://" in cd:
        cd=cd.replace("https://pay.paypay.ne.jp/","")

    headers={
        "Accept":"application/json, text/plain, */*",
        'User-Agent': ua.set(),
        "Content-Type":"application/json"
    }
    
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://www.paypay.ne.jp/app/v2/p2p-api/getP2PLinkInfo?verificationCode={cd}", headers=headers, proxy=proxy) as response:
                response.raise_for_status()
                link_info = await response.json()
            
        except aiohttp.ClientError as e:
            print(f"API_REQ_EXC: {e}") 
            return False
    
    result_code = link_info.get("header", {}).get("resultCode")

    if result_code != "S0000":
     return False

    return link_info
    
async def link_rev(cd: str, phoneNumber: str, password: str, uuid: str,link_password: str = None):
    proxy = load_proxy()
    if "https://" in cd:
        cd=cd.replace("https://pay.paypay.ne.jp/","")
        
    async with aiohttp.ClientSession() as session:
        base_headers = {
            "Accept": "application/json, text/plain, */*",
            'User-Agent': ua.set(),
            "Content-Type": "application/json"
        }
        
        try:
            async with session.get(f"https://www.paypay.ne.jp/app/v2/p2p-api/getP2PLinkInfo?verificationCode={cd}", headers=base_headers, proxy=proxy) as response:
                response.raise_for_status()
                link_info = await response.json()

            if link_info.get("payload", {}).get("orderStatus") != "PENDING":
                return False
            
            if link_info.get("payload", {}).get("pendingP2PInfo", {}).get("isSetPasscode") and link_password is None:
                return False

        except aiohttp.ClientError as e:
            print(f"LINK_REQ_EXC: {e}") 
            return False
        
        login_payload = {
            "scope":"SIGN_IN",
            "client_uuid":f"{uuid}",
            "grant_type":"password",
            "username":phoneNumber,
            "password":password,
            "add_otp_prefix": True,
            "language":"ja"
            }

        login_headers = {
            'User-Agent': ua.set(),
            'Accept' : 'application/json, text/plain, */*',
            'Content-Type' : 'application/json',
            'Origin': 'https://www.paypay.ne.jp',
            'Referer':'https://pay.paypay.ne.jp/'+cd,
        }

        async with session.post("https://www.paypay.ne.jp/app/v1/oauth/token", headers=login_headers, json=login_payload, proxy=proxy) as response:
            login_response = await response.json()
            try:
                login_response = (login_response["access_token"])
            except:
                try:
                    login_response["otp_reference_id"]
                    return "LOGINERR"
                except:
                    return "LOGINERR"
        
        receive_payload = {
            "verificationCode":cd,
            "client_uuid":uuid,
            "requestAt":str(datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))).strftime('%Y-%m-%dT%H:%M:%S+0900')),
            "requestId":link_info["payload"]["message"]["data"]["requestId"],
            "orderId":link_info["payload"]["message"]["data"]["orderId"],
            "senderMessageId":link_info["payload"]["message"]["messageId"],
            "senderChannelUrl":link_info["payload"]["message"]["chatRoomId"],
            "iosMinimumVersion":"3.45.0",
            "androidMinimumVersion":"3.45.0"
            }
        
        if link_password:
            receive_payload["passcode"]=link_password

        try:
            async with session.post("https://www.paypay.ne.jp/app/v2/p2p-api/acceptP2PSendMoneyLink", json=receive_payload, headers=base_headers, proxy=proxy) as response:
                response.raise_for_status()
                receive_data = await response.json()

                if receive_data.get("header", {}).get("resultCode") == "S0000":
                    return True
                else:
                    return False

        except aiohttp.ClientError as e:
            print(f"REVERR: {e}") 
            return False

# --- 追加：セッション保持用クラス ---
class PayPayAsync:
    def __init__(self, phone: str = None, password: str = None, uuid: str = None):
        self.phone = phone
        self.password = password
        self.uuid = uuid
        self.session = None
        self.access_token = None
        self.proxy = load_proxy()

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()

    async def login(self):
        headers = {'User-Agent': ua.set(), 'Content-Type': 'application/json'}
        payload = {
            "scope": "SIGN_IN", "client_uuid": self.uuid, "grant_type": "password",
            "username": self.phone, "password": self.password, "add_otp_prefix": True, "language": "ja"
        }
        async with self.session.post("https://www.paypay.ne.jp/app/v1/oauth/token", 
                                     headers=headers, json=payload, proxy=self.proxy) as resp:
            data = await resp.json()
            self.access_token = data.get("access_token")
            return data

    async def check_link(self, cd):
        """同一セッション内でリンク情報を確認（ログイン済みセッション使用）"""
        if "https://" in cd:
            cd = cd.replace("https://pay.paypay.ne.jp/", "")

        headers = {
            "Accept": "application/json, text/plain, */*",
            'User-Agent': ua.set(),
            "Content-Type": "application/json"
        }
        
        try:
            # ログイン済みセッションでリンク情報を取得
            async with self.session.get(
                f"https://www.paypay.ne.jp/app/v2/p2p-api/getP2PLinkInfo?verificationCode={cd}", 
                headers=headers, 
                proxy=self.proxy
            ) as response:
                response.raise_for_status()
                link_info = await response.json()
                
                # エラーレスポンスをチェック
                if response.status == 401:
                    print("401 Unauthorized - セッションが無効です。再ログインしてください。")
                    return False
                    
        except aiohttp.ClientError as e:
            print(f"API_REQ_EXC: {e}") 
            return False
        except Exception as e:
            print(f"API error: {e}")
            return False
        
        result_code = link_info.get("header", {}).get("resultCode")
        
        if result_code != "S0000":
            print(f"Link check failed with code: {result_code}")
            return False

        return link_info

    async def get_history(self):
        if not self.access_token:
            raise Exception("先にログインしてください")
        
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": ua.set(),
            "Content-Type": "application/json"
        }
        async with self.session.get("https://www.paypay.ne.jp/app/v2/bff/getPay2BalanceHistory", 
                                    headers=headers, proxy=self.proxy) as resp:
            return await resp.json()