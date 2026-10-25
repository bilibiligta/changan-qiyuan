"""后备箱（cover 实体）。

原先「开后备箱」「关后备箱」是两个按钮，现在合成一个原生 cover：
  - 显示「已关 / 已开」（来自车况 `trunk`：3=已关）
  - 打开 / 关闭分别下发 OpenTrunk / CloseTrunk
"""
from __future__ import annotations

import logging

from homeassistant.components.cover import CoverDeviceClass, CoverEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ChanganControlError
from .const import (
    CMD_CLOSE_TRUNK,
    CMD_LABELS,
    CMD_OPEN_TRUNK,
    CONF_PIN,
    DOMAIN,
    KEY_TRUNK,
)
from .coordinator import ChanganCoordinator

_LOGGER = logging.getLogger(__name__)

# 车况 trunk 字段：3 = 已关闭（本车实测）；其它值视为打开
TRUNK_CLOSED_VALUE = 3


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ChanganCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ChanganTrunkCover(coordinator, entry)])


class ChanganTrunkCover(CoordinatorEntity[ChanganCoordinator], CoverEntity):
    """后备箱盖。"""

    _attr_has_entity_name = True
    _attr_name = "后备箱"
    _attr_device_class = CoverDeviceClass.DOOR
    _attr_icon = "mdi:car-back"

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_trunk"

    @property
    def available(self) -> bool:
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def _raw(self) -> int | None:
        try:
            return int(self.coordinator.raw(KEY_TRUNK))
        except (TypeError, ValueError):
            return None

    @property
    def is_closed(self) -> bool | None:
        raw = self._raw
        if raw is None:
            return None
        return raw == TRUNK_CLOSED_VALUE

    @property
    def extra_state_attributes(self) -> dict:
        raw = self._raw
        attrs = {
            "后备箱状态": {3: "已关闭"}.get(raw, "未关闭" if raw is not None else "未知"),
            "状态原始值": raw,
        }
        attrs.update(self.coordinator.timeline_text())
        return attrs

    async def _send(self, cmd: str) -> None:
        pin = self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN)
        try:
            resp = await self.coordinator.api.async_control(
                cmd, pin, endpoint=self.coordinator.control_endpoint)
        except ChanganControlError as err:
            raise HomeAssistantError(f"{CMD_LABELS.get(cmd, cmd)}失败：{err}") from err
        self.coordinator.note_command(cmd, resp)
        self.coordinator.async_fast_poll()

    async def async_open_cover(self, **kwargs) -> None:
        await self._send(CMD_OPEN_TRUNK)

    async def async_close_cover(self, **kwargs) -> None:
        await self._send(CMD_CLOSE_TRUNK)
