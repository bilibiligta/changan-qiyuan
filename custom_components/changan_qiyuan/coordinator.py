"""车况轮询协调器。"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ChanganAuthError, ChanganConnectionError, ChanganQiyuanApi
from .const import (
    CMD_LABELS,
    CONF_REFRESH_TOKEN,
    DEFAULT_CONTROL_ENDPOINT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

_TASK_STATUS_TEXT = {
    "Initialized": "已下发，等待车端",
    "Completed": "车端已执行完成",
    "Timeout": "超时（车可能休眠或信号弱）",
    "RunFailure": "车端拒绝执行",
    "Failed": "执行失败",
    "Cancelled": "已取消",
}


def _human_time(value) -> str | None:
    """把 ISO 或云端 "2026-10-25 14:58:35.123" 统一成 "10-25 14:58:35"。"""
    if not value:
        return None
    text = str(value).strip().replace("T", " ").replace("Z", "")
    text = text.split("+")[0].split(".")[0].strip()
    return text[5:] if len(text) >= 16 else text

# 指令下发后的密集回读：车端（T-BOX 唤醒）真实生效要几秒到十几秒，
# 单靠 1~5 分钟的轮询，面板上的状态会一直停在旧值，看起来像"指令很慢"。
FAST_POLL_SECONDS = 45      # 最多盯 45 秒
FAST_POLL_INTERVAL = 3      # 每 3 秒回读一次
LOCATION_INTERVAL = 120  # 车辆位置刷新间隔（秒）
FAST_POLL_TIMEOUT = 10      # 单次云端请求最多等 10 秒，否则整轮卡死


class ChanganCoordinator(DataUpdateCoordinator[dict]):
    """轮询 /app2/api/car/data，把 201 个原始字段原样存进 self.data。"""

    def __init__(self, hass: HomeAssistant, api: ChanganQiyuanApi,
                 scan_interval: int = DEFAULT_SCAN_INTERVAL,
                 control_endpoint: str = DEFAULT_CONTROL_ENDPOINT,
                 entry=None, persist_token=None) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_interval),
        )
        self.api = api
        self.control_endpoint = control_endpoint
        self.entry = entry
        self._persist_token = persist_token
        self.location: dict = {}
        self._loc_at: float = 0.0
        self.car_detail: dict = {}
        # 短信码授权是否已通过；1004 时由 service 层置位/复位
        self.control_authorized: bool = False
        self.last_control_task: dict | None = None
        self._fast_poll_task: asyncio.Task | None = None
        # 最近一次指令的时间线，供排查"为什么慢"
        self.last_timeline: dict = {}
        # 密集回读的运行状态（会作为实体属性显示出来，方便定位问题）
        self.fast_poll_info: dict = {"state": "空闲（还没下发过指令）"}
        # 「手动改空调」时需要通知速冷/速热开关退出快捷模式（不做恢复）
        self._quick_mode_hooks: list = []
        # 速冷/速热两个开关实例，用于互斥时互相取「进入前状态」
        self.quick_switches: dict = {}

    async def async_try_refresh_token(self) -> bool:
        """access token 过期时用 refresh token 续期；成功则写回配置条目。"""
        rt = self.api.rtoken
        if not rt and self.entry is not None:
            rt = self.entry.data.get(CONF_REFRESH_TOKEN)
        if not rt:
            self.fast_poll_info["last_error"] = "没有 refresh token，无法自动续期"
            return False
        try:
            token = await asyncio.wait_for(
                self.api.refresh_token(rt), timeout=FAST_POLL_TIMEOUT)
        except Exception as err:  # noqa: BLE001
            self.fast_poll_info["last_error"] = f"续期失败：{type(err).__name__}: {err}"
            return False
        if not token:
            self.fast_poll_info["last_error"] = "续期接口没返回新 token"
            return False
        self.api.token = token
        self.api.rtoken = self.api.rtoken or rt
        if self._persist_token is not None:
            try:
                await self._persist_token(token, self.api.rtoken)
            except Exception:  # noqa: BLE001
                _LOGGER.exception("写回新 token 失败")
        _LOGGER.info("长安启源 token 已自动续期")
        return True

    async def _async_update_data(self) -> dict:
        # 主动续期：access token 只有 1 小时，提前 60s 换新，避免整轮失败
        if self.api.needs_refresh:
            if await self.async_try_refresh_token():
                _LOGGER.debug("已主动续期 access token")
        try:
            if not self.car_detail:
                self.car_detail = await self.api.car_detail()
            data = await self.api.car_data()
        except ChanganAuthError as err:
            # 先尝试自动续期（access token 只有 1 小时），失败才让用户重新登录
            if await self.async_try_refresh_token():
                try:
                    data = await self.api.car_data()
                except ChanganAuthError as err2:
                    raise ConfigEntryAuthFailed(
                        f"认证失败，需要重新登录: {err2}") from err2
            else:
                # 触发 HA 的重新认证流程（config_flow.async_step_reauth）
                raise ConfigEntryAuthFailed(f"认证失败，需要重新登录: {err}") from err
        except ChanganConnectionError as err:
            raise UpdateFailed(f"网络异常: {err}") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"未知错误: {err}") from err

        if not data:
            raise UpdateFailed("车况为空（车辆可能离线，或 token 已过期）")

        data["_fetched_at"] = datetime.now().isoformat(timespec="seconds")
        await self._async_update_location()
        return data

    async def _async_update_location(self) -> None:
        """车辆位置：云端单独一个接口，节流到每 LOCATION_INTERVAL 秒一次。"""
        now = time.monotonic()
        if now - self._loc_at < LOCATION_INTERVAL:
            return
        self._loc_at = now
        try:
            loc = await asyncio.wait_for(self.api.location(), timeout=FAST_POLL_TIMEOUT)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("读取车辆位置失败：%s", err)
            return
        if loc:
            self.location = loc
            _LOGGER.debug("车辆位置已更新：%s", loc.get("address"))

    # --- 取值工具 ---
    def raw(self, key: str, default=None):
        """支持 'air.temperature' 这种点号路径。"""
        if self.data is None:
            return default
        cur: object = self.data
        for part in key.split("."):
            if not isinstance(cur, dict):
                return default
            cur = cur.get(part)
        return default if cur is None else cur

    # --- 速冷/速热快捷模式的联动钩子 ---
    def register_quick_mode_hook(self, callback):
        """速冷/速热开关注册回调：用户手动改空调时通知它们退出快捷模式。"""
        self._quick_mode_hooks.append(callback)

        def _remove() -> None:
            if callback in self._quick_mode_hooks:
                self._quick_mode_hooks.remove(callback)

        return _remove

    def notify_manual_ac_change(self) -> None:
        """空调被手动操作（改温度/风量/开关）→ 让快捷模式退场，但不做恢复。

        用户的语义是「点关才恢复」；如果他自己去调温度，就说明不想再要这个快捷模式了，
        此时若还去恢复旧值，反而会覆盖他的手动设置。
        """
        for callback in list(self._quick_mode_hooks):
            try:
                callback()
            except Exception:  # noqa: BLE001
                _LOGGER.exception("通知速冷/速热开关退出失败")

    # --- 指令后的快速跟进 ---
    def note_command(self, cmd: str, resp: dict | None) -> None:
        """记下本次指令的 taskId 与发出时间。"""
        task_id = None
        if isinstance(resp, dict):
            payload = resp.get("data")
            if isinstance(payload, dict):
                task_id = payload.get("taskId")
        now = datetime.now()
        self.last_control_task = {
            "cmd": cmd,
            "taskId": task_id,
            "sent_at": now.isoformat(timespec="seconds"),
        }
        self.last_timeline = {"cmd": cmd, "taskId": task_id,
                              "sent_at": now.isoformat(timespec="seconds")}
        if task_id:
            self.last_timeline["sent_epoch"] = time.time()

    def timeline_text(self) -> dict:
        """把 last_timeline 翻译成中文属性（含下发/回读/车端时间）。"""
        tl = dict(self.last_timeline or {})
        info = dict(self.fast_poll_info or {})
        attrs: dict = {}
        if not tl:
            attrs["上次指令"] = "无"
        else:
            cmd = tl.get("cmd") or ""
            label = CMD_LABELS.get(cmd, cmd)
            attrs["上次指令"] = (
                f"{label}（{cmd}）" if cmd and cmd != label else (label or "未知")
            )
            sent = _human_time(tl.get("sent_at"))
            if sent:
                attrs["指令下发时间"] = sent
            first = _human_time(tl.get("first_readback_at"))
            if first:
                delta = tl.get("sent_to_readback_s")
                suffix = f"（下发后 {delta} 秒）" if delta else ""
                attrs["HA 首次看到变化"] = f"{first}{suffix}"
            else:
                attrs["HA 首次看到变化"] = "尚未读到"
            tbox_send = _human_time(tl.get("tbox_send"))
            if tbox_send:
                attrs["车端开始执行"] = tbox_send
            tbox_finish = _human_time(tl.get("tbox_finish"))
            if tbox_finish:
                attrs["车端执行完成"] = tbox_finish
            status = tl.get("task_status")
            if status:
                attrs["车端任务状态"] = _TASK_STATUS_TEXT.get(status, status)
        # 密集回读自检信息（排查“为什么没变快”）
        attrs["密集回读"] = info.get("state", "未知")
        if info.get("polls") is not None:
            attrs["回读次数"] = info.get("polls")
        if info.get("last_error"):
            attrs["回读错误"] = info.get("last_error")
        return attrs

    def async_fast_poll(self) -> None:
        """指令下发后立刻进入密集回读（3 秒一次，最多 45 秒）。"""
        if self._fast_poll_task is not None and not self._fast_poll_task.done():
            self.fast_poll_info = {
                "state": "进行中（沿用上一轮）",
                "polls": self.fast_poll_info.get("polls"),
            }
            return
        self.fast_poll_info = {"state": "已启动", "polls": 0}
        coro = self._async_fast_poll()
        try:
            self._fast_poll_task = self.hass.async_create_background_task(
                coro, f"{DOMAIN}_fast_poll"
            )
        except (AttributeError, TypeError):
            self._fast_poll_task = self.hass.async_create_task(coro)

    async def _async_fast_poll(
        self,
        seconds: int = FAST_POLL_SECONDS,
        interval: float = FAST_POLL_INTERVAL,
    ) -> None:
        start = time.monotonic()
        deadline = start + seconds
        self.fast_poll_info = {"state": "运行中", "polls": 0, "errors": 0}
        stop_reason = "到点结束"
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(interval)
                try:
                    data = await asyncio.wait_for(
                        self.api.car_data(), timeout=FAST_POLL_TIMEOUT)
                except asyncio.TimeoutError:
                    self.fast_poll_info["errors"] = self.fast_poll_info.get("errors", 0) + 1
                    self.fast_poll_info["last_error"] = (
                        f"car_data 超时（> {FAST_POLL_TIMEOUT}s）")
                    stop_reason = "回读超时"
                    break
                except ChanganAuthError as err:
                    stop_reason = f"鉴权失败：{err}"
                    break
                except ChanganConnectionError as err:
                    stop_reason = f"网络异常：{err}"
                    break
                except Exception as err:  # noqa: BLE001
                    self.fast_poll_info["errors"] += 1
                    self.fast_poll_info["last_error"] = f"{type(err).__name__}: {err}"
                    stop_reason = f"回读异常：{type(err).__name__}"
                    break
                if not data:
                    self.fast_poll_info["errors"] += 1
                    self.fast_poll_info["last_error"] = "car_data 返回空"
                    continue
                self.fast_poll_info["polls"] = self.fast_poll_info.get("polls", 0) + 1
                data["_fetched_at"] = datetime.now().isoformat(timespec="seconds")
                data["_fast_poll"] = True
                # 先更新时间线，再通知实体，否则实体拿到的是更新前的快照
                if "first_readback_at" not in self.last_timeline:
                    self.last_timeline["first_readback_at"] = datetime.now().isoformat(
                        timespec="seconds")
                    self.last_timeline["sent_to_readback_s"] = round(
                        time.monotonic() - start, 1)
                self.async_set_updated_data(data)

                # 顺便盯车端任务状态，把"车端真实耗时"也记下来
                task_id = (self.last_control_task or {}).get("taskId")
                if task_id and not self.last_timeline.get("task_finished"):
                    try:
                        info = await asyncio.wait_for(
                            self.api.query_task(task_id), timeout=FAST_POLL_TIMEOUT)
                    except asyncio.TimeoutError:
                        info = None
                        self.fast_poll_info["last_error"] = (
                            f"query_task 超时（> {FAST_POLL_TIMEOUT}s）")
                    except Exception:  # noqa: BLE001
                        info = None
                    if isinstance(info, dict) and info:
                        status = info.get("handleStatus")
                        self.last_timeline["task_status"] = status
                        self.last_timeline["tbox_cmd"] = info.get("tboxCmd")
                        self.last_timeline["tbox_send"] = info.get("cmdSendTime")
                        self.last_timeline["tbox_finish"] = info.get("cmdFinishTime")
                        self.async_set_updated_data(data)
                        if status in ("Completed", "Timeout", "Failed",
                                      "RunFailure", "Cancelled") or info.get(
                                "cmdFinishTime"):
                            self.last_timeline["task_finished"] = True
                            stop_reason = f"车端任务已结束（{status}）"
                            break
        except Exception as err:  # noqa: BLE001
            stop_reason = f"未预料的异常：{type(err).__name__}: {err}"
            _LOGGER.exception("密集回读异常")
        finally:
            self.fast_poll_info["state"] = f"已结束（{stop_reason}）"
            self._fast_poll_task = None
