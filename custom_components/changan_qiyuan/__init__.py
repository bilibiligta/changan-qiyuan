"""长安启源 (Changan Qiyuan) Home Assistant 集成.

当前版本状态（务必先读 README.md）：
  ✅ 只读车况可用：电量/续航/里程/温度/湿度/PM2.5/胎压/位置 等 201 个字段
  ✅ 控车可用（v0.2.0，2026-10-24 真车验证）：先 send_control_sms 发短信码、
     authorize_control 换会话级授权，之后 control 服务可连续下发指令；
     execute 必带 pin=RSA(控车码) + 一次性 pinVerifyCode，否则报 1004/3/40005/10。
  ✅ v0.3.0 新增 query_task：用 execute 返回的 taskId 查车端真实结果
     （tboxCmd / handleStatus）——code:0 只代表入队，不代表执行成功。
  ✅ v0.4.0 新增 climate 空调实体（18~32℃ + 1~5 档风量 + 速冷/速热）。
  ✅ v0.5.0 新增哨兵开关（switch）与主/副驾座椅加热档位（select），
     参数名来自 App 运行时下载的明文微应用，均已在真车实测通过。
"""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    ChanganConnectionError,
    ChanganControlBlocked,
    ChanganControlError,
    ChanganQiyuanApi,
)
from .const import (
    CMD_LABELS,
    CONF_CAR_ID,
    CONF_REFRESH_TOKEN,
    CONF_CONTROL_ENDPOINT,
    CONF_ENABLE_CONTROL,
    CONF_PIN,
    CONF_SCAN_INTERVAL,
    CONF_STORE_PIN,
    CONF_TOKEN,
    DEFAULT_CONTROL_ENDPOINT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .coordinator import ChanganCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[str] = ["sensor", "binary_sensor"]
CONTROL_PLATFORMS: list[str] = ["climate", "switch", "select", "lock", "cover", "button"]

SERVICE_CONTROL = "control"
SERVICE_CHECK_PIN = "check_pin"
SERVICE_REFRESH = "refresh"
SERVICE_SEND_CONTROL_SMS = "send_control_sms"
SERVICE_AUTHORIZE_CONTROL = "authorize_control"
SERVICE_QUERY_TASK = "query_task"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """从配置条目装载。"""
    session = async_get_clientsession(hass)
    api = ChanganQiyuanApi(session, token=entry.data.get(CONF_TOKEN))
    api.set_car_id(entry.data.get(CONF_CAR_ID))
    # 用 refresh token 续期的能力：登录时存下来的 refreshToken（有效期长），
    # access token 只有 1 小时，不续期就要每小时手动重新登录。
    api.rtoken = entry.data.get(CONF_REFRESH_TOKEN)

    async def _persist_token(new_token: str, new_rtoken: str | None) -> None:
        data = {**entry.data, CONF_TOKEN: new_token}
        if new_rtoken:
            data[CONF_REFRESH_TOKEN] = new_rtoken
        hass.config_entries.async_update_entry(entry, data=data)

    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
    coordinator = ChanganCoordinator(
        hass, api, scan_interval,
        entry.options.get(CONF_CONTROL_ENDPOINT, DEFAULT_CONTROL_ENDPOINT),
        entry=entry,
        persist_token=_persist_token,
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # 控车实体始终创建：没填控车码时实体存在但不可用（available=False），
    # 这样用户能在界面上看到「空调 / 座椅 / 哨兵 / 按钮」，而不是凭空消失。
    await hass.config_entries.async_forward_entry_setups(entry, CONTROL_PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    _async_register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    platforms = list(PLATFORMS) + list(CONTROL_PLATFORMS)
    ok = await hass.config_entries.async_unload_platforms(entry, platforms)
    if ok:
        hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    return ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.reload_config_entry(entry.entry_id)


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变更（轮询间隔 / 开关控车）时重载。"""
    await hass.config_entries.async_reload(entry.entry_id)


# ------------------------------------------------------------------ 服务
def _coordinator_for(hass: HomeAssistant, entry_id: str | None) -> ChanganCoordinator:
    entries = hass.data.get(DOMAIN, {})
    if entry_id:
        if entry_id not in entries:
            raise HomeAssistantError(f"找不到配置条目 {entry_id}")
        return entries[entry_id]
    if not entries:
        raise HomeAssistantError("尚未配置长安启源集成")
    return next(iter(entries.values()))


def _async_register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_CONTROL):
        return

    async def _handle_control(call: ServiceCall) -> None:
        """下发控车指令（预留接口，当前大概率被风控拦截）。"""
        coordinator = _coordinator_for(hass, call.data.get("entry_id"))
        cmd = call.data["cmd"]
        if cmd not in CMD_LABELS:
            _LOGGER.warning("指令 %s 不在已知指令表内，仍尝试下发", cmd)

        pin = call.data.get("pin")
        if not pin:
            stored = _stored_pin(hass, coordinator)
            if not stored:
                raise HomeAssistantError("未提供 pin，且该集成未保存控车码")
            pin = stored

        try:
            result = await coordinator.api.async_control(
                cmd, pin, endpoint=coordinator.control_endpoint)
        except ChanganControlBlocked as err:
            coordinator.control_authorized = False
            _LOGGER.error("控车被风控拦截：%s（指令 %s）", err, cmd)
            await _persist_notify(
                hass,
                "控车需要短信校验：请先调用 changan_qiyuan.send_control_sms，"
                "收到 6 位码后再调用 changan_qiyuan.authorize_control。",
            )
            raise HomeAssistantError(str(err)) from err
        except ChanganControlError as err:
            _LOGGER.error("控车失败：%s（指令 %s）", err, cmd)
            raise
        except ChanganConnectionError as err:
            _LOGGER.error("控车网络异常：%s", err)
            raise
        coordinator.control_authorized = True
        coordinator.last_control_task = result.get("data") or {}
        _LOGGER.info("控车指令 %s 已提交：%s", cmd, result)
        await coordinator.async_request_refresh()

    async def _handle_check_pin(call: ServiceCall) -> None:
        """只校验控车码是否正确，不下发指令（安全探针）。"""
        coordinator = _coordinator_for(hass, call.data.get("entry_id"))
        try:
            result = await coordinator.api.check_pin(call.data["pin"])
        except ChanganConnectionError as err:
            _LOGGER.error("校验控车码时网络异常：%s", err)
            raise
        ok = result.get("code") in (0, "0") and result.get("data") is True
        _LOGGER.info("控车码校验结果：%s -> %s", "正确" if ok else "错误", result)
        if not ok:
            raise HomeAssistantError(f"控车码错误：{result.get('msg') or result}")

    async def _handle_refresh(call: ServiceCall) -> None:
        coordinator = _coordinator_for(hass, call.data.get("entry_id"))
        await coordinator.async_request_refresh()

    async def _handle_send_control_sms(call: ServiceCall) -> None:
        """下发控车校验短信码（1004 的第一步）。手机号默认取配置里登录用的号码。"""
        coordinator = _coordinator_for(hass, call.data.get("entry_id"))
        mobile = call.data.get("mobile") or _stored_phone(hass, coordinator)
        if not mobile:
            raise HomeAssistantError("未提供 mobile，且配置条目里没有手机号")
        resp = await coordinator.api.send_control_sms(mobile)
        if resp.get("code") not in (0, "0"):
            raise HomeAssistantError(f"发送校验码失败：{resp.get('msg') or resp}")
        _LOGGER.warning("控车校验短信已发到 %s，请在 5 分钟内调用 "
                        "changan_qiyuan.authorize_control 并传入 auth_code", mobile)
        await _persist_notify(hass, f"控车校验短信已发到 {mobile}，收到 6 位码后请调用 "
                                    f"changan_qiyuan.authorize_control")

    async def _handle_authorize_control(call: ServiceCall) -> None:
        """用短信码换会话级控车授权；成功后可连续下发指令，不必每条都发码。"""
        coordinator = _coordinator_for(hass, call.data.get("entry_id"))
        try:
            await coordinator.api.control_auth(call.data["auth_code"])
        except ChanganControlError as err:
            _LOGGER.error("控车授权失败：%s", err)
            raise HomeAssistantError(f"控车授权失败：{err}") from err
        coordinator.control_authorized = True
        _LOGGER.info("控车授权成功，后续指令可直接下发（有效期见 TTL 探针）")
        await _persist_notify(hass, "控车授权成功 ✅ 现在可以正常控车了")

    async def _handle_query_task(call: ServiceCall) -> dict:
        """查询一条指令在车端的真实执行结果（execute 的 code:0 只代表入队）。"""
        coordinator = _coordinator_for(hass, call.data.get("entry_id"))
        task_id = call.data.get("task_id") or (
            coordinator.last_control_task or {}).get("taskId")
        if not task_id:
            raise HomeAssistantError("没有可查询的 taskId：请传 task_id，或先下发一条指令")
        try:
            info = await coordinator.api.query_task(task_id)
        except ChanganControlError as err:
            raise HomeAssistantError(f"查询指令结果失败：{err}") from err
        _LOGGER.info(
            "指令 %s 车端状态：%s %s（tboxCmd=%s）",
            task_id, info.get("handleStatus"), info.get("handleStatusDesc") or "",
            info.get("tboxCmd"),
        )
        return info

    hass.services.async_register(
        DOMAIN, SERVICE_CONTROL, _handle_control,
        schema=vol.Schema({
            vol.Required("cmd"): cv.string,
            vol.Optional("pin"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SEND_CONTROL_SMS, _handle_send_control_sms,
        schema=vol.Schema({
            vol.Optional("mobile"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_AUTHORIZE_CONTROL, _handle_authorize_control,
        schema=vol.Schema({
            vol.Required("auth_code"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_CHECK_PIN, _handle_check_pin,
        schema=vol.Schema({
            vol.Required("pin"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_REFRESH, _handle_refresh,
        schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_QUERY_TASK, _handle_query_task,
        schema=vol.Schema({
            vol.Optional("task_id"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }),
        supports_response=SupportsResponse.OPTIONAL,
    )


def _stored_pin(hass: HomeAssistant, coordinator: ChanganCoordinator) -> str | None:
    """取用户明确同意保存的控车码。默认不保存。"""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hass.data.get(DOMAIN, {}).get(entry.entry_id) is coordinator:
            if entry.options.get(CONF_STORE_PIN):
                return entry.options.get(CONF_PIN) or entry.data.get(CONF_PIN)
    return None


def _stored_phone(hass: HomeAssistant, coordinator: ChanganCoordinator) -> str | None:
    """取配置条目里登录用的手机号（用于发控车校验短信）。"""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if hass.data.get(DOMAIN, {}).get(entry.entry_id) is coordinator:
            return entry.data.get("phone") or entry.options.get("phone")
    return None


async def _persist_notify(hass: HomeAssistant, message: str) -> None:
    """往 HA 侧栏推一条持久通知（授权/短信类事件，用户需要看到）。"""
    try:
        await hass.services.async_call(
            "persistent_notification", "create",
            {"title": "长安启源", "message": message,
             "notification_id": "changan_qiyuan_control"},
            blocking=False,
        )
    except Exception:  # noqa: BLE001 - 通知失败不该影响主流程
        _LOGGER.debug("推送持久通知失败", exc_info=True)
