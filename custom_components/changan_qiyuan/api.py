"""长安启源云端 API 客户端（纯 aiohttp，无第三方依赖）.

踩坑点全部写在注释里，改这个文件时请一并维护 README.md 的「踩坑清单」：

1. token 走 query `?token=`，不是 header。header `X-VCS-User-Token` 在 /app2/* 上无效。
2. 请求体必须是 application/x-www-form-urlencoded；传 JSON 会报「缺少必填参数：carId」。
3. carId 登录响应里恒为 null，必须单独 GET /app2/api/v3/car/detail 取。
4. 控车码(PIN)必须先用 /app2/api/security/key 取的 RSA 公钥做 PKCS#1 v1.5 加密，
   明文直接传会报「密钥已过期」；本机无 cryptography 库，这里是纯 Python 实现。
5. 控车链路（2026-10 实测跑通）：短信码 -> /app2/api/control/auth -> execute；
   execute 必带 pin=RSA(控车码) + 一次性 pinVerifyCode，否则 1004/3/40005/10。
"""
from __future__ import annotations

import asyncio
import base64
import json
import time
import logging
import secrets
from typing import Any
from urllib.parse import urlencode

import aiohttp

from .const import (
    BASE_URL,
    BUSY_RETRY,
    BUSY_WAIT,
    CMD_EXTRA_PARAMS,
    CONTROL_ENDPOINTS,
    DEFAULT_CONTROL_ENDPOINT,
    OAUTH_CLIENT_ID,
    PATH_CAR_DATA,
    PATH_CAR_DETAIL,
    PATH_CHECK_PIN,
    PATH_CONTROL_AUTH,
    PATH_CONTROL_INFO,
    PATH_CONTROL_SMS,
    PATH_LOCATION,
    PATH_LOGIN,
    PATH_PIN_CODE,
    PATH_REFRESH_TOKEN,
    PATH_RENEW_TOKEN,
    PATH_SECURITY_KEY,
    PATH_SEND_SMS,
    SMS_USAGE_CONTROL,
    USER_AGENT,
    VCS_APP_ID,
)
from .rsa_utils import parse_rsa_pubkey, rsa_pkcs1v15_encrypt

_LOGGER = logging.getLogger(__name__)


# ------------------------------------------------------------------ 异常
class ChanganError(Exception):
    """基础异常。"""


class ChanganAuthError(ChanganError):
    """token 失效 / 登录失败。"""


class ChanganConnectionError(ChanganError):
    """网络异常。"""


class ChanganControlError(ChanganError):
    """控车失败。"""

    def __init__(self, code: Any, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message


class ChanganControlBlocked(ChanganControlError):
    """风控拦截（code 1004 / 20 / 100511 等）。"""


# 已知错误码 -> 人话
ERROR_HINTS = {
    1: "解密失败（pin 传了明文，必须 RSA 加密）",
    2: "token 已过期，需要重新登录",
    3: "输入控车码（缺 pin，或 cmd 不被该车支持）",
    10: "指令发送异常[4000]（pinVerifyCode 已被用过，或 pin 编解码不对）",
    20: "控车码错误",
    21: "控车码错误（服务端判定 PIN 不对）",
    999: "上一条指令尚未执行完成，稍后重试",
    1004: "存在账户安全，请输入校验码 —— 需要先 send_control_sms 拿码再做 control_auth",
    -1000: "密钥已过期（多半是参数缺失，v3 端点还额外要一个参数 s）",
    40005: "缺少必填参数（通常是 pinVerifyCode 或 pin 没带）",
    100511: "验证码无效或已过期（短信码一次性）",
    40003: "调用过快，操作频繁（换手机号或等 1 分钟）",
    4000: "指令下发失败（通常与鉴权参数形态不对有关）",
}


# ------------------------------------------------------------------ 客户端
class ChanganQiyuanApi:
    def __init__(self, session: aiohttp.ClientSession, token: str | None = None):
        self._session = session
        self.token = token
        self.rtoken: str | None = None
        self.expires_at: float = 0.0
        self._car_id: str | None = None

    @property
    def car_id_cache(self) -> str | None:
        return self._car_id

    def set_car_id(self, car_id: str | None) -> None:
        self._car_id = car_id

    # --- 底层 ---
    async def _request(
        self,
        method: str,
        path: str,
        params: dict | None = None,
        data: dict | None = None,
        timeout: int = 25,
    ) -> dict:
        url = BASE_URL + path
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "vcs-app-id": VCS_APP_ID,
        }
        body = None
        if data:
            # 必须是 form-urlencoded，不能是 JSON。
            # 实测通过的那版客户端对空参数（car/data）是「完全不带 body」，这里保持一致。
            headers["Content-Type"] = "application/x-www-form-urlencoded"
            body = urlencode(data).encode()
        try:
            async with self._session.request(
                method, url, params=params, data=body, headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                raw = await resp.text(errors="replace")
        except aiohttp.ClientError as err:
            raise ChanganConnectionError(str(err)) from err
        except TimeoutError as err:
            raise ChanganConnectionError("请求超时") from err

        try:
            payload = json.loads(raw)
        except Exception:  # noqa: BLE001
            raise ChanganConnectionError(f"响应不是 JSON: {raw[:200]}") from None
        if not isinstance(payload, dict):
            raise ChanganConnectionError(f"响应格式异常: {raw[:200]}")
        return payload

    def _params(self, **kw) -> dict:
        p = dict(kw)
        if self.token:
            p["token"] = self.token
        return p

    # --- 登录 ---
    async def send_sms(self, phone: str) -> dict:
        return await self._request("POST", PATH_SEND_SMS, data={"phone": phone, "type": 1})

    async def login(self, phone: str, verify_code: str) -> str:
        resp = await self._request(
            "POST", PATH_LOGIN, data={"phone": phone, "verifyCode": verify_code}
        )
        data = resp.get("data") or {}
        token = data.get("token") or data.get("accessToken") or resp.get("token")
        if not token:
            raise ChanganAuthError(f"登录失败: {resp}")
        self.token = token

        exp = (resp.get("data") or {}).get("expires_in") or resp.get("expires_in")
        if exp:
            try:
                self.expires_at = time.time() + float(exp) - 60
            except (TypeError, ValueError):
                self.expires_at = 0.0
        # 登录响应里同时带 refreshToken（有效期远长于 1 小时的 access token），
        # 存下来用于后续自动续期，避免每小时都要重新登录。
        self.rtoken = (data.get("refreshToken") or data.get("refresh_token")
                       or data.get("refreshTicket"))
        self._car_id = None
        return token

    async def renewal_token(self) -> str | None:
        """续期 token。实测网关对此接口不认账（返回 token expired），故仅作预留。"""
        resp = await self._request("POST", PATH_RENEW_TOKEN, data={"token": self.token})
        return (resp.get("data") or {}).get("token")

    # --- 车辆 ---
    async def car_detail(self) -> dict:
        resp = await self._request("GET", PATH_CAR_DETAIL, params=self._params(type=0))
        if resp.get("code") not in (0, "0", None):
            raise ChanganAuthError(f"获取车辆信息失败: {resp}")
        return resp

    async def car_id(self, refresh: bool = False) -> str:
        if self._car_id and not refresh:
            return self._car_id
        detail = await self.car_detail()
        self._car_id = (detail.get("data") or {}).get("carId")
        if not self._car_id:
            raise ChanganAuthError("carId 为空，账号下可能没有车辆")
        return self._car_id

    async def car_data(self) -> dict:
        car_id = await self.car_id()
        # 注意：carId/keys 必须走 body。只放 query 会返回 code:1「尚无车况信息」。
        resp = await self._request(
            "POST", PATH_CAR_DATA,
            params=self._params(),
            data={"carId": car_id, "keys": "*"},
        )
        if resp.get("code") not in (0, "0", None):
            raise ChanganAuthError(f"获取车况失败: {resp.get('code')} {resp.get('msg')}")
        return resp.get("data") or {}

    async def location(self) -> dict:
        car_id = await self.car_id()
        resp = await self._request("GET", PATH_LOCATION, params=self._params(carId=car_id))
        return resp.get("data") or {}

    # --- 控车鉴权 ---
    async def security_key(self) -> str:
        resp = await self._request("GET", PATH_SECURITY_KEY, params=self._params())
        return resp.get("data")

    async def check_pin(self, pin: str) -> dict:
        """校验控车码是否正确。正确返回 {"code":0,"data":true}，错码返回 code:21。"""
        n, e = parse_rsa_pubkey(await self.security_key())
        enc = rsa_pkcs1v15_encrypt(pin.encode(), n, e)
        car_id = await self.car_id()
        return await self._request(
            "POST", PATH_CHECK_PIN, params=self._params(),
            data={"carId": car_id, "pin": enc},
        )

    async def create_pin_verify_code(self) -> str:
        """返回形如 "<19位id>::<32位hex>" 的校验串。"""
        car_id = await self.car_id()
        resp = await self._request(
            "POST", PATH_PIN_CODE, params=self._params(), data={"carId": car_id}
        )
        return resp.get("data")

    # --- 短信码换控车授权（1004 的正解）---
    async def send_control_sms(self, mobile: str) -> dict:
        """请求一条控车校验短信码。实测同一手机号 1 分钟内取第二码会 code:40003。"""
        return await self._request(
            "POST", PATH_CONTROL_SMS, params=self._params(),
            data={"mobile": mobile, "usage": SMS_USAGE_CONTROL},
        )

    async def control_auth(self, auth_code: str) -> dict:
        """用短信码换会话级控车授权。返回 code:0 后 execute 不再被 1004 拦。"""
        car_id = await self.car_id()
        resp = await self._request(
            "POST", PATH_CONTROL_AUTH, params=self._params(),
            data={"carId": car_id, "authCode": auth_code},
        )
        if resp.get("code") not in (0, "0"):
            msg = resp.get("msg") or ERROR_HINTS.get(resp.get("code"), "授权失败")
            if str(resp.get("code")) == "100511":
                raise ChanganControlError(100511, f"{msg}（短信码一次性，用过或超时）")
            raise ChanganControlError(resp.get("code"), msg)
        return resp

    @property
    def needs_refresh(self) -> bool:
        """access token 是否即将（或已经）过期。"""
        if not self.expires_at:
            return False
        return time.time() >= self.expires_at

    async def refresh_token(self, refresh_token: str) -> str | None:
        """用 refresh token 换新 access token（有效期 3600s）。"""
        resp = await self._request(
            "GET", PATH_REFRESH_TOKEN,
            params={"client_id": OAUTH_CLIENT_ID, "refresh_token": refresh_token,
                    "grant_type": "refresh_token"},
        )
        # 真实响应是扁平结构（2026-10-25 实测）：
        # {"status_code":"0","message":"成功","access_token":"...","expires_in":3600,
        #  "refresh_token":"...","openid":"...","scope":null}
        data = resp.get("data") or {}
        token = (
            data.get("access_token") or data.get("accessToken")
            or resp.get("access_token") or resp.get("accessToken")
            or resp.get("token")
        )
        if token:
            self.token = token
        new_rt = (
            data.get("refresh_token") or data.get("refreshToken")
            or resp.get("refresh_token") or resp.get("refreshToken")
        )
        if new_rt:
            self.rtoken = new_rt
        exp = data.get("expires_in") or resp.get("expires_in")
        if token and exp:
            try:
                self.expires_at = time.time() + float(exp) - 60
            except (TypeError, ValueError):
                self.expires_at = 0.0
        if token:
            _LOGGER.info("access token 已通过 refresh token 续期成功")
        return token

    # --- 下发指令 ---
    async def query_task(self, task_id: str) -> dict:
        """查一条指令的执行结果（2026-10-24 实测可用）。

        返回 data: {tboxCmd, handleStatus, handleStatusDesc, cmdSendTime,
                    cmdFinishTime, taskId, requestId, handleCode}
        handleStatus=Completed + handleCode=0 才算真的执行成功；
        execute 返回 code:0 只代表「已入队」，不代表车端执行成功。
        """
        car_id = await self.car_id()
        resp = await self._request(
            "POST", PATH_CONTROL_INFO, params=self._params(),
            data={"carId": car_id, "id": task_id},
        )
        if resp.get("code") not in (0, "0"):
            raise ChanganControlError(resp.get("code"), resp.get("msg") or "查询指令失败")
        return resp.get("data") or {}

    async def async_control(
        self,
        cmd: str,
        pin: str,
        endpoint: str = DEFAULT_CONTROL_ENDPOINT,
        retry_on_busy: int = BUSY_RETRY,
        busy_wait: float = BUSY_WAIT,
        extra: dict | None = None,
    ) -> dict:
        """下发一条控车指令。

        实测正确的 payload（缺一个都不行）：
            carId + cmd + pin=RSA(PIN) + pinVerifyCode=<本次新取的 vc>
            - 缺 pin        -> code:3「输入控车码」
            - pin 传明文    -> code:1「解密失败」
            - 缺 vc         -> code:40005「缺少必填参数：pinVerifyCode」
            - vc 复用/过期  -> code:10「指令发送异常，请重试[4000]」
        前置条件：已用 /app2/api/control/auth 认过短信码，否则一律 code:1004。
        """
        path = CONTROL_ENDPOINTS.get(endpoint, CONTROL_ENDPOINTS[DEFAULT_CONTROL_ENDPOINT])
        car_id = await self.car_id()
        n, e = parse_rsa_pubkey(await self.security_key())
        enc_pin = rsa_pkcs1v15_encrypt(pin.encode(), n, e)

        payload = {"carId": car_id, "cmd": cmd, "pin": enc_pin}
        payload.update(CMD_EXTRA_PARAMS.get(cmd, {}))
        if extra:
            payload.update({k: str(v) for k, v in extra.items()})

        last: dict = {}
        for attempt in range(max(1, retry_on_busy)):
            # vc 一次一用，每轮都重新取
            payload["pinVerifyCode"] = await self.create_pin_verify_code()
            resp = await self._request("POST", path, params=self._params(), data=payload)
            raw_code = resp.get("code")
            try:
                code: Any = int(raw_code)
            except (TypeError, ValueError):
                code = raw_code
            last = resp
            if code in (0, "0"):
                return resp
            if code == 999:      # 上一条指令尚未执行完成 -> 等一会重试
                await asyncio.sleep(busy_wait)
                continue
            if code == 10:       # vc 被消费/过期，换新 vc 再试一次
                continue
            break

        msg = last.get("msg") or last.get("message") or ERROR_HINTS.get(code, "未知错误")
        if code == 1004:
            raise ChanganControlBlocked(
                code, f"{msg} —— 需要先做短信码授权（send_control_sms + control_auth）"
            )
        raise ChanganControlError(code, msg)
