"""二元传感器（门窗/灯/空调等）。

⚠️ 枚举语义尚未标定：const.BINARY_CALIBRATED 目前是 False，
   所以这些实体默认处于「已禁用」状态，不会污染你的面板。
   标定方法见 README.md「枚举语义标定」，标定完把 BINARY_CALIBRATED 改成 True。
"""
from __future__ import annotations

import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BINARY_CALIBRATED, BINARY_SENSORS, DOMAIN, BinaryDesc
from .coordinator import ChanganCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ChanganCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ChanganBinarySensor(coordinator, entry, d) for d in BINARY_SENSORS
    )


class ChanganBinarySensor(CoordinatorEntity[ChanganCoordinator], BinarySensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry, desc: BinaryDesc):
        super().__init__(coordinator)
        self.desc = desc
        detail = (coordinator.car_detail.get("data") or {})
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, car_id)},
            "name": f"启源 A07 {car_id[-6:].upper()}" if len(car_id) > 6 else "启源 A07",
            "manufacturer": "长安启源",
            "model": detail.get("seriesCode") or "A07",
        }
        self._attr_unique_id = f"{entry.entry_id}_bin_{desc.key}"
        self._attr_name = desc.name
        self._attr_device_class = desc.device_class
        self._attr_icon = desc.icon
        self._attr_entity_registry_enabled_default = BINARY_CALIBRATED

    @property
    def is_on(self):
        # 空调：云端 airStatus 恒为 1，不可用；改用风量 frontReqWindLevel > 0 判定（与 climate 一致）
        if self.desc.key == "airStatus":
            try:
                return float(self.coordinator.raw("frontReqWindLevel") or 0) > 0
            except (TypeError, ValueError):
                return False
        val = self.coordinator.raw(self.desc.key)
        try:
            return int(val) in self.desc.on_values
        except (TypeError, ValueError):
            return str(val) in {str(v) for v in self.desc.on_values}

    @property
    def extra_state_attributes(self):
        return {
            "field": self.desc.key,
            "raw_value": self.coordinator.raw(self.desc.key),
            "on_values": list(self.desc.on_values),
            "calibrated": BINARY_CALIBRATED,
        }
