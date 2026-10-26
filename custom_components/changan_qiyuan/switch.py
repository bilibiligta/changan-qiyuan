"""开关类实体：哨兵模式 + 速冷/速热快捷模式。

哨兵模式（2026-10-25 启源 A07 真车实测通过）
-------------------------------------------
参数名来自 App 运行时下载的明文微应用 `micro.cn.com.qiyuan.sentinelModeV2`：
    navigator.packaging.sendCmd({cmd: "setSentryMode", params: {
        sendSentryModeSwitchSts: 1|0, sendSentryModeSensitivity: 1|2}})
实测：
    {"cmd": "setSentryMode", "sendSentryModeSwitchSts": "1"}  -> sentryModeSwitchSts 0→3（站岗中）
    {"cmd": "setSentryMode", "sendSentryModeSwitchSts": "0"}  -> 3→0（关闭）
回读有 10~40s 滞后：刚下发会先读到 1（已设置未开启），过一会才变 3。

速冷 / 速热（2026-10-25 与用户逐条确认过的语义）
----------------------------------------------
点开 = 记住当前空调状态（开/关 + 设定温度 + 风量档位），
       然后下发 OpenAir {temperature: 18|32, windLevel: 5}
点关 = 恢复进入前的状态：
       · 原本开着 → OpenAir {记忆温度, 记忆风量}，保持开启
       · 原本关着 → CloseAir（关机状态下温度无意义，关掉即可）
其它规则：
  · 两个开关互斥：开速冷会让速热退场，并**继承速热记住的原始状态**（不做恢复）
  · 记忆值写进 .storage，HA 重启 / 集成重载都不丢
  · 用户在空调卡片上手动改温度、风量或开关 → 快捷开关自动退场，且**不做恢复**
    （否则会覆盖用户的手动设置）
  · 车端 15 分钟自动关空调；回读到风量为 0 → 快捷开关自动回到 off
"""
from __future__ import annotations

import logging
import time

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ChanganControlError
from .const import (
    KEY_TRUNK,
    KEY_DOOR_LOCK,
    CMD_CLOSE_TRUNK,
    CMD_OPEN_TRUNK,
    CMD_UNLOCK,
    CMD_LOCK,
    AC_DEFAULT_WIND,
    AC_FAN_FAST,
    AC_FAN_LEVELS,
    CMD_CLOSE_AIR,
    CMD_CLOSE_WINDOW,
    CMD_OPEN_AIR,
    CMD_SET_SENTRY_MODE,
    CMD_WINDOW_SLIT,
    CONF_PIN,
    DOMAIN,
    KEY_AC_TEMP_SET,
    KEY_AC_WIND,
    KEY_SENTRY_SENSITIVITY,
    KEY_SENTRY_STATE,
    KEY_WINDOW,
    QUICK_COOL,
    QUICK_HEAT,
    QUICK_MODES,
    SENTRY_ON_VALUES,
    SENTRY_PENDING_VALUES,
    SENTRY_PARAM_SWITCH,
)
from .coordinator import ChanganCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ChanganCoordinator = hass.data[DOMAIN][entry.entry_id]
    store = _QuickModeStore(hass, entry.entry_id)
    await store.async_load()
    async_add_entities(
        [
            ChanganSentrySwitch(coordinator, entry),
            ChanganAcPowerSwitch(coordinator, entry),
            ChanganWindowSeamsSwitch(coordinator, entry),
            ChanganQuickModeSwitch(coordinator, entry, store, QUICK_COOL),
            ChanganQuickModeSwitch(coordinator, entry, store, QUICK_HEAT),
        ]
    )


def _num(value, default=None):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- 哨兵
class ChanganSentrySwitch(CoordinatorEntity[ChanganCoordinator], SwitchEntity):
    """哨兵模式：开=站岗，关=解除；灵敏度作为属性展示。"""

    _attr_has_entity_name = True
    _attr_name = "哨兵模式"
    _attr_icon = "mdi:shield-car"

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_sentry"

    @property
    def available(self) -> bool:
        """未填控车码时只读展示（不可下发）。"""
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def _state(self) -> int:
        try:
            return int(self.coordinator.raw(KEY_SENTRY_STATE))
        except (TypeError, ValueError):
            return -1

    @property
    def is_on(self) -> bool:
        # 1/3/4 都算“哨兵开着”：3=站岗中，1/4=已布防但车端还没确认站岗
        # （下发后 10~40 秒、或车在睡眠导致云端快照没刷新时会停在 1）。
        return self._state in SENTRY_ON_VALUES

    @property
    def extra_state_attributes(self) -> dict:
        raw_state = self._state
        sens = self.coordinator.raw(KEY_SENTRY_SENSITIVITY)
        attrs = {
            "哨兵状态": {
                3: "站岗中",
                0: "关闭",
                1: "已开启（待站岗确认）",
                4: "已开启（待站岗确认）",
                2: "低电量预开启",
            }.get(raw_state, "未知"),
            "灵敏度": {0: "低", 1: "中", 2: "高"}.get(
                int(sens) if isinstance(sens, (int, str)) and str(sens).isdigit() else -1,
                sens,
            ),
            "状态原始值": raw_state,
            "是否站岗": raw_state == 3,
            "已布防待站岗": raw_state in SENTRY_PENDING_VALUES,
        }
        attrs.update(self.coordinator.timeline_text())
        return attrs

    async def _set(self, value: int) -> None:
        pin = self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN)
        try:
            resp = await self.coordinator.api.async_control(
                CMD_SET_SENTRY_MODE,
                pin,
                endpoint=self.coordinator.control_endpoint,
                extra={SENTRY_PARAM_SWITCH: value},
            )
        except ChanganControlError as err:
            raise HomeAssistantError(f"哨兵指令失败：{err}") from err
        self.coordinator.note_command(CMD_SET_SENTRY_MODE, resp)
        self.coordinator.async_fast_poll()

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(1)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(0)


# --------------------------------------------------------------- 车窗留缝
class ChanganWindowSeamsSwitch(CoordinatorEntity[ChanganCoordinator], SwitchEntity):
    """车窗留缝：开=四窗留缝，关=全部关闭。

    原先「车窗留缝」「关闭车窗」是两个按钮，现在合成一个开关；
    状态取自车况 `window` 字段（0=关闭，非 0=留缝/打开）。
    """

    _attr_has_entity_name = True
    _attr_name = "车窗留缝"
    _attr_icon = "mdi:car-windshield-outline"

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_window_seams"

    @property
    def available(self) -> bool:
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def _raw(self) -> int | None:
        try:
            return int(self.coordinator.raw(KEY_WINDOW))
        except (TypeError, ValueError):
            return None

    @property
    def is_on(self) -> bool:
        raw = self._raw
        return bool(raw) if raw is not None else False

    @property
    def extra_state_attributes(self) -> dict:
        raw = self._raw
        attrs = {
            "车窗状态": {0: "已关闭"}.get(raw, "留缝/打开" if raw is not None else "未知"),
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
            raise HomeAssistantError(f"车窗指令失败：{err}") from err
        self.coordinator.note_command(cmd, resp)
        self.coordinator.async_fast_poll()

    async def async_turn_on(self, **kwargs) -> None:
        await self._send(CMD_WINDOW_SLIT)

    async def async_turn_off(self, **kwargs) -> None:
        await self._send(CMD_CLOSE_WINDOW)


# --------------------------------------------------------------- 速冷/速热记忆存储
class _QuickModeStore:
    """把「进入快捷模式前的空调状态」持久化到 HA 的 .storage。"""

    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store = Store(hass, 1, f"{DOMAIN}.quick_mode.{entry_id}")
        self._data: dict = {}

    async def async_load(self) -> None:
        try:
            self._data = await self._store.async_load() or {}
        except Exception:  # noqa: BLE001
            _LOGGER.exception("读取速冷/速热记忆失败，按空记忆处理")
            self._data = {}

    def get(self, mode: str) -> dict:
        return dict(self._data.get(mode) or {})

    def set(self, mode: str, value: dict) -> None:
        self._data[mode] = value
        self._store.async_delay_save(lambda: self._data, 5)


# ------------------------------------------------------------------ 速冷/速热开关
class ChanganQuickModeSwitch(CoordinatorEntity[ChanganCoordinator], SwitchEntity):
    """速冷 / 速热：开=进入快捷模式，关=恢复进入前的空调状态。"""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: ChanganCoordinator,
        entry: ConfigEntry,
        store: _QuickModeStore,
        mode: str,
    ) -> None:
        super().__init__(coordinator)
        self.entry = entry
        self._store = store
        self._mode = mode
        self._cfg = QUICK_MODES[mode]
        self._attr_name = self._cfg["name"]
        self._attr_icon = self._cfg["icon"]
        self._attr_unique_id = f"{entry.entry_id}_quick_{mode}"
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        record = store.get(mode)
        self._active = bool(record.get("active"))
        self._baseline: dict = dict(record.get("baseline") or {})
        self._note = ""
        self._since = 0.0

    # ------------------------------------------------------------ 生命周期
    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.coordinator.quick_switches[self._mode] = self
        self.async_on_remove(
            self.coordinator.register_quick_mode_hook(self._on_manual_ac_change)
        )
        # 上次是激活态，但车端已经是关空调（15 分钟自动关或被人关了）→ 直接回落
        if self._active and self._wind <= 0:
            self._active = False
            self._save()

    def _handle_coordinator_update(self) -> None:
        super()._handle_coordinator_update()
        # 车端把空调关了（含 15 分钟自动关）：快捷模式自动退场
        if self._active and self._wind <= 0 and time.monotonic() - self._since > 20:
            self.deactivate("车端已关闭空调，快捷模式自动退出")

    # ------------------------------------------------------------ 回读工具
    @property
    def _wind(self) -> int:
        return int(_num(self.coordinator.raw(KEY_AC_WIND), 0) or 0)

    @property
    def _set_temp(self) -> float:
        return _num(self.coordinator.raw(KEY_AC_TEMP_SET), 24.0) or 24.0

    @property
    def available(self) -> bool:
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def is_on(self) -> bool:
        return self._active

    @property
    def extra_state_attributes(self) -> dict:
        base = self._baseline or {}
        if base:
            before = "开" if base.get("ac_on") else "关"
            setting = f"{base.get('temp')}℃ / {base.get('wind')} 档"
        else:
            before, setting = "未知", "无"
        attrs = {
            "快捷模式": f"{self._cfg['name']}（{self._cfg['state_desc']}）",
            "当前状态": "开启中" if self._active else "关闭",
            "进入前空调": before,
            "进入前设定": setting,
            "说明": self._note
            or "点开=记住当前状态并进入快捷模式；点关=恢复进入前状态",
        }
        attrs.update(self.coordinator.timeline_text())
        return attrs

    # ------------------------------------------------------------ 记忆与状态
    def _capture_baseline(self) -> dict:
        wind = self._wind
        return {
            "ac_on": wind > 0,
            "temp": int(self._set_temp),
            "wind": wind if wind in AC_FAN_LEVELS else AC_DEFAULT_WIND,
        }

    def _save(self) -> None:
        self._store.set(
            self._mode,
            {"active": self._active, "baseline": self._baseline},
        )

    def _sibling(self) -> ChanganQuickModeSwitch | None:
        other = QUICK_HEAT if self._mode == QUICK_COOL else QUICK_COOL
        return self.coordinator.quick_switches.get(other)

    def deactivate(self, note: str = "") -> None:
        """退出快捷模式（不恢复），用于互斥/手动干预/车端已关空调。"""
        if not self._active:
            return
        self._active = False
        self._note = note
        self._save()
        self.async_write_ha_state()

    def _on_manual_ac_change(self) -> None:
        self.deactivate("检测到手动改了空调，快捷模式已退出（不做恢复）")

    # ------------------------------------------------------------ 下发
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
            raise HomeAssistantError(f"{self._cfg['name']}指令失败：{err}") from err
        self.coordinator.note_command(cmd, resp)
        self.coordinator.async_fast_poll()

    async def _send_open(self, temperature: int, wind: int) -> None:
        await self._send(
            CMD_OPEN_AIR,
            {
                "airStatus": "1",
                "temperature": int(temperature),
                "windLevel": int(wind),
            },
        )

    async def _send_close(self) -> None:
        await self._send(CMD_CLOSE_AIR, {"airStatus": "0"})

    # ------------------------------------------------------------ 开关动作
    async def async_turn_on(self, **kwargs) -> None:
        sibling = self._sibling()
        if sibling is not None and sibling.is_on:
            # 互斥：继承对方记住的「原始状态」，然后让它静默退场
            baseline = dict(sibling._baseline) or self._capture_baseline()
            sibling.deactivate()
        else:
            baseline = self._capture_baseline()

        self._baseline = baseline
        self._active = True
        self._note = ""
        self._since = time.monotonic()
        self._save()
        self.async_write_ha_state()
        try:
            await self._send_open(int(self._cfg["temp"]), AC_FAN_FAST)
        except Exception:
            # 下发失败就退回原状，别让界面显示"已开启"
            self._active = False
            self._note = "下发失败，已退出快捷模式"
            self._save()
            self.async_write_ha_state()
            raise

    async def async_turn_off(self, **kwargs) -> None:
        if not self._active:
            return
        baseline = dict(self._baseline)
        self._active = False
        self._note = ""
        self._save()
        self.async_write_ha_state()
        if baseline.get("ac_on"):
            await self._send_open(
                int(baseline.get("temp") or 24),
                int(baseline.get("wind") or AC_DEFAULT_WIND),
            )
        else:
            await self._send_close()


# ============================================================ 空调开关（switch）
class ChanganAcPowerSwitch(CoordinatorEntity[ChanganCoordinator], SwitchEntity):
    """空调开关：开=OpenAir，关=CloseAir。

    判定用风量 `frontReqWindLevel > 0`（云端 `airStatus` 恒为 1，不可用）。
    开启时沿用当前设定温度与上次风量，缺省 24℃ / 3 档。
    """

    _attr_has_entity_name = True
    _attr_name = "空调开关"
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, coordinator: ChanganCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self.entry = entry
        detail = coordinator.car_detail.get("data") or {}
        car_id = detail.get("carId") or entry.data.get("car_id") or entry.entry_id
        self._attr_device_info = {"identifiers": {(DOMAIN, car_id)}}
        self._attr_unique_id = f"{entry.entry_id}_ac_power"
        self._last_wind = AC_DEFAULT_WIND

    @property
    def available(self) -> bool:
        return bool(self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN))

    @property
    def _wind(self) -> float:
        try:
            return float(self.coordinator.raw(KEY_AC_WIND))
        except (TypeError, ValueError):
            return 0.0

    @property
    def is_on(self) -> bool:
        return self._wind > 0

    @property
    def extra_state_attributes(self) -> dict:
        wind = self._wind
        if wind > 0:
            self._last_wind = int(wind)
        attrs = {
            "空调状态": "已开启" if wind > 0 else "已关闭",
            "风量档位": int(wind) if wind > 0 else "关",
            "设定温度": self.coordinator.raw(KEY_AC_TEMP_SET),
            "状态原始值": self.coordinator.raw(KEY_AC_WIND),
        }
        attrs.update(self.coordinator.timeline_text())
        return attrs

    async def _send(self, cmd: str, extra: dict | None = None) -> None:
        pin = self.entry.options.get(CONF_PIN) or self.entry.data.get(CONF_PIN)
        try:
            resp = await self.coordinator.api.async_control(
                cmd, pin, endpoint=self.coordinator.control_endpoint, extra=extra
            )
        except ChanganControlError as err:
            raise HomeAssistantError(f"空调指令失败：{err}") from err
        self.coordinator.note_command(cmd, resp)
        self.coordinator.async_fast_poll()

    async def async_turn_on(self, **kwargs) -> None:
        if self.is_on:
            return
        temp = self.coordinator.raw(KEY_AC_TEMP_SET) or 24
        try:
            temp = int(float(temp))
        except (TypeError, ValueError):
            temp = 24
        await self._send(
            CMD_OPEN_AIR,
            {"airStatus": 1, "temperature": temp, "windLevel": self._last_wind or AC_DEFAULT_WIND},
        )

    async def async_turn_off(self, **kwargs) -> None:
        await self._send(CMD_CLOSE_AIR, {"airStatus": 0})
