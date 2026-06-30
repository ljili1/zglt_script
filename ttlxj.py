import asyncio, json, os, random, string, time
from datetime import datetime
from functools import wraps
from urllib.parse import parse_qs, urlparse

import httpx

# ====================  Decorators  ====================
def async_task(task_name=None):
    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            try: return await func(self, *args, **kwargs)
            except Exception as e: self.logger.log(f"{task_name or func.__name__.replace('_', ' ').strip()}异常: {e}")
        return wrapper
    return decorator

def async_task_silent(func):
    """静默异步任务装饰器：只捕获异常不记录"""
    @wraps(func)
    async def wrapper(self, *args, **kwargs):
        try:
            return await func(self, *args, **kwargs)
        except Exception:
            pass
    return wrapper

# ====================  Constants  ====================
APP_VERSION = "iphone_c@11.0503"
USER_AGENT = lambda v: f"Mozilla/5.0 (iPhone; CPU iPhone OS 16_1_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 unicom{{version:{v}}}"
APP_ID = "86b8be06f56ba55e9fa7dff134c6b16c62ca7f319da4a958dd0afa0bf9f36f1daa9922869a8d2313b6f2f9f3b57f2901f0021c4575e4b6949ae18b7f6761d465c12321788dcd980aa1a641789d1188bb"
CLIENT_ID = "73b138fd-250c-4126-94e2-48cbcc8b9cbe"

# ====================  Utils  ====================
_print_lock = asyncio.Lock()
_notify_messages = []

class Logger:
    def __init__(self, prefix=""): self.prefix = prefix
    def log(self, message, notify=False):
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] [{self.prefix}] {message}" if self.prefix else f"[{ts}] {message}", flush=True)
        if notify: _notify_messages.append(f"[{self.prefix}] {message}" if self.prefix else message)
    async def log_async(self, message, notify=False):
        async with _print_lock: self.log(message, notify)

class HttpClient:
    def __init__(self, logger_instance):
        self.logger, self.headers, self.cookies, self.timeout, self.retries = logger_instance, {"User-Agent": USER_AGENT(APP_VERSION), "Connection": "keep-alive"}, httpx.Cookies(), 50.0, 3
        self.client = None
        self.client_limits = httpx.Limits(max_keepalive_connections=20, max_connections=20)

    async def _get_client(self):
        if not self.client or self.client.is_closed:
            self.client = httpx.AsyncClient(cookies=self.cookies, http2=False, follow_redirects=False, timeout=self.timeout, verify=False, limits=self.client_limits)
        return self.client

    async def close(self):
        if self.client and not self.client.is_closed:
            await self.client.aclose()
        self.client = None

    async def request(self, method, url, **kwargs):
        headers = {**self.headers, **kwargs.pop("headers", {})}
        cookies = kwargs.pop("cookies", self.cookies)
        timeout = kwargs.pop("timeout", self.timeout)
        retries = kwargs.pop("retries", self.retries)
        use_temp_client = cookies is not self.cookies
        for attempt in range(retries):
            try:
                if use_temp_client:
                    async with httpx.AsyncClient(cookies=cookies, http2=False, follow_redirects=False, timeout=self.timeout, verify=False, limits=self.client_limits) as client:
                        response = await client.request(method, url, headers=headers, timeout=timeout, **kwargs)
                else:
                    client = await self._get_client()
                    response = await client.request(method, url, headers=headers, timeout=timeout, **kwargs)
                    client.cookies.update(response.cookies)
                self.cookies.update(response.cookies)
                text = response.text
                if text.strip().startswith(("{", "[")):
                    try: result = response.json()
                    except Exception: result = text
                else: result = text
                return {"statusCode": response.status_code, "headers": response.headers, "result": result}
            except Exception:
                if attempt + 1 < retries: await asyncio.sleep(1 + attempt * 2)
        return {"statusCode": -1, "headers": {}, "result": None}
    get = lambda self, url, **kw: self.request("GET", url, **kw)
    post = lambda self, url, **kw: self.request("POST", url, **kw)


class CustomUserService:
    def __init__(self, cookie, index=1):
        self.cookie, self.index = cookie, index
        self.logger = Logger(prefix=f"账号{index}")
        self.http = HttpClient(self.logger)
        self.valid, self.mobile, self.province = False, "", ""
        self.app_version, self.token_online, self.app_id = APP_VERSION, cookie.strip(), APP_ID
        self.unicom_token_id = "".join(random.choices(string.ascii_letters + string.digits, k=32))
        self.token_id_cookie = "chinaunicom-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=32))
        self.sdkuuid = self.unicom_token_id
        self.random_string = lambda n, c=string.ascii_letters + string.digits: "".join(random.choices(c, k=n))
        for name, val in [("TOKENID_COOKIE", self.token_id_cookie), ("UNICOM_TOKENID", self.unicom_token_id), ("sdkuuid", self.sdkuuid)]:
            self.http.cookies.set(name, val, domain=".10010.com")
        self.rpt_id = self.ecs_token = ""
        self.session_id = self.token_id = ""

    get_bizchannelinfo = lambda self: json.dumps({"bizChannelCode": "225", "disriBiz": "party", "unionSessionId": "", "stType": "", "stDesmobile": "", "source": "", "rptId": self.rpt_id, "ticket": "", "tongdunTokenId": self.token_id_cookie, "xindunTokenId": self.sdkuuid})
    get_epay_authinfo = lambda self: json.dumps({"mobile": "", "sessionId": getattr(self, "session_id", ""), "tokenId": getattr(self, "token_id", ""), "userId": ""})

    # ====================  登录  ====================

    @async_task("登录")
    async def online(self):
        data = {"token_online": self.token_online, "reqtime": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "appId": self.app_id, "version": self.app_version, "step": "bindlist", "isFirstInstall": 0, "deviceModel": "iPhone14,6", "deviceOS": "16.6", "deviceBrand": "iPhone", "uniqueIdentifier": "ios" + self.random_string(32, "0123456789abcdef"), "simOperator": "--,--,65535,65535,--@--,--,65535,65535,--", "voipToken": "citc-default-token-do-not-push"}
        res = await self.http.post("https://m.client.10010.com/mobileService/onLine.htm", data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
        if (result := res["result"]) and str(result.get("code")) == "0":
            self.valid, self.mobile, self.ecs_token, self.province = True, result.get("desmobile", ""), result.get("ecs_token", ""), (result.get("list") or [{}])[0].get("proName", "")
            masked = f"{self.mobile[:3]}****{self.mobile[-4:]}" if len(self.mobile) >= 11 else self.mobile
            self.logger.log(f"登录成功: {masked} (归属地: {self.province})")
            return True
        self.logger.log(f"登录失败: {result}")
        return False

    @async_task("获取ticket")
    async def open_plat_line_new(self, url, headers=None):
        res = await self.http.get("https://m.client.10010.com/mobileService/openPlatform/openPlatLineNew.htm", params={"to_url": url}, headers=headers or {})
        if location := (res["headers"].get("location") or res["headers"].get("Location")):
            qs = parse_qs(urlparse(location).query)
            return {"ticket": qs.get("ticket", [""])[0], "type": qs.get("type", ["02"])[0], "loc": location}
        self.logger.log("获取ticket失败: 无location")
        return {"ticket": "", "type": "", "loc": ""}

    # ====================  天天领现金  ====================

    async def ttlxj_task(self):
        self.rpt_id = ""
        if (ticket_info := await self.open_plat_line_new("https://epay.10010.com/ci-mps-st-web/?webViewNavIsHidden=webViewNavIsHidden"))["ticket"]:
            await self.ttlxj_authorize(ticket_info["ticket"], ticket_info["type"], ticket_info["loc"])

    @async_task("天天领现金授权")
    async def ttlxj_authorize(self, ticket, st_type, referer):
        data = {"response_type": "rptid", "client_id": CLIENT_ID, "redirect_uri": "https://epay.10010.com/ci-mps-st-web/", "login_hint": {"credential_type": "st_ticket", "credential": ticket, "st_type": st_type, "force_logout": True, "source": "app_sjyyt"}, "device_info": {"token_id": f"chinaunicom-pro-{int(time.time() * 1000)}-{self.random_string(13)}", "trace_id": self.random_string(32)}}
        res = await self.http.post("https://epay.10010.com/woauth2/v2/authorize", headers={"Origin": "https://epay.10010.com", "Referer": referer}, json=data)
        if res["statusCode"] == 200: await self.ttlxj_auth_check()
        else: self.logger.log(f"天天领现金授权失败: {res['result']}")

    @async_task("天天领现金认证")
    async def ttlxj_auth_check(self):
        res = await self.http.post("https://epay.10010.com/ps-pafs-auth-front/v1/auth/check", headers={"bizchannelinfo": self.get_bizchannelinfo()})
        result = res["result"]
        if str(result.get("code")) == "0000":
            auth = result.get("data", {}).get("authInfo", {})
            self.session_id, self.token_id = auth.get("sessionId"), auth.get("tokenId")
            await self.ttlxj_user_draw_info()
            await self.ttlxj_query_available()
        elif str(result.get("code")) == "2101000100": await self.ttlxj_login(result.get("data", {}).get("woauth_login_url"))
        else: self.logger.log(f"天天领现金认证失败: {result}")

    @async_task("天天领现金登录")
    async def ttlxj_login(self, login_url):
        res = await self.http.get(f"{login_url}https://epay.10010.com/ci-mcss-party-web/clockIn/?bizFrom=225&bizChannelCode=225&channelType=WDQB")
        if location := (res["headers"].get("location") or res["headers"].get("Location")):
            rpt_id = parse_qs(urlparse(location).query).get("rptid", [""])[0]
            if rpt_id: self.rpt_id = rpt_id; await self.ttlxj_auth_check()
            else: self.logger.log("天天领现金获取rptid失败")
        else: self.logger.log("天天领现金获取rptid失败: 无location")

    @async_task("天天领现金查询")
    async def ttlxj_user_draw_info(self):
        res = await self.http.post("https://epay.10010.com/ci-mcss-party-front/v1/ttlxj/userDrawInfo", headers={"bizchannelinfo": self.get_bizchannelinfo(), "authinfo": self.get_epay_authinfo()})
        if (result := res["result"]) and str(result.get("code")) == "0000":
            data = result.get("data", {})
            day_key = f"day{data.get('dayOfWeek')}"
            not_clocked = data.get(day_key) == "1"
            self.logger.log(f"天天领现金今天{'未' if not_clocked else '已'}打卡", notify=True)
            if not_clocked:
                draw_type = "C" if (datetime.now().weekday() + 1) % 7 == 0 else "B"
                await self.ttlxj_unify_draw_new(draw_type)
        else: self.logger.log(f"天天领现金查询失败: {result}")

    @async_task("天天领现金打卡")
    async def ttlxj_unify_draw_new(self, draw_type):
        res = await self.http.post("https://epay.10010.com/ci-mcss-party-front/v1/ttlxj/unifyDrawNew", headers={"bizchannelinfo": self.get_bizchannelinfo(), "authinfo": self.get_epay_authinfo()}, data={"drawType": draw_type, "bizFrom": "225", "activityId": "TTLXJ20210330"})
        if (result := res["result"]) and str(result.get("code")) == "0000" and str(result.get("data", {}).get("returnCode")) == "0":
            amount = result["data"].get("amount")
            msg = result["data"].get("awardTipContent", "").replace("xx", str(amount))
            self.logger.log(f"天天领现金打卡: {msg}", notify=True)
        else: self.logger.log(f"天天领现金打卡失败: {result}")

    @async_task("天天领现金查询余额")
    async def ttlxj_query_available(self):
        res = await self.http.post("https://epay.10010.com/ci-mcss-party-front/v1/ttlxj/queryAvailable", headers={"bizchannelinfo": self.get_bizchannelinfo(), "authinfo": self.get_epay_authinfo()})
        if (result := res["result"]) and str(result.get("code")) == "0000" and str(result.get("data", {}).get("returnCode")) == "0":
            self.logger.log(f"可用立减金: {float(result['data'].get('availableAmount', 0)) / 100:.2f}元", notify=True)
        else: self.logger.log(f"天天领现金查询余额失败: {result}")

    # ====================  主任务  ====================

    async def user_task(self):
        try:
            if not await self.online(): return
            await self.ttlxj_task()
        finally:
            await self.http.close()


async def send_tg_message(messages):
    """Telegram Bot 推送通知"""
    token = os.environ.get("TG_BOT_TOKEN", "")
    chat_id = os.environ.get("TG_CHAT_ID", "")
    if not token or not chat_id or not messages:
        return
    text = "📢 中国联通天天领现金\n\n" + "\n".join(messages)
    try:
        async with httpx.AsyncClient(timeout=30.0, verify=False) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
            )
            if response.status_code == 200:
                print("TG推送成功")
            else:
                print(f"TG推送失败: {response.text}")
    except Exception as e:
        print(f"TG推送异常: {e}")


async def main():
    start_time = datetime.now()
    print(f"开始运行时间: {start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    if not (cookies := os.environ.get("chinaUnicomCookie", "")): return print("未找到 chinaUnicomCookie 环境变量")
    tasks = [CustomUserService(cookie, index=i + 1).user_task() for i, cookie in enumerate(cookies.split("@")) if cookie.strip()]
    if tasks: print(f"启动 {len(tasks)} 个账号任务 (并行模式)..."); await asyncio.gather(*tasks)
    if _notify_messages:
        await send_tg_message(_notify_messages)
    print(f"\n运行结束, 总用时: {datetime.now() - start_time}")

if __name__ == "__main__": asyncio.run(main())
