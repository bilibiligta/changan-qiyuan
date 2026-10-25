"""车门锁（lock 实体）。

原先「解锁」「闭锁」是两个按钮，现在合成一个 HA 原生 lock 实体：
  - 显示「已锁 / 未锁」（来自车况 `leftFrontDoorLock`：0=已锁，1=未锁）
  - 点锁 / 点开分别下发 LockDoor / UnLockDoor（解锁需要控车码）
"""
from __future__ import annotations

import logging

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ChanganControlError
from .const import CMD_LABELS, CMD_LOCK, CMD_UNLOCK, CONF_PIN, DOMAIN, KEY_DOOR_LOCK
from .coordinator import ChanganCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ChanganCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ChanganDoorLock(coordinator, entry)])


class ChanganDoorLock(CoordinatorEntity[ChanganCoordinator], LockEntity):
    """整车车门锁。"""

    _attr_has_entity_name = True
    _attr_name = "车门锁"
    _attr_icon = "mdi:car-door-lock"

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_door_lock"

    @property
    def available(self) -> bool:
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def _raw(self) -> int | None:
        try:
            return int(self.coordinator.raw(KEY_DOOR_LOCK))
        except (TypeError, ValueError):
            return None

    @property
    def is_locked(self) -> bool | None:
        """`leftFrontDoorLock`：0=已锁，1=未锁；读不到就返回 None（未知）。"""
        raw = self._raw
        if raw is None:
            return None
        return raw == 0

    @property
    def extra_state_attributes(self) -> dict:
        raw = self._raw
        attrs = {
            "车锁状态": {0: "已锁", 1: "未锁"}.get(raw, "未知"),
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

    async def async_lock(self, **kwargs) -> None:
        await self._send(CMD_LOCK)

    async def async_unlock(self, **kwargs) -> None:
        await self._send(CMD_UNLOCK)
