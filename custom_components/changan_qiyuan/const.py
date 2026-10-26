"""长安启源 (Changan Qiyuan) 集成常量定义.

本文件里的「指令码表」来自 APK 内 assets/control_config.json（明文，无需脱壳），
已在 启源 A07（seriesCode=C236ICA1 / confCode=SC7007AABBEV.CNH4001+00004）上核对过
车辆功能开关，具体见同目录 README.md 的「指令码 × 车辆开关」交叉表。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    DEGREE,
    PERCENTAGE,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)

DOMAIN = "changan_qiyuan"

# ------------------------------------------------------------------ 轮询
DEFAULT_SCAN_INTERVAL = 1          # 分钟（指令下发后另有 3 秒级快速回读）
MIN_SCAN_INTERVAL = 1              # 分钟
# 车端字段异常值上界：超过即视为 T-BOX 上报无效（例如 totalOdometer 饱和到 2^24 × 0.1km = 1677721.6km），
# 直接显示会严重误导，实体置为未知。
SANITY_MAX: dict[str, float] = {"totalOdometer": 500000.0}

CONF_SCAN_INTERVAL = "scan_interval"

CONF_TOKEN = "token"
CONF_REFRESH_TOKEN = "refresh_token"   # access token 只有 1 小时，靠它自动续期
CONF_CAR_ID = "car_id"
CONF_PIN = "pin"                   # 控车码（安全 PIN），默认不落盘
CONF_STORE_PIN = "store_pin"
CONF_ENABLE_CONTROL = "enable_control"
CONF_CONTROL_ENDPOINT = "control_endpoint"

# ------------------------------------------------------------------ 网关
BASE_URL = "https://m.iov.changan.com.cn"
VCS_APP_ID = "qiyuan"              # 启源 App 的 appId，长安/深蓝/阿维塔各不相同
USER_AGENT = (
    "Mozilla/5.0 (Linux; Android 14; M2102K1C Build/UKQ1.240624.001; wv) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/120.0.0.0 "
    "Mobile Safari/537.36"
)

# 接口路径（全部实测过，详见 README「跑通的接口」）
PATH_SEND_SMS = "/api/sms/sendAuthcode"        # POST {phone, type:1}
PATH_LOGIN = "/api/user/mobileLogin"           # POST {phone, verifyCode} -> token
PATH_CAR_DETAIL = "/app2/api/v3/car/detail"    # GET  ?token=&type=0  -> carId + 功能开关
PATH_CAR_DATA = "/app2/api/car/data"           # POST ?token=&keys=*&carId=  -> 201 字段
PATH_LOCATION = "/app2/api/car/location"       # GET  ?token=&carId=
PATH_SECURITY_KEY = "/app2/api/security/key"   # GET  ?token= -> RSA 公钥(base64 DER)
PATH_CHECK_PIN = "/app2/api/control/check-pin" # POST {carId, pin=RSA(PIN)}
PATH_PIN_CODE = "/app2/api/control/createPinVerifyCode"  # POST {carId} -> "<19位id>::<32位hex>"
PATH_RENEW_TOKEN = "/appserver/api/user/renewalToken"
# 指令执行结果查询（2026-10-24 实测）：POST /app2/api/car/getControlInfo
#   body {carId, id=<execute 返回的 taskId>}
#   -> {tboxCmd, handleStatus, handleStatusDesc, cmdSendTime, cmdFinishTime, taskId}
# 注意参数名是 id 不是 taskId；getControlInfoByTaskId 只在旧网关 /api/car/* 上存在。
PATH_CONTROL_INFO = "/app2/api/car/getControlInfo"

# 车端指令串行执行：一条指令约占用 60s，期间再发会 code:999。
BUSY_RETRY = 8          # 999 时最多重试次数
BUSY_WAIT = 10          # 每次重试间隔（秒）

# ---- 控车授权链（2026-10 实测跑通，见 README「控车链路 v2」）----
# 1004「存在账户安全，请输入校验码」的正解：短信码换授权，授权是会话级的，可连发多条指令。
PATH_CONTROL_AUTH = "/app2/api/control/auth"     # POST {carId, authCode} -> code:0 即授权成功
PATH_CONTROL_SMS = "/app2/api/code/cac/sms"      # POST {mobile, usage:"carControl"} -> 下发短信码
SMS_USAGE_CONTROL = "carControl"
# execute 的必填组合（缺一不可）：
#   carId + cmd + pin=RSA(PIN) + pinVerifyCode=<createPinVerifyCode 的返回值，一次一用>
PATH_REFRESH_TOKEN = "/cac/api/v1/oauth2/refresh_token"   # ?client_id=..&refresh_token=..
OAUTH_CLIENT_ID = "bce34639784c4b159e7ebeda9a5d13bb"

# 控车端点。v3 比 v1 多一个必填参数 `s`（未攻破），传错报 -1000 密钥已过期
CONTROL_ENDPOINTS = {
    "v1": "/app2/api/control/execute",
    "v2": "/app2/api/v2/control/execute",
    "v3": "/app2/api/v3/control/execute",
}
DEFAULT_CONTROL_ENDPOINT = "v1"

# ------------------------------------------------------------------ 指令码
# 来源：APK assets/control_config.json（38 条，去重后 24 个唯一 cmd）
# 注意：这些是 UI 里出现的指令，不等于本车全部支持——是否支持以 car/detail 的功能开关为准。
CMD_UNLOCK = "UnLockDoor"
CMD_LOCK = "LockDoor"
CMD_OPEN_TRUNK = "OpenTrunk"
CMD_CLOSE_TRUNK = "CloseTrunk"
CMD_OPEN_WINDOW = "OpenWindow"          # 车窗留缝
CMD_CLOSE_WINDOW = "CloseWindow"
CMD_WINDOW_SLIT = "WindowSlit"          # 车窗留缝（带 configId:WindowSeamsBtn）
CMD_OPEN_DORMER = "OpenDormer"          # 天窗起翘
CMD_CLOSE_DORMER = "CloseDormer"
CMD_SKY_WINDOW_LIFT_UP = "SkyWindowLiftUp"
CMD_OPEN_ENGINE = "OpenEngine"
CMD_CLOSE_ENGINE = "CloseEngine"
CMD_OPEN_FRAGRANCE = "OpenFragrance"
CMD_CLOSE_FRAGRANCE = "CloseFragrance"
CMD_UNLOCK_CHARGE_GUN = "unlockChargingGun"
CMD_STEERING_WHEEL_HEATING = "SteeringWheelHeating"   # 需要额外参数 steeringWheelHeating:0/1
CMD_SEAT_HEATING = "SeatHeating"
CMD_MUSIC_LIGHT_SHOW = "musicLightShow"               # 需要额外参数 openOrClose:1
CMD_OPEN_UV = "openUltraVioletLight"
CMD_CLOSE_UV = "closeUltraVioletLight"
# 以下两个不在 control_config.json 里，是从 APK 其它资源扫出来的，语义待验证：
CMD_SEARCH_CAR = "SearchCar"              # assets/aa/catalog.json —— 寻车（闪灯鸣笛）
CMD_SWITCH_BUTTON_LIGHT = "SwitchButtonLight"  # res/*.xml —— 具体语义未确认

# 2026-10-25 从 App 运行时下载的明文微应用里挖到（APK 内是加密的）：
CMD_SET_SENTRY_MODE = "setSentryMode"     # 哨兵模式，参数见下方 SENTRY_*
CMD_SEAT_VENTILATION = "SeatVentilation"  # 座椅通风（本车无此配置）

# 指令 -> 附带参数（APK 中 otherParams 原样搬运）
CMD_EXTRA_PARAMS = {
    CMD_STEERING_WHEEL_HEATING: {"steeringWheelHeating": "0"},  # 0=开 / 1=关，注意与直觉相反
    CMD_MUSIC_LIGHT_SHOW: {"openOrClose": "1"},
}

# ------------------------------------------------------------------ 空调（2026-10-24 真车实测）
# 开：OpenAir  {airStatus:1, temperature:<18..32>, windLevel:<1..5>}
# 关：CloseAir {airStatus:0}
# App 里没有独立「速冷/速热」指令：速冷 = 18℃ + 默认最大风量，速热 = 32℃ + 默认最大风量。
# 回读要点：**airStatus 不是开关量**（关空调后它仍然是 1），
#   真正的开关看 frontReqWindLevel / airConditioningHairRatings 是否 > 0。
CMD_OPEN_AIR = "OpenAir"
CMD_CLOSE_AIR = "CloseAir"
AC_TEMP_MIN = 18
AC_TEMP_MAX = 32
AC_FAN_LEVELS = (1, 2, 3, 4, 5)
AC_FAN_MAX = 5
AC_TEMP_FAST_COOL = 18       # 速冷
AC_TEMP_FAST_HEAT = 32       # 速热
AC_FAN_FAST = AC_FAN_MAX     # 速冷/速热固定最大风量（用户确认：不做成选项）
AC_DEFAULT_WIND = 3          # 空调关着时点「开」，又没有历史风量时的兜底档位

# 速冷/速热两种「快捷模式」（switch 实体）：名字、目标温度、图标集中在这里。
# 语义（用户确认过）：
#   点开 = 记住当前空调状态（开/关 + 温度 + 风量）→ 下发目标温度 + 最大风量；
#   点关 = 恢复进入前状态：原本开着就恢复温度/风量并保持开；原本关着就关空调。
QUICK_COOL = "cool"
QUICK_HEAT = "heat"
QUICK_MODES = {
    QUICK_COOL: {
        "name": "速冷",
        "temp": AC_TEMP_FAST_COOL,
        "icon": "mdi:snowflake",
        "state_desc": f"{AC_TEMP_FAST_COOL}℃ + {AC_FAN_FAST} 档风量",
    },
    QUICK_HEAT: {
        "name": "速热",
        "temp": AC_TEMP_FAST_HEAT,
        "icon": "mdi:heat-wave",
        "state_desc": f"{AC_TEMP_FAST_HEAT}℃ + {AC_FAN_FAST} 档风量",
    },
}
KEY_AC_WIND = "frontReqWindLevel"
# 空调 hvac_modes 只有「关」和这个自定义字面量；HA 对未知模式会原样显示，
# 所以这里直接用中文「开」，面板上就是「开 / 关」而不是「制热/制冷」。
AC_MODE_ON = "开"
# 车锁 / 后备箱 / 车窗的回读字段
KEY_DOOR_LOCK = "leftFrontDoorLock"   # 0=已锁，1=未锁
KEY_TRUNK = "trunk"                   # 3=已关闭
KEY_WINDOW = "window"                 # 0=关闭；非 0=留缝/开启
KEY_AC_TEMP_SET = "airConditioningSetTemperature"
KEY_CABIN_TEMP = "vehicleTemperature"

# 指令 -> 中文名（服务调用时做校验 / HA UI 显示用）
CMD_LABELS = {
    CMD_UNLOCK: "解锁车门",
    CMD_LOCK: "闭锁车门",
    CMD_OPEN_TRUNK: "开后备箱",
    CMD_CLOSE_TRUNK: "关后备箱",
    CMD_OPEN_WINDOW: "车窗留缝",
    CMD_CLOSE_WINDOW: "关闭车窗",
    CMD_WINDOW_SLIT: "车窗留缝(WindowSlit)",
    CMD_OPEN_DORMER: "天窗起翘",
    CMD_CLOSE_DORMER: "关闭天窗",
    CMD_SKY_WINDOW_LIFT_UP: "天窗起翘(SkyWindowLiftUp)",
    CMD_OPEN_ENGINE: "启动",
    CMD_CLOSE_ENGINE: "熄火",
    CMD_OPEN_FRAGRANCE: "开香氛",
    CMD_CLOSE_FRAGRANCE: "关香氛",
    CMD_UNLOCK_CHARGE_GUN: "慢充枪解锁",
    CMD_STEERING_WHEEL_HEATING: "方向盘加热",
    CMD_SEAT_HEATING: "座椅加热",
    CMD_MUSIC_LIGHT_SHOW: "音乐灯光秀",
    CMD_OPEN_UV: "开智能杀菌",
    CMD_CLOSE_UV: "关智能杀菌",
    CMD_SEARCH_CAR: "寻车",
    CMD_SWITCH_BUTTON_LIGHT: "灯光开关（待验证）",
    CMD_SET_SENTRY_MODE: "哨兵模式",
    CMD_SEAT_VENTILATION: "座椅通风",
}

# 预留：按钮实体（只有开启 enable_control 且已保存控车码时才会创建）
# 保留为「按钮」的指令（成对的操作已改为 lock / cover / switch 实体）
BUTTON_CMDS = (
    CMD_OPEN_ENGINE,
    CMD_CLOSE_ENGINE,
)
# 2026-10-25 实车实测：SearchCar（寻车）下发后车端 90 秒超时（深度休眠唤醒类），不再暴露按钮实体。

# ------------------------------------------------------------------ 车辆能力开关
# 来源：GET /app2/api/v3/car/detail 的 data.json（30 个分组，键名就是 App 里那些 # 开关键）。
# 只有值为 1 的功能，App 才会显示入口，车端也才认——据此过滤按钮，避免出现按了必失败的空实体。
# 2026-10-24 本车（启源 A07 / C236ICA1）实测：control/remoteBaseControl 组里
#   #seatBtn=1 #trunkBtn=1 #WindowSeamsBtn=1 #doorBtn=1 #lockCar=1(blueKeyControl) #searchCar=1
#   #windowBtn=0 #dormerBtn=0 #engineBtn=0 #Fragrance=0 #uvgi=0 #SteeringWheelHeat=0
#   #MusicLightShow=0 #chargeLock=0 #airBtn=0（空调走速冷/速热，OpenAir 已实测可用）
FLAG_GROUP_BASE = "control/remoteBaseControl"
FLAG_GROUP_BLUE = "blueKeyControl"

# 指令 -> (分组, 开关key)；不在表里的指令一律按「可用」处理（车端自会报错）
CMD_FEATURE_FLAGS = {
    CMD_UNLOCK: (FLAG_GROUP_BLUE, "#unlockCar"),
    CMD_LOCK: (FLAG_GROUP_BLUE, "#lockCar"),
    CMD_SEARCH_CAR: (FLAG_GROUP_BLUE, "#searchCar"),
    CMD_OPEN_TRUNK: (FLAG_GROUP_BASE, "#trunkBtn"),
    CMD_CLOSE_TRUNK: (FLAG_GROUP_BASE, "#trunkBtn"),
    CMD_OPEN_WINDOW: (FLAG_GROUP_BASE, "#WindowSeamsBtn"),
    CMD_WINDOW_SLIT: (FLAG_GROUP_BASE, "#WindowSeamsBtn"),
    CMD_CLOSE_WINDOW: (FLAG_GROUP_BASE, "#StateWindow"),
    CMD_OPEN_DORMER: (FLAG_GROUP_BASE, "#dormerBtn"),
    CMD_CLOSE_DORMER: (FLAG_GROUP_BASE, "#dormerBtn"),
    CMD_SKY_WINDOW_LIFT_UP: (FLAG_GROUP_BASE, "#dormerBtn"),
    CMD_OPEN_ENGINE: (FLAG_GROUP_BASE, "#engineBtn"),
    CMD_CLOSE_ENGINE: (FLAG_GROUP_BASE, "#engineBtn"),
    CMD_OPEN_FRAGRANCE: (FLAG_GROUP_BASE, "#Fragrance"),
    CMD_CLOSE_FRAGRANCE: (FLAG_GROUP_BASE, "#Fragrance"),
    CMD_OPEN_UV: (FLAG_GROUP_BASE, "#uvgi"),
    CMD_CLOSE_UV: (FLAG_GROUP_BASE, "#uvgi"),
    CMD_STEERING_WHEEL_HEATING: (FLAG_GROUP_BASE, "#SteeringWheelHeat"),
    CMD_MUSIC_LIGHT_SHOW: (FLAG_GROUP_BASE, "#MusicLightShow"),
    CMD_UNLOCK_CHARGE_GUN: (FLAG_GROUP_BASE, "#chargeLock"),
    CMD_SEAT_HEATING: (FLAG_GROUP_BASE, "#seatBtn"),
}

# 座椅加热参数（2026-10-25 真车实测，值直接就是档位）：
#   {"cmd": "SeatHeating", "lfSeatStatus": "1"}   主驾 1 档   -> driverSeatHeatStatus 6→1
#   {"cmd": "SeatHeating", "lfSeatStatus": "3"}   主驾 3 档   -> 1→3
#   {"cmd": "SeatHeating", "lfSeatStatus": "0"}   主驾关      -> 3→6
#   {"cmd": "SeatHeating", "rfSeatStatus": "1"}   副驾 1 档（同理）
# 回读：driverSeatHeatStatus / passengerSeatHeatStatus，6=关闭，1/2/3=档位。
CMD_SEAT_HEATING_DRIVER = "lfSeatStatus"
CMD_SEAT_HEATING_PASSENGER = "rfSeatStatus"
SEAT_LEVELS = (1, 2, 3)
SEAT_OFF = 0                     # 请求里 0 = 关闭
SEAT_OFF_READBACK = 6            # 回读里 6 = 关闭
SEAT_LABELS = {0: "关", 1: "1档", 2: "2档", 3: "3档"}
SEAT_HEAT_STATUS_DRIVER = "driverSeatHeatStatus"
SEAT_HEAT_STATUS_PASSENGER = "passengerSeatHeatStatus"

# 哨兵模式参数（2026-10-25 实测，字段名来自明文微应用 sentinelModeV2）：
#   {"cmd": "setSentryMode", "sendSentryModeSwitchSts": "1"}                       -> 站岗中
#   {"cmd": "setSentryMode", "sendSentryModeSwitchSts": "0"}                       -> 关闭
#   {"cmd": "setSentryMode", "sendSentryModeSwitchSts": "1",
#    "sendSentryModeSensitivity": "2"}                                             -> 灵敏度 2
# 回读 sentryModeSwitchSts：3=站岗中，0=关闭，1/4=已设置但未开启，2=低电量预开启。
# 注意状态机有 10~40s 滞后：发 switch=1 后先读到 1，过一会才会变 3。
SENTRY_PARAM_SWITCH = "sendSentryModeSwitchSts"
SENTRY_PARAM_SENSITIVITY = "sendSentryModeSensitivity"
KEY_SENTRY_STATE = "sentryModeSwitchSts"
KEY_SENTRY_SENSITIVITY = "SentryModeSensitivity"
# 2026-10-26 修正：3=站岗中；1/4=已布防但车端尚未宣告“站岗中”（下发后 10~40 秒内、车在睡眠、
# 或云端快照未刷新时会长期停在 1）。对用户而言 1/4 与 3 都是“哨兵开着”，所以两者都算开启，
# 差别只体现在属性「哨兵状态 / 是否站岗」上。
SENTRY_ON_VALUES = (1, 3, 4)
SENTRY_ARMED_VALUES = (1, 4)     # 已布防、待站岗确认
SENTRY_PENDING_VALUES = (1, 4)   # 兼容旧代码的别名
SENTRY_OFF_VALUES = (0, 2)
SENTRY_SENSITIVITY_LEVELS = (1, 2)   # 实测 1→2 生效；3 下发后回读仍为 2，暂不暴露
# 2026-10-25 实测：sendSentryModeSensitivity 是 0 基（0/1/2 = 低/中/高），回读同值。
SENTRY_SENSITIVITY_VALUES = (0, 1, 2)
SENTRY_SENSITIVITY_LABELS = {0: "低", 1: "中", 2: "高"}


def car_flags(car_detail: dict) -> dict[str, dict]:
    """从 car/detail 的 data 里取出功能开关表：{分组: {key: 0/1}}。

    data["json"] 是 JSON 字符串（30 个分组），camelCase 与 # 开头的键都在里面。
    """
    if not car_detail:
        return {}
    raw = car_detail.get("json") or car_detail.get("data", {}).get("json")
    if isinstance(raw, str):
        import json as _json

        try:
            raw = _json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def cmd_supported(cmd: str, flags: dict[str, dict]) -> bool:
    """这条指令在本车上是否被 App 暴露（=车端是否支持）。无开关信息的指令默认放行。"""
    spec = CMD_FEATURE_FLAGS.get(cmd)
    if not spec:
        return True
    group, key = spec
    if not flags:
        return True            # 拿不到开关表时不做过滤，免得误杀
    return int(flags.get(group, {}).get(key, 0) or 0) == 1

# ------------------------------------------------------------------ 实体描述
@dataclass(frozen=True)
class SensorDesc:
    """一个传感器的定义。"""
    key: str                      # car/data 返回的原始字段名
    name: str
    unit: str | None = None
    device_class: SensorDeviceClass | None = None
    state_class: SensorStateClass | None = None
    icon: str | None = None
    factor: float = 1.0           # 原始值 * factor


SENSORS: tuple[SensorDesc, ...] = (
    # --- 三电 ---
    SensorDesc("soc", "动力电量", PERCENTAGE, SensorDeviceClass.BATTERY,
               SensorStateClass.MEASUREMENT, "mdi:battery"),
    SensorDesc("remainPower", "剩余电量", PERCENTAGE, None,
               SensorStateClass.MEASUREMENT, "mdi:battery-50"),
    SensorDesc("remainedPowerMile", "剩余续航", UnitOfLength.KILOMETERS,
               SensorDeviceClass.DISTANCE, SensorStateClass.MEASUREMENT, "mdi:map-marker-distance"),
    SensorDesc("totalOdometer", "总里程", UnitOfLength.KILOMETERS,
               SensorDeviceClass.DISTANCE, SensorStateClass.TOTAL_INCREASING, "mdi:counter"),
    SensorDesc("preSetChargeSoc", "充电目标SOC", PERCENTAGE, None,
               SensorStateClass.MEASUREMENT, "mdi:battery-charging-60"),
    SensorDesc("totalVoltage", "电池总电压", UnitOfElectricPotential.VOLT,
               SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:flash"),
    SensorDesc("totalCurrent", "电池总电流", UnitOfElectricCurrent.AMPERE,
               SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT, "mdi:current-dc"),
    SensorDesc("maxVoltageBattery", "最高单体电压", UnitOfElectricPotential.VOLT,
               SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:flash"),
    SensorDesc("minVoltageBattery", "最低单体电压", UnitOfElectricPotential.VOLT,
               SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, "mdi:flash"),
    SensorDesc("insulateResistance", "绝缘电阻", "kΩ", None,
               SensorStateClass.MEASUREMENT, "mdi:omega"),
    SensorDesc("averageEnergyConsumption", "平均能耗", "kWh/100km", None,
               SensorStateClass.MEASUREMENT, "mdi:speedometer"),

    # --- 座舱 ---
    SensorDesc("vehicleTemperature", "车内温度", UnitOfTemperature.CELSIUS,
               SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer"),
    SensorDesc("innerHumidity", "车内湿度", PERCENTAGE,
               SensorDeviceClass.HUMIDITY, SensorStateClass.MEASUREMENT, "mdi:water-percent"),
    SensorDesc("pm25", "车内PM2.5", CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
               SensorDeviceClass.PM25, SensorStateClass.MEASUREMENT, "mdi:air-filter"),
    SensorDesc("airConditioningSetTemperature", "空调设定温度", UnitOfTemperature.CELSIUS,
               SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermostat"),
    SensorDesc("air.temperature", "空调出风温度", UnitOfTemperature.CELSIUS,
               SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, "mdi:thermometer"),
    SensorDesc("air.wind", "空调风量档位", None, None,
               SensorStateClass.MEASUREMENT, "mdi:fan"),

    # --- 轮胎 ---
    SensorDesc("lfTyrePressure", "左前胎压", UnitOfPressure.KPA,
               SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, "mdi:car-tire-alert"),
    SensorDesc("rfTyrePressure", "右前胎压", UnitOfPressure.KPA,
               SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, "mdi:car-tire-alert"),
    SensorDesc("lrTyrePressure", "左后胎压", UnitOfPressure.KPA,
               SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, "mdi:car-tire-alert"),
    SensorDesc("rrTyrePressure", "右后胎压", UnitOfPressure.KPA,
               SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, "mdi:car-tire-alert"),

    # --- 位置 ---
    SensorDesc("lat", "纬度", DEGREE, None, SensorStateClass.MEASUREMENT, "mdi:latitude"),
    SensorDesc("lng", "经度", DEGREE, None, SensorStateClass.MEASUREMENT, "mdi:longitude"),
    SensorDesc("alti", "海拔", UnitOfLength.METERS, None,
               SensorStateClass.MEASUREMENT, "mdi:image-filter-hdr"),
    SensorDesc("heading", "航向角", DEGREE, None, SensorStateClass.MEASUREMENT, "mdi:compass"),
    SensorDesc("speed", "车速", UnitOfSpeed.KILOMETERS_PER_HOUR,
               SensorDeviceClass.SPEED, SensorStateClass.MEASUREMENT, "mdi:speedometer"),
    SensorDesc("satNum", "GPS卫星数", None, None, SensorStateClass.MEASUREMENT, "mdi:satellite-variant"),

    # --- 时间戳 ---
    SensorDesc("lastUpdatedTime", "车况更新时间", None, SensorDeviceClass.TIMESTAMP,
               None, "mdi:clock-outline"),
    SensorDesc("deviceTime", "TBOX时间", None, None, None, "mdi:clock-outline"),
)

# ------------------------------------------------------------------ 原始枚举字段
# 这些字段是 int/str 枚举，语义尚未标定。全部以「诊断类传感器」原值暴露，
# 供你在真实开锁/关锁/开窗时采样对照，标定完再把语义填进 BINARY_ON_VALUES。
RAW_STATE_KEYS = (
    "carStatus", "powerStatusFeedBack", "shiftPosition", "chargeStatus", "dcStatus",
    "airStatus", "door", "window", "trunk", "hood",
    "driverDoor", "passengerDoor", "leftFrontDoor", "rightFrontDoor",
    "leftRearDoor", "rightRearDoor",
    "driverDoorLock", "passengerDoorLock", "leftFrontDoorLock", "rightFrontDoorLock",
    "leftRearDoorLock", "rightRearDoorLock",
    "leftFrontDoorrWindow", "rightFrontDoorrWindow", "leftRearDoorrWindow",
    "light", "positionLamp", "highBeam", "lowBeam", "frontFogLight", "rearFogLight",
    "leftTurnSignal", "rightTurnSignal",
    "steeringWheelHeating", "driverSeatHeatStatus", "passengerSeatHeatStatus",
    "fragrancestatus", "sentryModeSwitchSts", "frontDefrosterSwitch",
)

@dataclass(frozen=True)
class BinaryDesc:
    """一个二元传感器的定义。on_values 里的值判定为 on。"""
    key: str
    name: str
    device_class: BinarySensorDeviceClass | None = None
    on_values: tuple = (1,)
    icon: str | None = None


# ⚠️ 待标定：on_values 目前是「猜测值」，默认只按 1 判 on。
# 正确的做法：在真实开锁 / 关锁 / 开窗前后各采一次 RAW 传感器，把变化后的值填进来。
# 标定完成后把 BINARY_CALIBRATED 改成 True，实体才会默认启用。
BINARY_CALIBRATED = True

# 已经踩过一次样（车辆静止熄火、空调有设定值）时的观测，供标定参考：
#   airStatus=1 / light=1 / steeringWheelHeating=1 / door=1 / trunk=3 / hood=0 / window="0"
#   driverDoorLock=0  leftRearDoorLock=3  rightRearDoorLock=3
#   highBeam=3  lowBeam=3  turnSignal=3  sentryModeSwitchSts=3  seatHeatStatus=6
# → 猜测：3 / 6 很可能是「无效/未上报/关闭」，0 与 1 才是真实状态位。切勿直接采信。
BINARY_SENSORS: tuple[BinaryDesc, ...] = (
    BinaryDesc("airStatus", "空调", None, (1,), "mdi:air-conditioner"),
    BinaryDesc("light", "车灯", BinarySensorDeviceClass.LIGHT, (1,), "mdi:car-light-high"),
    BinaryDesc("chargeStatus", "充电中", BinarySensorDeviceClass.BATTERY_CHARGING,
               (1, 2), "mdi:battery-charging"),
    BinaryDesc("door", "车门未关", BinarySensorDeviceClass.DOOR, (1,), "mdi:car-door"),
    BinaryDesc("window", "车窗未关", BinarySensorDeviceClass.WINDOW, (1,), "mdi:car-door"),
    BinaryDesc("trunk", "后备箱未关", BinarySensorDeviceClass.OPENING, (1,), "mdi:car-back"),
    BinaryDesc("hood", "前舱盖未关", BinarySensorDeviceClass.OPENING, (1,), "mdi:car"),
    BinaryDesc("driverDoorLock", "主驾车门解锁", BinarySensorDeviceClass.LOCK,
               (1,), "mdi:car-door-lock"),
    BinaryDesc("steeringWheelHeating", "方向盘加热", None, (1,), "mdi:steering"),
    BinaryDesc("fragrancestatus", "香氛", None, (1,), "mdi:scent"),
    BinaryDesc("sentryModeSwitchSts", "哨兵模式", None, SENTRY_ON_VALUES, "mdi:shield-car"),
    BinaryDesc("driverSeatHeatStatus", "主驾座椅加热状态", None, (1, 2, 3), "mdi:car-seat-heater"),
    BinaryDesc("passengerSeatHeatStatus", "副驾座椅加热状态", None, (1, 2, 3), "mdi:car-seat-heater"),
)
