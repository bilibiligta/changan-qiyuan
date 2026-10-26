"""控车按钮（预留）。

只有在「集成选项 → 启用控车实体」打开、且已保存控车码时才会创建这些按钮。
按下即走 api.async_control()：
    security/key -> RSA(PIN) -> control/execute
目前服务端对 control/execute 返回 code:1004（账户安全拦截），
所以按钮按下会抛错并在日志里给出明确原因。风控解除后这里无需改动。
"""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ChanganControlError
from .const import BUTTON_CMDS, CMD_LABELS, CONF_PIN, DOMAIN, car_flags, cmd_supported
from .coordinator import ChanganCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ChanganCoordinator = hass.data[DOMAIN][entry.entry_id]
    # 「立即刷新车况」始终创建（不受功能开关表限制）
    async_add_entities([ChanganRefreshButton(coordinator, entry)])
    # 只创建本车 App 里真的有的功能（car/detail 的 # 开关表），避免空按钮
    flags = car_flags(coordinator.car_detail.get("data") or coordinator.car_detail)
    cmds = [cmd for cmd in BUTTON_CMDS if cmd_supported(cmd, flags)]
    skipped = [cmd for cmd in BUTTON_CMDS if cmd not in cmds]
    if skipped:
        _LOGGER.info("本车不支持，跳过按钮：%s", ", ".join(skipped))
    if not cmds:
        _LOGGER.warning("功能开关表里没有任何可控功能，跳过创建按钮")
        return
    async_add_entities(ChanganControlButton(coordinator, entry, cmd) for cmd in cmds)


class ChanganControlButton(CoordinatorEntity[ChanganCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry, cmd: str):
        super().__init__(coordinator)
        self.cmd = cmd
        self.entry = entry
        detail = (coordinator.car_detail.get("data") or {})
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_btn_{cmd}"
        self._attr_name = CMD_LABELS.get(cmd, cmd)
        self._attr_icon = "mdi:car-connected"

    @property
    def available(self) -> bool:
        """没保存控车码时按钮不可用（但仍显示，方便发现）。"""
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def extra_state_attributes(self) -> dict:
        return self.coordinator.timeline_text()

    async def async_press(self) -> None:
        pin = self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN)
        if not pin:
            raise HomeAssistantError("未设置控车码：请在集成「配置」里填写 6 位控车码")
        try:
            resp = await self.coordinator.api.async_control(
                self.cmd, pin, endpoint=self.coordinator.control_endpoint)
        except ChanganControlError as err:
            raise HomeAssistantError(f"控车失败：{err}") from err
        self.coordinator.note_command(self.cmd, resp)
        self.coordinator.async_fast_poll()


class ChanganRefreshButton(CoordinatorEntity[ChanganCoordinator], ButtonEntity):
    """立即向云端拉一次车况（实测每次请求都会真实唤醒 T-BOX）。

    车端深度休眠时云端会原样返回旧快照，此时 HA 里手动按一下这个按钮，
    比等下一个轮询周期更快，也能配合「冲刺唤醒」把车叫醒。
    """

    _attr_has_entity_name = True
    _attr_name = "立即刷新车况"
    _attr_icon = "mdi:cloud-refresh"

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        detail = (coordinator.car_detail.get("data") or {})
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_btn_refresh_now"

    @property
    def extra_state_attributes(self) -> dict:
        coord = self.coordinator
        attrs = {
            "数据龄期(分钟)": coord.data_age_min,
            "数据陈旧": coord.stale,
            "冲刺唤醒次数": coord.stale_retries,
            "轮询间隔(分钟)": coord.scan_interval,
        }
        if coord.last_poll_error:
            attrs["上次轮询错误"] = coord.last_poll_error
        return attrs

    async def async_press(self) -> None:
        await self.coordinator.async_refresh()
