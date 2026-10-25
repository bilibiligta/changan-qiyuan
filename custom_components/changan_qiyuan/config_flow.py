"""配置流程：手机号 → 短信验证码 → 登录 → 可选保存控车码。

隐私约定：
  - 手机号 / 短信验证码 / 控车码都只在流程里流转，不会写进日志；
  - 控车码默认不落盘（CONF_STORE_PIN 默认 False），不勾选就只在内存里用一次。
"""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ChanganAuthError,
    ChanganConnectionError,
    ChanganQiyuanApi,
)
from .const import (
    CONF_CAR_ID,
    CONF_CONTROL_ENDPOINT,
    CONF_ENABLE_CONTROL,
    CONF_PIN,
    CONF_REFRESH_TOKEN,
    CONF_SCAN_INTERVAL,
    CONF_STORE_PIN,
    CONF_TOKEN,
    DEFAULT_CONTROL_ENDPOINT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema({vol.Required("phone"): str})
STEP_CODE_SCHEMA = vol.Schema({vol.Required("verify_code"): str})


class ChanganConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._phone: str | None = None
        self._api: ChanganQiyuanApi | None = None
        self._token: str | None = None
        self._reauth_entry: ConfigEntry | None = None

    # -------------------------------------------------------------- 工具
    @property
    def _client(self) -> ChanganQiyuanApi:
        if self._api is None:
            self._api = ChanganQiyuanApi(async_get_clientsession(self.hass))
        return self._api

    # -------------------------------------------------------------- 主流程
    async def async_step_user(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            phone = user_input["phone"].strip()
            try:
                await self._client.send_sms(phone)
            except ChanganConnectionError:
                errors["base"] = "cannot_connect"
            else:
                self._phone = phone
                return await self.async_step_code()
        return self.async_show_form(
            step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    async def async_step_code(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            code = user_input["verify_code"].strip()
            try:
                self._token = await self._client.login(self._phone, code)
                await self._client.car_id(refresh=True)
            except ChanganAuthError:
                errors["base"] = "invalid_auth"
            except ChanganConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_pin()
        return self.async_show_form(
            step_id="code", data_schema=STEP_CODE_SCHEMA, errors=errors,
            description_placeholders={"phone": self._phone or ""},
        )

    async def async_step_pin(self, user_input: dict | None = None) -> ConfigFlowResult:
        """控车码（可跳过）。不勾选保存就完全不落盘。"""
        errors: dict[str, str] = {}
        if user_input is not None:
            pin = (user_input.get("pin") or "").strip()
            if not pin:
                return self._create_entry({}, {})
            try:
                ok = await self._client.check_pin(pin)
            except ChanganConnectionError:
                errors["base"] = "cannot_connect"
            else:
                if ok.get("code") in (0, "0") and ok.get("data") is True:
                    if user_input.get(CONF_STORE_PIN):
                        return self._create_entry(
                            {CONF_PIN: pin},
                            {CONF_STORE_PIN: True, CONF_ENABLE_CONTROL: bool(
                                user_input.get(CONF_ENABLE_CONTROL))},
                        )
                    return self._create_entry(
                        {},
                        {CONF_STORE_PIN: False, CONF_ENABLE_CONTROL: False},
                    )
                errors["base"] = "invalid_pin"
        return self.async_show_form(
            step_id="pin",
            data_schema=vol.Schema({
                vol.Optional(CONF_PIN): str,
                vol.Optional(CONF_STORE_PIN, default=True): bool,
                vol.Optional(CONF_ENABLE_CONTROL, default=True): bool,
            }),
            errors=errors,
        )

    def _create_entry(self, extra_data: dict, options: dict) -> ConfigFlowResult:
        car_id = self._client.car_id_cache
        title = f"启源 A07 {car_id[-6:].upper()}" if car_id and len(car_id) > 6 else "启源 A07"

        if self._reauth_entry is not None:
            self.hass.config_entries.async_update_entry(
                self._reauth_entry,
                data={**self._reauth_entry.data, CONF_TOKEN: self._token,
                      CONF_REFRESH_TOKEN: self._client.rtoken,
                      CONF_CAR_ID: car_id, **extra_data},
            )
            self.hass.async_create_task(
                self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
            )
            return self.async_abort(reason="reauth_successful")

        opts = {
            CONF_SCAN_INTERVAL: DEFAULT_SCAN_INTERVAL,
            CONF_CONTROL_ENDPOINT: DEFAULT_CONTROL_ENDPOINT,
            CONF_STORE_PIN: False,
            CONF_ENABLE_CONTROL: True,
        }
        opts.update(options)
        return self.async_create_entry(
            title=title,
            data={CONF_TOKEN: self._token, CONF_CAR_ID: car_id,
                  CONF_REFRESH_TOKEN: self._client.rtoken, **extra_data},
            options=opts,
        )

    # -------------------------------------------------------------- 重新认证
    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        self._phone = None
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._phone = user_input["phone"].strip()
            try:
                await self._client.send_sms(self._phone)
            except ChanganConnectionError:
                errors["base"] = "cannot_connect"
            else:
                return await self.async_step_code()
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=STEP_USER_SCHEMA, errors=errors
        )

    # -------------------------------------------------------------- 选项
    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        # HA 2025.x 起 OptionsFlow 不接受构造参数，改用 self.config_entry
        return ChanganOptionsFlow()


class ChanganOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict | None = None) -> ConfigFlowResult:
        if user_input is not None:
            opts = dict(self.config_entry.options)
            opts.update(user_input)
            return self.async_create_entry(title="", data=opts)
        cur = self.config_entry.options
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(CONF_SCAN_INTERVAL,
                             default=cur.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)):
                    vol.All(vol.Coerce(int), vol.Range(min=1, max=1440)),
                vol.Optional(CONF_CONTROL_ENDPOINT,
                             default=cur.get(CONF_CONTROL_ENDPOINT, DEFAULT_CONTROL_ENDPOINT)):
                    vol.In(["v1", "v2", "v3"]),
                vol.Optional(CONF_STORE_PIN, default=cur.get(CONF_STORE_PIN, False)): bool,
                vol.Optional(CONF_PIN, default=cur.get(CONF_PIN, "")): str,
                vol.Optional(CONF_ENABLE_CONTROL,
                             default=cur.get(CONF_ENABLE_CONTROL, True)): bool,
            }),
        )
