"""座椅加热档位选择器（2026-10-25 启源 A07 真车实测通过）。

参数名来自 App 运行时下载的明文微应用（APK 内的 dex 是爱加密，读不到）：
    oneButtonStart/main.js: {cmd: "SeatHeating", lfSeatStatus: <档位>} / rfSeatStatus
    oneButtonStart/carControlHome.js: 座椅面板用 {lfSeatSet: 1(加热)/2(通风), lfSeatStatus: <档位>}

实测（主驾）：
    lfSeatStatus=1 -> driverSeatHeatStatus 6→1     （从关闭态直接开启，1 档）
    lfSeatStatus=3 -> 1→3
    lfSeatStatus=0 -> 3→6                          （0 = 关闭）
回读 driverSeatHeatStatus / passengerSeatHeatStatus：6=关闭，1/2/3=档位。
"""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ChanganControlError
from .const import (
    AC_DEFAULT_WIND,
    AC_FAN_LEVELS,
    AC_FAN_MAX,
    AC_TEMP_MAX,
    AC_TEMP_MIN,
    CMD_CLOSE_AIR,
    CMD_OPEN_AIR,
    CMD_SEAT_HEATING,
    CMD_SEAT_HEATING_DRIVER,
    CMD_SEAT_HEATING_PASSENGER,
    CMD_SET_SENTRY_MODE,
    CONF_PIN,
    DOMAIN,
    KEY_AC_TEMP_SET,
    KEY_AC_WIND,
    KEY_AC_TEMP_SET,
    KEY_AC_WIND,
    KEY_AC_TEMP_SET,
    KEY_AC_WIND,
    KEY_SENTRY_SENSITIVITY,
    KEY_SENTRY_STATE,
    SEAT_HEAT_STATUS_DRIVER,
    SEAT_HEAT_STATUS_PASSENGER,
    SEAT_LABELS,
    SEAT_OFF_READBACK,
    SENTRY_ON_VALUES,
    SENTRY_PARAM_SENSITIVITY,
    SENTRY_PARAM_SWITCH,
    SENTRY_SENSITIVITY_LABELS,
    SENTRY_SENSITIVITY_VALUES,
)
from .coordinator import ChanganCoordinator

_LOGGER = logging.getLogger(__name__)

OPTIONS = ["关", "1档", "2档", "3档"]
_VALUE_BY_OPTION = {"关": 0, "1档": 1, "2档": 2, "3档": 3}
_OPTION_BY_VALUE = {v: k for k, v in _VALUE_BY_OPTION.items()}

_SENS_VALUE_BY_OPTION = {SENTRY_SENSITIVITY_LABELS[v]: v for v in SENTRY_SENSITIVITY_VALUES}
_SENS_OPTION_BY_VALUE = {v: k for k, v in _SENS_VALUE_BY_OPTION.items()}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ChanganCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        ChanganSeatHeatSelect(coordinator, entry, driver=True),
        ChanganSeatHeatSelect(coordinator, entry, driver=False),
        ChanganSentrySensitivitySelect(coordinator, entry),
    ])


class ChanganSeatHeatSelect(CoordinatorEntity[ChanganCoordinator], SelectEntity):
    """主驾 / 副驾座椅加热：关 / 1 档 / 2 档 / 3 档。"""

    _attr_has_entity_name = True
    _attr_icon = "mdi:car-seat-heater"
    _attr_options = OPTIONS

    def __init__(
        self, coordinator: ChanganCoordinator, entry: ConfigEntry, driver: bool
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._driver = driver
        self._attr_name = "主驾座椅加热" if driver else "副驾座椅加热"
        self._status_key = (
            SEAT_HEAT_STATUS_DRIVER if driver else SEAT_HEAT_STATUS_PASSENGER
        )
        self._param = (
            CMD_SEAT_HEATING_DRIVER if driver else CMD_SEAT_HEATING_PASSENGER
        )
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        suffix = "seat_driver" if driver else "seat_passenger"
        self._attr_unique_id = f"{entry.entry_id}_{suffix}"

    @property
    def available(self) -> bool:
        """未填控车码时只读展示（不可下发）。"""
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def _level(self) -> int:
        raw = self.coordinator.raw(self._status_key)
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return 0
        return 0 if value == SEAT_OFF_READBACK else value

    @property
    def current_option(self) -> str:
        return _OPTION_BY_VALUE.get(self._level, "关")

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {
            "档位": SEAT_LABELS.get(self._level, "未知"),
            "回读原始值（6=关）": self.coordinator.raw(self._status_key),
        }
        attrs.update(self.coordinator.timeline_text())
        return attrs

    async def async_select_option(self, option: str) -> None:
        value = _VALUE_BY_OPTION.get(option)
        if value is None:
            raise HomeAssistantError(f"不支持的档位：{option}")
        pin = self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN)
        if not pin:
            raise HomeAssistantError("未设置控车码：请在集成「配置」里填写 6 位控车码")
        try:
            resp = await self.coordinator.api.async_control(
                CMD_SEAT_HEATING,
                pin,
                endpoint=self.coordinator.control_endpoint,
                extra={self._param: value},
            )
        except ChanganControlError as err:
            raise HomeAssistantError(f"座椅加热指令失败：{err}") from err
        self.coordinator.note_command(CMD_SEAT_HEATING, resp)
        self.coordinator.async_fast_poll()


class ChanganSentrySensitivitySelect(CoordinatorEntity[ChanganCoordinator], SelectEntity):
    """哨兵灵敏度：低 / 中 / 高（下发 0/1/2，回读同值）。

    setSentryMode 必须同时带开关位，所以这里按「当前是否站岗」把开关位一起带上：
    已开启 → sendSentryModeSwitchSts=1，否则 =0。
    """

    _attr_has_entity_name = True
    _attr_name = "哨兵灵敏度"
    _attr_icon = "mdi:tune-variant"
    _attr_options = [_SENS_OPTION_BY_VALUE[v] for v in SENTRY_SENSITIVITY_VALUES]

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_sentry_sensitivity"

    @property
    def available(self) -> bool:
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def _value(self) -> int:
        try:
            return int(self.coordinator.raw(KEY_SENTRY_SENSITIVITY))
        except (TypeError, ValueError):
            return -1

    @property
    def current_option(self) -> str | None:
        return _SENS_OPTION_BY_VALUE.get(self._value)

    @property
    def extra_state_attributes(self) -> dict:
        raw_state = self.coordinator.raw(KEY_SENTRY_STATE)
        attrs = {
            "灵敏度档位": SENTRY_SENSITIVITY_LABELS.get(self._value, "未知"),
            "哨兵状态": {
                3: "站岗中",
                0: "关闭",
                1: "已设置但未开启",
                4: "已设置但未开启",
                2: "低电量预开启",
            }.get(int(raw_state) if str(raw_state).isdigit() else -1, raw_state),
        }
        attrs.update(self.coordinator.timeline_text())
        return attrs

    async def async_select_option(self, option: str) -> None:
        value = _SENS_VALUE_BY_OPTION.get(option)
        if value is None:
            raise HomeAssistantError(f"不支持的灵敏度：{option}")
        pin = self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN)
        if not pin:
            raise HomeAssistantError("未设置控车码：请在集成「配置」里填写 6 位控车码")
        try:
            on = int(self.coordinator.raw(KEY_SENTRY_STATE)) in SENTRY_ON_VALUES
        except (TypeError, ValueError):
            on = False
        try:
            resp = await self.coordinator.api.async_control(
                CMD_SET_SENTRY_MODE,
                pin,
                endpoint=self.coordinator.control_endpoint,
                extra={
                    SENTRY_PARAM_SWITCH: 1 if on else 0,
                    SENTRY_PARAM_SENSITIVITY: value,
                },
            )
        except ChanganControlError as err:
            raise HomeAssistantError(f"哨兵灵敏度指令失败：{err}") from err
        self.coordinator.note_command(CMD_SET_SENTRY_MODE, resp)
        self.coordinator.async_fast_poll()


# --------------------------------------------------------------- 空调开关
AC_POWER_OPTIONS = ["关闭", "开启"]


class ChanganAcPowerSelect(CoordinatorEntity[ChanganCoordinator], SelectEntity):
    """空调开关：关闭 / 开启（两个字成对，和 App/面板文案一致）。

    为什么不用 climate 的模式：HA 的 climate.set_hvac_mode 服务 schema
    只接受内置 8 种模式，自定义中文模式会被拦成 400；所以开关做成 select。
    """

    _attr_has_entity_name = True
    _attr_name = "空调开关"
    _attr_icon = "mdi:power"
    _attr_options = AC_POWER_OPTIONS

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_ac_power"

    @property
    def available(self) -> bool:
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def _wind(self) -> int:
        try:
            return int(float(self.coordinator.raw(KEY_AC_WIND)))
        except (TypeError, ValueError):
            return 0

    @property
    def _set_temp(self) -> int:
        try:
            return int(float(self.coordinator.raw(KEY_AC_TEMP_SET)))
        except (TypeError, ValueError):
            return 26

    @property
    def current_option(self) -> str:
        return "开启" if self._wind > 0 else "关闭"

    @property
    def extra_state_attributes(self) -> dict:
        attrs = {
            "设定温度": self._set_temp,
            "风速档位": self._wind,
            "状态原始值": self.coordinator.raw(KEY_AC_WIND),
        }
        attrs.update(self.coordinator.timeline_text())
        return attrs

    async def async_select_option(self, option: str) -> None:
        pin = self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN)
        if option == "关闭":
            cmd, extra = CMD_CLOSE_AIR, {"airStatus": "0"}
        else:
            temp = int(max(AC_TEMP_MIN, min(AC_TEMP_MAX, self._set_temp)))
            wind = self._wind if self._wind in AC_FAN_LEVELS else AC_DEFAULT_WIND
            cmd = CMD_OPEN_AIR
            extra = {"airStatus": "1", "temperature": temp, "windLevel": int(wind)}
        try:
            resp = await self.coordinator.api.async_control(
                cmd, pin, endpoint=self.coordinator.control_endpoint, extra=extra)
        except ChanganControlError as err:
            raise HomeAssistantError(f"空调指令失败：{err}") from err
        self.coordinator.note_command(cmd, resp)
        self.coordinator.async_fast_poll()
