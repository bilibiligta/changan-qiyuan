"""车况传感器：数值类 + 原始枚举类（诊断用）。"""
from __future__ import annotations

import logging
from datetime import datetime

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, RAW_STATE_KEYS, SENSORS, SensorDesc
from .coordinator import ChanganCoordinator

_LOGGER = logging.getLogger(__name__)

# 车端字段异常值上界（超过视为 T-BOX 上报无效，实体置未知）
SANITY_MAX: dict[str, float] = {"totalOdometer": 500000.0}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ChanganCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [ChanganSensor(coordinator, entry, d) for d in SENSORS]
    entities += [ChanganRawSensor(coordinator, entry, k) for k in RAW_STATE_KEYS]
    entities.append(ChanganLocationSensor(coordinator, entry))
    async_add_entities(entities)


class _Base(CoordinatorEntity[ChanganCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        detail = (coordinator.car_detail.get("data") or {})
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {
            "identifiers": {(DOMAIN, car_id)},
            "name": f"启源 A07 {car_id[-6:].upper()}" if len(car_id) > 6 else "启源 A07",
            "manufacturer": "长安启源",
            "model": detail.get("seriesCode") or "A07",
            "configuration_url": None,
        }


class ChanganSensor(_Base, SensorEntity):
    """按 SENSORS 表生成的常规传感器。"""

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry, desc: SensorDesc):
        super().__init__(coordinator, entry)
        self.desc = desc
        self._attr_unique_id = f"{entry.entry_id}_{desc.key}"
        self._attr_name = desc.name
        self._attr_native_unit_of_measurement = desc.unit
        self._attr_device_class = desc.device_class
        self._attr_state_class = desc.state_class
        self._attr_icon = desc.icon

    @property
    def native_value(self):
        val = self.coordinator.raw(self.desc.key)
        if val is None:
            return None
        if self.desc.device_class is not None and str(
            getattr(self.desc.device_class, "value", self.desc.device_class)
        ) == "timestamp":
            return _parse_time(val)
        try:
            num = float(val)
        except (TypeError, ValueError):
            return val
        value = round(num * self.desc.factor, 3)
        limit = SANITY_MAX.get(self.desc.key)
        if limit is not None and value > limit:
            if not getattr(self, "_sanity_warned", False):
                self._sanity_warned = True
                _LOGGER.warning(
                    "车端字段 %s 上报异常值 %s %s，超过阈值 %s，已置为未知"
                    "（T-BOX 侧数据无效，请联系售后）",
                    self.desc.key, value, self.desc.unit or "", limit,
                )
            return None
        return value

    @property
    def extra_state_attributes(self):
        """「车况更新时间」额外暴露新鲜度，方便判断是不是云端给了旧快照。"""
        if self.desc.key != "lastUpdatedTime":
            return None
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


class ChanganRawSensor(_Base, SensorEntity):
    """原始枚举值。语义未标定，用于对照采样；标完语义后去填 const.BINARY_ON_VALUES。"""

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry, key: str):
        super().__init__(coordinator, entry)
        self.key = key
        self._attr_unique_id = f"{entry.entry_id}_raw_{key}"
        self._attr_name = f"{key} (原始值)"
        self._attr_icon = "mdi:numeric"

    @property
    def native_value(self):
        return self.coordinator.raw(self.key)

    @property
    def extra_state_attributes(self):
        return {"field": self.key}


def _parse_time(val) -> datetime | None:
    """云端时间形如 "2026-10-12 15:37:24.440"（本地时间、无时区）。

    HA 的 device_class=TIMESTAMP 要求带时区，否则会报
    "Error adding entity ... for domain sensor"，所以这里补上 HA 配置时区。
    """
    if isinstance(val, datetime):
        parsed = val
    elif isinstance(val, (int, float)) or (isinstance(val, str) and val.isdigit()):
        try:
            num = float(val)
        except (TypeError, ValueError):
            return None
        if num > 1e11:  # 毫秒
            num /= 1000.0
        try:
            parsed = datetime.fromtimestamp(num, tz=dt_util.DEFAULT_TIME_ZONE)
            return parsed
        except (OverflowError, OSError, ValueError):
            return None
    elif isinstance(val, str) and val:
        txt = val.replace("T", " ").strip()
        parsed = None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(txt, fmt)
                break
            except ValueError:
                continue
        if parsed is None:
            return None
    else:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.DEFAULT_TIME_ZONE)
    return parsed


class ChanganLocationSensor(_Base, SensorEntity):
    """车辆位置：状态=地址，属性带经纬度/GPS 时间（供地图卡片与卡片插件取用）。"""

    _attr_name = "车辆位置"
    _attr_icon = "mdi:map-marker-radius"
    _attr_state_class = None

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_location"

    @property
    def native_value(self):
        loc = self.coordinator.location or {}
        addr = loc.get("address")
        city = loc.get("city") or ""
        if not addr:
            return None
        return f"{city}{addr}" if city and not str(addr).startswith(city) else addr

    @property
    def extra_state_attributes(self):
        loc = self.coordinator.location or {}
        def num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        return {
            "纬度": num(loc.get("lat")),
            "经度": num(loc.get("lng")),
            "城市": loc.get("city"),
            "区县": loc.get("district") or loc.get("county"),
            "行政区划码": loc.get("adCode"),
            "GPS时间": loc.get("gpsTime"),
            "速度": loc.get("speed"),
            "定位有效": loc.get("validGps"),
            "定位来源": "长安云端 /app2/api/car/location",
        }
