"""空调实体（2026-10-24 启源 A07 真车实测过，参数全部为实测值）。

实测结论（很重要，和直觉不一样）：
    · 开空调 = OpenAir  {airStatus:"1", temperature:"18".."32", windLevel:"1".."5"}
    · 关空调 = CloseAir {airStatus:"0"}
    · App 里没有独立的「速冷/速热」指令；
        速冷 = 18℃ + 风量最大，速热 = 32℃ + 风量最大（图标由车机自己点亮）
    · **airStatus 不是开关量**：关掉空调后车端回读 airStatus 仍是 1。
      判断"空调开没开"只能看 frontReqWindLevel（风量档位）是否 > 0。

依赖：集成选项里已保存控车码，且已做过短信码授权（/app2/api/control/auth）。
"""
from __future__ import annotations

import logging

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
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
    CONF_PIN,
    DOMAIN,
    KEY_AC_TEMP_SET,
    KEY_AC_WIND,
    KEY_CABIN_TEMP,
)
from .coordinator import ChanganCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ChanganCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ChanganClimate(coordinator, entry)])


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


_FEATURES = (
    ClimateEntityFeature.TARGET_TEMPERATURE | ClimateEntityFeature.FAN_MODE
)
# HA 用 supported_features 判断是否允许调用 climate.turn_on/turn_off，
# 少了这两个位，服务层会直接抛 ServiceNotSupported（前端表现为 500）。
for _flag in ("TURN_ON", "TURN_OFF"):
    _member = getattr(ClimateEntityFeature, _flag, None)
    if _member is not None:
        _FEATURES |= _member


class ChanganClimate(CoordinatorEntity[ChanganCoordinator], ClimateEntity):
    """车机空调：开/关 + 18~32℃ + 1~5 档风量（冷暖由温度决定，无模式参数）。"""

    _attr_has_entity_name = True
    _attr_name = "空调"
    _attr_icon = "mdi:air-conditioner"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = AC_TEMP_MIN
    _attr_max_temp = AC_TEMP_MAX
    _attr_target_temperature_step = 1
    _attr_hvac_modes = [HVACMode.OFF, HVACMode.HEAT_COOL]
    _attr_fan_modes = [str(level) for level in AC_FAN_LEVELS]
    _attr_supported_features = _FEATURES

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_climate"

    # ---------------------------------------------------------------- 回读
    @property
    def available(self) -> bool:
        """未填控车码时只读展示（不可下发）。"""
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def _wind(self) -> int:
        return int(_num(self.coordinator.raw(KEY_AC_WIND), 0) or 0)

    @property
    def _set_temp(self) -> float:
        return _num(self.coordinator.raw(KEY_AC_TEMP_SET), 24.0) or 24.0

    @property
    def current_temperature(self):
        return _num(self.coordinator.raw(KEY_CABIN_TEMP))

    @property
    def extra_state_attributes(self) -> dict:
        wind = self._wind
        attrs = {
            "空调状态": "开" if wind > 0 else "关",
            "设定温度": f"{self._set_temp:g}℃",
            "风速档位": f"{wind} 档（1~5 档可调）" if wind > 0 else "关",
            "车内温度": (
                f"{self.current_temperature:g}℃"
                if self.current_temperature is not None
                else "未知"
            ),
        }
        attrs.update(self.coordinator.timeline_text())
        return attrs

    @property
    def target_temperature(self) -> float:
        return self._set_temp

    @property
    def fan_mode(self) -> str:
        wind = self._wind
        return str(wind) if wind in AC_FAN_LEVELS else str(AC_FAN_MAX)

    @property
    def hvac_mode(self) -> HVACMode:
        # 注意：这里不能用 airStatus 判断，关空调后它仍是 1。
        # 本车空调只有「开/关」两态：制冷还是制热完全由设定温度决定（<26 冷、>=26 热），
        # 指令里也没有模式参数，所以统一用 HEAT_COOL。
        # 注意：HA 的 climate.set_hvac_mode schema 只接受内置 8 种模式，
        # 自定义字符串（如「开」）会被拦成 400，所以模式名只能用它给的翻译。
        # 想要「开启 / 关闭」成对的中文文案，用同设备上的 select「空调开关」。
        if self._wind <= 0:
            return HVACMode.OFF
        return HVACMode.HEAT_COOL

    # ---------------------------------------------------------------- 控制
    async def _send(self, cmd: str, extra: dict) -> None:
        pin = self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN)
        try:
            resp = await self.coordinator.api.async_control(
                cmd,
                pin,
                endpoint=self.coordinator.control_endpoint,
                extra=extra,
            )
        except ChanganControlError as err:
            raise HomeAssistantError(f"空调指令失败：{err}") from err
        self.coordinator.note_command(cmd, resp)
        self.coordinator.async_fast_poll()

    async def async_set_hvac_mode(self, hvac_mode) -> None:
        # 除 off 之外的任何值（「开」/heat_cool/cool/heat/auto）都按开机处理
        if hvac_mode == HVACMode.OFF:
            await self._send(CMD_CLOSE_AIR, {"airStatus": "0"})
            self.coordinator.notify_manual_ac_change()
            return
        # 开：不再硬套 25/28℃，直接把当前设定温度 + 风量发下去
        # （速冷/速热开关会自己写 18/32 + 5 档，两者互不干扰）
        wind = self._wind if self._wind in AC_FAN_LEVELS else AC_DEFAULT_WIND
        await self._send(
            CMD_OPEN_AIR,
            {"airStatus": "1", "temperature": int(self._set_temp), "windLevel": int(wind)},
        )
        self.coordinator.notify_manual_ac_change()

    async def async_set_temperature(self, **kwargs) -> None:
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        temp = int(max(AC_TEMP_MIN, min(AC_TEMP_MAX, temp)))
        wind = self._wind if self._wind in AC_FAN_LEVELS else AC_DEFAULT_WIND
        await self._send(
            CMD_OPEN_AIR,
            {"airStatus": "1", "temperature": temp, "windLevel": int(wind)},
        )
        self.coordinator.notify_manual_ac_change()

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        wind = int(_num(fan_mode, AC_DEFAULT_WIND) or AC_DEFAULT_WIND)
        wind = max(AC_FAN_LEVELS[0], min(AC_FAN_MAX, wind))
        await self._send(
            CMD_OPEN_AIR,
            {"airStatus": "1", "temperature": int(self._set_temp), "windLevel": wind},
        )
        self.coordinator.notify_manual_ac_change()

    async def async_turn_on(self) -> None:
        try:
            await self.async_set_hvac_mode(HVACMode.HEAT_COOL)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("空调开机（turn_on）失败")
            raise

    async def async_turn_off(self) -> None:
        try:
            await self.async_set_hvac_mode(HVACMode.OFF)
        except Exception:  # noqa: BLE001
            _LOGGER.exception("空调关机（turn_off）失败")
            raise
