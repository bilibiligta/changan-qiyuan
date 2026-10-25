# 长安启源 A07 —— Home Assistant 集成 v0.1.0

> 反向工程自官方 App `com.changan.oushangCos1` 4.0.2（启源）的云端 REST 网关。
> 车辆：启源 A07，`seriesCode=C236ICA1`、`confCode=SC7007AABBEV.CNH4001+00004`、`protocolType=TLV`。
> 网关：`https://m.iov.changan.com.cn`，请求头 `vcs-app-id: qiyuan`。

---

## 0. 能力矩阵（先看这个）

| 能力 | 状态 | 说明 |
|---|---|---|
| 读取车况（电量/续航/里程/温度/湿度/PM2.5/胎压/位置…） | ✅ 可用 | 单次返回 **201 个字段** |
| 车辆档案 / 功能开关 | ✅ 可用 | 30 个功能页开关 |
| 校验控车码 `check-pin` | ✅ 可用 | 正确→`code:0 data:true`；错误→`code:21` |
| RSA 公钥 + PKCS#1 v1.5 加密 | ✅ 可用 | 纯 Python 实现，无第三方依赖 |
| **下发控车指令** | ✅ **可用（需一次短信码授权）** | 真车验证：开窗留缝 / 关窗均成功 |
| 门窗/灯/空调等二元状态 | ⚠️ 待标定 | 语义未确认，实体默认禁用，原值全部暴露 |

**控车已在 2026-10-24 真车跑通**：短信码 → `/app2/api/control/auth` → `execute`
（`pin=RSA(控车码)` + 一次性 `pinVerifyCode`），详见第 5 节。

---

## 1. 安装

把 `custom_components/changan_qiyuan/` 整个目录拷到 HA 的 `config/custom_components/` 下：

```
config/
└── custom_components/
    └── changan_qiyuan/
        ├── __init__.py
        ├── api.py
        ├── rsa_utils.py
        ├── coordinator.py
        ├── sensor.py
        ├── binary_sensor.py
        ├── button.py
        ├── config_flow.py
        ├── const.py
        ├── manifest.json
        ├── services.yaml
        ├── strings.json
        └── translations/
```

重启 HA → **设置 → 设备与服务 → 添加集成 → 搜索「长安启源」**。

零第三方依赖（`requirements: []`），RSA 是自己实现的，不会拖慢 HA 启动。

### 已验证（HA 2026.2.3）

```
$ python smoke_test.py            # 8 个模块全部导入通过
$ python smoke_test.py --live     # 真实车辆只读链路
  ✓ car/detail code=0
  ✓ car/data 字段数=201   soc=88  续航=508km  里程=69183km  车内=29.0°C
  ✓ security/key 长度=216（1024 位 RSA 公钥）
  ✓ check-pin code=21 控车码错误    ← 用已知错误 PIN 做探针，
                                      证明「RSA 加密 + form 编码」链路被服务端正常接受
```

`smoke_test.py` 不需要把集成装进 HA 就能跑；`check_ha_consts.py` 用来校验
用到的 HA 常量名是否都存在（HA 升级改名时能第一时间发现）。

---

## 2. 配置流程

1. 输入注册手机号 → 会收到一条短信验证码（`POST /api/sms/sendAuthcode`）；
2. 输入验证码 → 登录拿 token（`POST /api/user/mobileLogin`）；
3. **控车码（6 位 PIN，可跳过）**：
   - 填了会立刻走一次 `check-pin` 校验，错了会当场报「控车码校验失败」；
   - **默认不落盘**。只有勾选「保存到配置」才会写进 `.storage/core.config_entries`；
   - 不填完全不影响车况读取。

登录态失效时 HA 会弹出「重新登录」，走手机号 + 验证码即可。

### 选项

| 选项 | 默认 | 说明 |
|---|---|---|
| 轮询间隔 | 5 分钟 | 云端子弹有限，别设太快（最小 1 分钟） |
| 控车端点版本 | `v1` | `v3` 还额外要一个参数 `s`，未攻破 |
| 保存控车码 | 关 | 开之前想清楚：config_entries 是明文存储 |
| 启用控车按钮实体 | 关 | 需同时保存控车码 |

---

## 3. 实体

### 3.1 常规传感器（`sensor.py`，共 29 个）

| 实体 | 原始字段 | 单位 |
|---|---|---|
| 动力电量 | `soc` | % |
| 剩余电量 | `remainPower` | % |
| 剩余续航 | `remainedPowerMile` | km |
| 总里程 | `totalOdometer` | km |
| 充电目标 SOC | `preSetChargeSoc` | % |
| 电池总电压 / 总电流 | `totalVoltage` / `totalCurrent` | V / A |
| 最高 / 最低单体电压 | `maxVoltageBattery` / `minVoltageBattery` | V |
| 绝缘电阻 | `insulateResistance` | kΩ |
| 平均能耗 | `averageEnergyConsumption` | kWh/100km |
| 车内温度 | `vehicleTemperature` | °C |
| 车内湿度 | `innerHumidity` | % |
| 车内 PM2.5 | `pm25` | µg/m³ |
| 空调设定温度 | `airConditioningSetTemperature` | °C |
| 空调出风温度 | `air.temperature` | °C |
| 空调风量档位 | `air.wind` | — |
| 四轮胎压 | `lf/rf/lr/rrTyrePressure` | kPa |
| 纬度 / 经度 / 海拔 / 航向角 / 车速 | `lat`/`lng`/`alti`/`heading`/`speed` | ° / m / km/h |
| GPS 卫星数 | `satNum` | — |
| 车况更新时间 | `lastUpdatedTime` | timestamp |

### 3.2 原始枚举传感器（诊断分类，默认启用）

`RAW_STATE_KEYS` 里共 **39 个**字段会以 `xxx (原始值)` 的形式暴露，
例如 `driverDoorLock (原始值)`、`trunk (原始值)`、`highBeam (原始值)`。

> 字段 `leftFrontDoorrWindow` / `rightFrontDoorrWindow` / `leftRearDoorrWindow`
> 中间那个 `rr` 是服务端自己的拼写错误，不是我打错的，照抄即可。

**它们的存在就是为了让你做标定**——见第 4 节。

### 3.3 二元传感器（默认禁用）

空调 / 车灯 / 充电中 / 车门未关 / 车窗未关 / 后备箱未关 / 前舱盖未关 /
主驾车门解锁 / 方向盘加热 / 香氛 / 哨兵模式。

> `const.BINARY_CALIBRATED = False` 时全部默认禁用，不会污染面板。

### 3.4 位置 / device_tracker

本集成**没有**提供 device_tracker 实体——新版 HA 已经废弃 legacy device_tracker 平台，
而且车辆定位本来就该走 GPS 坐标而不是网络扫描。需要地图卡片的话，用模板拼一个：

```yaml
# configuration.yaml
device_tracker:
  - platform: template
    trackers:
      qiyuan_a07:
        friendly_name: 启源 A07
        latitude: "{{ states('sensor.qiyuan_a07_wei_du') }}"
        longitude: "{{ states('sensor.qiyuan_a07_jing_du') }}"
```

实体 ID 以你实际生成的为准（中文实体名会被拼音化）。介意位置隐私的话，
直接在实体注册表里禁用 `纬度` / `经度` 两个传感器。

### 3.5 示例自动化

```yaml
automation:
  - alias: 启源 A07 低电量提醒
    trigger:
      - platform: numeric_state
        entity_id: sensor.qiyuan_a07_dong_li_dian_liang
        below: 20
    action:
      - service: notify.mobile_app
        data:
          title: 车机电量低
          message: "当前 {{ states('sensor.qiyuan_a07_dong_li_dian_liang') }}%，剩余续航 {{ states('sensor.qiyuan_a07_sheng_yu_xu_hang') }} km"
```

---

## 4. 枚举语义标定（**还没做完的一件事**）

车况里的状态位是整数枚举，我们只采到过**一次静止熄火**的样本，无法确定
`0 / 1 / 3 / 6` 各自代表什么。一次采样（车辆熄火、空调有设定值）：

```
carStatus=2        powerStatusFeedBack=0   shiftPosition=15
airStatus=1        light=1                 steeringWheelHeating=1
door=1             window="0"              trunk=3     hood=0
driverDoorLock=0   leftFrontDoorLock=0     leftRearDoorLock=3   rightRearDoorLock=3
highBeam=3         lowBeam=3               turnSignal=3         sentryModeSwitchSts=3
driverSeatHeatStatus=6   passengerSeatHeatStatus=6
chargeStatus=3     dcStatus=1
```

**猜测**：`3` 和 `6` 很可能是「无效 / 未上报 / 关闭」，`0` 与 `1` 才是真实状态位。
但前排门锁 `0` 与后排门锁 `3` 不一致，说明至少有一类不是直觉映射，**别直接采信**。

### 标定步骤

1. 打开 HA，把关注的原始传感器加进一个临时仪表盘；
2. 用**官方 App 或车钥匙**真实做一次操作（比如解锁车门）；
3. 记录操作前后 `driverDoorLock / leftFrontDoorLock / …` 的变化；
4. 把「解锁时的值」填进 `const.py`：

```python
BINARY_SENSORS = (
    ...
    BinaryDesc("driverDoorLock", "主驾车门解锁", BinarySensorDeviceClass.LOCK,
               on_values=(1,)),   # ← 换成实测到的「解锁」值
)
```

5. 把 `BINARY_CALIBRATED` 改成 `True`，重启 HA，实体就会默认启用。

标定完欢迎把结果写回 `a07_probe/car_status_*.json` 旁边的备注文件，方便后面直接复用。

---

## 5. 控车：已跑通（2026-10-24 实测，A07 上真车验证）

### 5.1 完整链路

```
① POST /app2/api/code/cac/sms?token=…        {mobile, usage:"carControl"}   → code:0（下发短信码）
② POST /app2/api/control/auth?token=…        {carId, authCode:"<6位码>"}     → code:0（会话级控车授权）
③ GET  /app2/api/security/key?token=…        → RSA 1024 公钥（base64 DER，每次请求可能变）
④ POST /app2/api/control/createPinVerifyCode {carId}                        → 53 位校验串 vc（一次一用）
⑤ POST /app2/api/control/execute?token=…     {carId, cmd, pin:RSA(PIN), pinVerifyCode:vc}
                                             → {"code":0,"data":{"taskId":…,"handleStatus":"Initialized"},
                                                "msg":"指令发送成功"}
```

真车证据（2026-10-24 21:02–21:05）：

| 指令 | execute 返回 | 车况变化（`/app2/api/car/data`） |
|---|---|---|
| `OpenWindow` | `code:0 指令发送成功`，taskId `…20261024210234_923` | 四窗 `*WindowDegree` 0.0 → **0.1**（留缝） |
| `CloseWindow` | `code:0 指令发送成功`，taskId `…20261024210514_525` | 四窗 `*WindowDegree` → **0.0** |

### 5.2 `execute` 的参数语义（缺一不可，都是踩出来的）

| 情形 | 服务端返回 |
|---|---|
| 缺 `pin` | `code:3 输入控车码` |
| `pin` 传明文 | `code:1 解密失败` |
| 缺 `pinVerifyCode` | `code:40005 缺少必填参数：pinVerifyCode` |
| `pinVerifyCode` 复用 / 过期 | `code:10 指令发送异常，请重试[4000]!` |
| 未做第②步授权 | `code:1004 存在账户安全，请输入校验码` |
| 上一条指令还在执行 | `code:999 上一条指令尚未执行完成` |
| 未知 `cmd` | 仍回 `code:0`（服务端不校验指令名，**别拿假 cmd 当探针**，会占住车的指令队列） |

关键结论：

- **`pinVerifyCode` 一次一用**，每条指令都要重新 `createPinVerifyCode`；
- 短信码只用于第②步，**授权是会话级的**，之后可连发多条指令（已连发 3 轮验证）；
- 短信码也是**一次性**的，拿旧码重放报 `100511 验证码无效或已过期`；
- `/app2/api/control/check-pin {carId, pin:RSA(PIN)}` → `{"code":0,"data":true}`，
  可以**不花短信码**先确认 PIN 对不对（错码 `code:21`）。

### 5.3 服务（推荐用法，不落盘 PIN）

```yaml
# 第一步：发控车校验短信（手机号留空则用配置里登录的号码）
service: changan_qiyuan.send_control_sms
data: {}

# 第二步：拿短信码换授权（授权后长时间有效，失效时 HA 会弹持久通知提醒）
service: changan_qiyuan.authorize_control
data:
  auth_code: "438001"

# 第三步：正常下指令（每条都会自动取新 vc 并 RSA 加密 PIN）
service: changan_qiyuan.control
data:
  cmd: OpenWindow          # 车窗留缝；CloseWindow 关窗
  pin: "242290"            # 可省略：省略时用选项中保存的控车码
```

```yaml
# 只校验控车码，不下发指令（不消耗短信码的安全探针）
service: changan_qiyuan.check_pin
data:
  pin: "123456"

service: changan_qiyuan.refresh
data: {}
```

`1004` 出现时 `api.async_control()` 会抛 `ChanganControlBlocked`，集成会推送持久通知
提示「先 send_control_sms 再 authorize_control」。

### 5.4 按钮实体（可选）

选项里打开「启用控车按钮实体」+ 保存控车码后，会生成
解锁 / 闭锁 / 开后备箱 / 关后备箱 / 车窗留缝 / 关车窗 / 启动 / 熄火 八个按钮。
按下即走 `async_control()`，失败会把错误码直接抛到 UI 上。

---

## 6. 指令码表

来源：APK 内 `assets/control_config.json` —— **明文，不需要脱壳**。
38 条 UI 记录去重后 **20 个** `cmd`；另外从 APK 其它资源里扫出 2 个候选
（`SearchCar` 在 `assets/aa/catalog.json`，`SwitchButtonLight` 在 `res/*.xml`），
后者语义未验证，已一并写进 `const.CMD_LABELS` 备用。

| 功能 | `cmd` | 附带参数 | 本车开关 | 本车支持 |
|---|---|---|---|---|
| 解锁 | `UnLockDoor` | — | `doorBtn` | ✅ |
| 闭锁 | `LockDoor` | — | `doorBtn` | ✅ |
| 开后备箱 | `OpenTrunk` | — | `trunkBtn` | ✅ |
| 关后备箱 | `CloseTrunk` | — | `trunkBtn` | ✅ |
| 车窗留缝 | `WindowSlit` | — | `WindowSeamsBtn` | ✅ |
| 关车窗 | `CloseWindow` | — | `WindowSeamsBtn` | ✅ |
| 座椅加热 | `SeatHeating` | — | `seatBtn` | ✅ |
| 启动 / 熄火 | `OpenEngine` / `CloseEngine` | — | `engineBtn` | ❌ |
| 天窗起翘 / 关 | `OpenDormer` / `CloseDormer` / `SkyWindowLiftUp` | — | `dormerBtn` | ❌ |
| 香氛 | `OpenFragrance` / `CloseFragrance` | — | `Fragrance` | ❌ |
| 方向盘加热 | `SteeringWheelHeating` | `steeringWheelHeating=0`（**0 是开，1 是关**） | `SteeringWheelHeat` | ❌ |
| 智能杀菌 | `openUltraVioletLight` / `closeUltraVioletLight` | — | `uvgi` | ❌ |
| 慢充枪解锁 | `unlockChargingGun` | — | `chargeLock` | ❌ |
| 音乐灯光秀 | `musicLightShow` | `openOrClose=1` | `MusicLightShow` | ❌ |
| 寻车（闪灯鸣笛） | `SearchCar` | — | 未知 | ❓ 待验证 |
| 灯光开关 | `SwitchButtonLight` | — | 未知 | ❓ 待验证 |

> ⚠️ 带 `#` 的（如 `#searchCar`、`#lockCar`、`#openTrunk`）是 **UI 功能开关**，
> **不是**指令码。拿它们当 `cmd` 下发会被拒（我在这上面浪费过一轮）。

---

## 7. 附录：踩坑清单（最重要，改代码前必读）

| # | 坑 | 正确做法 |
|---|---|---|
| 1 | token 放 header | 必须放 **query** `?token=xxx`。header `X-VCS-User-Token` 在 `/app2/*` 上无效 |
| 2 | 请求体用 JSON | 必须 `application/x-www-form-urlencoded`，否则报 `缺少必填参数：carId` |
| 3 | 用登录响应里的 carId | 登录响应 `carId` **恒为 null**，必须单独 `GET /app2/api/v3/car/detail?token=&type=0` |
| 4 | 包名写成 `com.changan.qiyuan` | 真实包名是 `com.changan.oushangCos1`，`qiyuan` 只是 Java 类名前缀 |
| 5 | PIN 明文传给 `check-pin` | 必须先用 `/app2/api/security/key` 的公钥做 RSA/PKCS#1 v1.5 加密，否则 `-1000 密钥已过期` |
| 6 | 把 `createPinVerifyCode` 的校验串当 PIN 用 | 校验串是 `<19位id>::<32位hex>`，它和 `RSA(PIN)` 是两个不同东西，服务端对两者的反应不同 |
| 7 | 拿 UI 开关当 cmd | `#searchCar` 之类不是指令码 |
| 8 | DER 解析 offset 复用 | 解完 BIT STRING 后要**跳 1 字节**（未使用位数），且 RSAPublicKey 内部偏移必须**重新从 0 开始**，否则 `IndexError: index out of range` |
| 9 | 误判 1004 是临时限流 | 等 2 小时 + 换 token 都无效，是账号级风控 |
| 10 | 密集请求 | 我在约 50 次下发后触发的 1004。**不要高频轮询 execute** |
| 11 | 用 `check-pin` 做「指令合法性预言机」 | 不行：合法 `OpenTrunk` 和非法 `ZzzBogusCmd123` 都返回 `code:10`，PIN 校验发生在 cmd 校验之前 |
| 12 | 依赖 cryptography/pycryptodome | 本机没有，纯 Python 实现 30 行搞定（见 `rsa_utils.py`） |
| 13 | Git Bash 里跑 adb | 需 `export MSYS_NO_PATHCONV=1`，否则路径被转换；且 adb daemon 不能并行调用 |

---

## 8. 附录：跑通的接口清单

全部实测过，token 走 query、body 走 form-urlencoded。

| 接口 | 方法 | 参数 | 状态 |
|---|---|---|---|
| `/api/sms/sendAuthcode` | POST | `{phone, type:1}` | ✅ |
| `/api/user/mobileLogin` | POST | `{phone, verifyCode}` → `data.token` | ✅ |
| `/app2/api/v3/car/detail` | GET | `?token=&type=0` | ✅ carId + 30 个功能开关 |
| `/app2/api/car/data` | POST | `?token=&keys=*&carId=`，空 body | ✅ 201 字段 |
| `/app2/api/car/location` | GET | `?token=&carId=` | ✅ |
| `/app2/api/security/key` | GET | `?token=` | ✅ RSA 公钥 |
| `/app2/api/control/check-pin` | POST | `{carId, pin:RSA(PIN)}` | ✅ `code:0 data:true` |
| `/app2/api/control/createPinVerifyCode` | POST | `{carId}` | ✅ 返回校验串 |
| `/app2/api/control/execute` | POST | `{carId, pinVerifyCode, cmd}` | ❌ `code:1004` |
| `/app2/api/v2/control/execute` | POST | 同上 | ❌ `code:20 控车码错误`（不受 1004 影响） |
| `/app2/api/v3/control/execute` | POST | 同上 **+ `s`** | ❌ 缺 `s` 无法绕过 |
| `/appserver/api/user/renewalToken` | POST | `{token}` | ❌ `code:2 token expired` |
| `/app2/api/device/upload` | POST | 设备信息 | ⚠️ `code:0` 但对 1004 无帮助 |
| `/app2/api/v2/car/perms` | GET | `?carId=` | ⚠️ `code:0` 空数据 |
| `/app2/api/car/users` | GET | — | ✅ 共享用户列表（均无 `car_control` 权限） |

---

## 9. 后续开发 TODO（按优先级）

1. **枚举语义标定**（第 4 节）—— 唯一影响「读」的准确性的事，需要一次真实操作采样。
2. **攻 v2 端点**：`/app2/api/v2/control/execute` 明确返回「控车码错误」而不是 1004，
   说明它走的是另一条鉴权路径。把 `RSA(PIN)` 换成 `createPinVerifyCode` 的校验串再试一轮。
3. **补齐 v3 的 `s` 参数**：逐步补全法已经确认 `s` 是必填，且是 RSA 加密载荷；
   `RSA(PIN)`/`RSA(carId)`/`RSA(PIN+carId)`/校验串 都被判 `-1000`，还需继续试别的明文。
4. **抓官方 App 的真实请求**：本机 root 机上 App 启动 20–80ms 就被 SIGKILL
   （signal 9，无 Java 异常、无 tombstone；Enforcing/Permissive 都复现；
   Magisk alpha 的 Zygisk 未加载 → DenyList 无效）。
   修好 Zygisk + Shamiko 后在非 root 感知下抓包，是最直接的破局点。
   → 详见 `a07_probe/脱壳诊断报告_20261012.md`。
5. **查控车结果回调**：`/api/car/getControlInfo`、`/api/car/getControlInfoByTaskId`
   可以查上次控车的结果，`data.cmds` 目前恒为空——控车跑通后这里会有值。

---

## 10. 隐私与安全

- 手机号 / 短信验证码 / 控车码 **都不会写进日志**；
- 控车码默认**不保存**，每次通过服务调用传入；
- 车况输出里的 `vin` / `carId` / `tuid` / `userId` / `requestId` 属于敏感字段，
  集成内部使用但不作为实体暴露（位置经纬度会作为传感器暴露，介意的话禁用这两个实体）；
- 如果你的 HA 实例会被分享出去，请务必不要勾选「保存控车码」。

---

## 11. v0.3.0 变更（2026-10-24 深夜补录，全部真车实测过）

### 11.1 新增服务 `query_task` —— 唯一可信的「指令是否真的执行了」

`control` 服务返回 `code:0 / 指令发送成功` **只代表指令进了车端队列**，
服务端对任意 `cmd` 字符串都返回同一句（实测 `SetTemperature`、`QuickHeat`、
`OpenQuickCool`、`ZzzProbeCmd` 全是 `code:0`）。真正能判定成败的是这条：

```
POST /app2/api/car/getControlInfo      body: {carId, id=<taskId>}
-> {"code":0,"data":{"tboxCmd":"UnLockDoor","handleStatus":"Completed",
                     "handleStatusDesc":"指令执行成功","handleCode":"0",
                     "cmdSendTime":"2026-10-24 21:31:30",
                     "cmdFinishTime":"2026-10-24 21:31:31",
                     "taskId":"...","requestId":"...","senderType":"app"}}
```

坑：参数名是 **`id`**，不是 `taskId`；传 `taskId` 会得到
`{"code":0,"msg":"指令走丢了，请重试！"}`。`/app2/api/car/getControlInfoByTaskId`
在 app2 网关上是 404，只有旧网关 `/api/car/*` 有，且旧网关只认旧 token（返回
`token expired`）。`data.cmds` 恒为空 —— 指令能力表不下发。

### 11.2 会话级授权的 TTL（实测）

一次 `authorize_control` 之后，同一 token 下连发指令、跨 10+ 分钟仍在授权态，
不需要每条都发短信。但 `token` 本身 3600s 过期，换 token 后**要重新授权**。

### 11.3 队列是串行的（关系到 HA 自动化写法）

车端一条指令约占用 60s，期间任何新指令都返回 `999 上一条指令尚未执行完成`。
集成已把重试改成 8 次 × 10s；自动化里连发多条时请留 ≥ 30s 间隔，
否则会看到 `999` 刷日志。

### 11.4 仍未攻破：空调 / 哨兵 / 温度 的指令码

`assets/control_config.json` 里没有空调类 cmd；全 APK 二进制扫描只在
Unity 的 `catalog.json`、`global-metadata.dat`（`AirConditioner`/`SentryMode` 是 UI 名）
和 H5 的 `sentinelMode` 属性里命中，**真实 cmd 常量在爱加密保护的
`assets/ijiami.dat` 内**。已实测失败的候选：`SetTemperature`（6 种参数名）、
`QuickHeat`、`OpenQuickCool`、`SentinelMode{sentryModeStatus}` —— 都是
`code:0` 但车况回读无变化，即被服务端当未知指令静默丢弃。

`/app2/api/v2/control/execute` 与 v3 都要一个未攻破的 `s` 参数：
缺 `pinVerifyCode` 报 `40005`，带全 `carId+cmd+pin+pinVerifyCode` 却报
`-1000 密钥已过期，请重新请求`（与 v3 同症状），所以 v1 仍是唯一可用端点。

### 11.5 抓包（方案 B）的现实前提

`res/8GD.xml`（manifest 的 `networkSecurityConfig`）解出来只有：

```xml
<network-security-config><base-config cleartextTrafficPermitted="true"/></network-security-config>
```

没有 `domain-config`、没有 `trust-anchors` → targetSdk 31 默认规则下
**App 不信任用户安装的 CA**。所以普通「装个 mitmproxy 证书」抓不到 HTTPS，
必须是系统级 CA（需要 root 且要能躲过爱加密的 root 检测），或者用 iOS 设备
（iOS 装描述文件后对 App 全局生效，除非有证书绑定）。
`a07_probe/step66`–`step72` 记录了这条路线上的全部接口测绘结果。

---

## 12. 空调（v0.4.0，2026-10-24 真车实测通过）

`climate.changan_qiyuan_*` 空调实体：开/关、制冷/制热、18~32℃、1~5 档风量。

| 动作 | 请求 | 说明 |
|---|---|---|
| 开 | `OpenAir` `{airStatus:"1", temperature:"18".."32", windLevel:"1".."5"}` | 三个参数缺一不可，只给 airStatus 不生效 |
| 关 | `CloseAir` `{airStatus:"0"}` | 实测 `Completed / 指令执行成功`，风量回到 0 |
| 速冷 | 开 + `temperature=18` + 最大风量 | App 里没有独立「速冷」指令，图标由车机自己点亮 |
| 速热 | 开 + `temperature=32` + 最大风量 | 同上；实测 32℃ + 风量 4 档后「速热」图标亮起 |

### 12.1 回读陷阱（务必注意）

**`airStatus` 不是开关量。** 关掉空调后车端回读 `airStatus` 仍然是 `1`。
判断空调是否开启请看：

- `frontReqWindLevel`（风量档位，关空调后为 `0`）
- `airConditioningHairRatings`（同上，关后为 `0`）

`airConditioningSetTemperature` 只是「上次设定温度」，关空调后不会复位，
所以 UI 上会看到「已关闭 + 32℃」这种组合。

### 12.2 指令排队

车端指令串行，一条约占用 60 秒，期间再发返回 `code:999 上一条指令尚未执行完成`；
vc（pinVerifyCode）被消费后重发会返回 `code:10 指令发送异常，请重试[4000]`。
`api.async_control()` 已对这两个码做自动重试。

### 12.3 哨兵（未打通，原因已定位）

- App 的点击在服务端留下 `tboxCmd=setSentryMode`、`handleStatus=Completed`，
  说明指令名是小写 `setSentryMode`，走的是同一个 `control/execute` 接口。
- 但**同样的请求从服务端发出去一律 `RunFailure / RequestId不满足指令执行条件`**；
  大写 `SetSentryMode` 能被受理，却在两分钟后变成
  `Timeout / 车辆可能处于信号不稳定地区，激活车辆失败`。
- 线索：App 生成的 `requestId` 是 16 位（如 `2026102422160101` = `yyyyMMddHHmmss` + 2 位序号），
  而服务端替我们回填的 `requestId` 是 17 位；哨兵属于「深度唤醒类」指令，
  需要车辆真正被激活（App 走 MQTT 长连接 / 前台心跳唤醒）。
- 结论：**哨兵暂不在集成里实现**，需要抓到 App 的完整请求体（`requestId` 生成规则
  + 真实参数名）或直接复用 MQTT 通道才能补上。空调/车窗/车锁/后备箱/位置/车况不受影响。

---

## 13. 车辆能力表（v0.4.1）：按钮按车过滤，不再列「App 里没有」的功能

以前 `BUTTON_CMDS` 是照 APK 的 `assets/control_config.json`（24 条通用指令）列的，
结果在启源 A07 上会生成一堆按了只能报错的空按钮。现在改成**以车端能力表为准**。

### 13.1 能力从哪来

`GET /app2/api/v3/car/detail` 的 `data.json` 字符串，里面有 30 个分组，键名就是 App 的 `#` 开关键：

```json
{"control/remoteBaseControl": {"#trunkBtn": 1, "#seatBtn": 1, "#WindowSeamsBtn": 1, ...},
 "blueKeyControl": {"#lockCar": 1, "#unlockCar": 1, "#searchCar": 1, ...},
 "carControl": {"#privateKey": 1}, ...}
```

`const.car_flags()` 解析它，`const.cmd_supported()` 按 `CMD_FEATURE_FLAGS` 判断，
`button.py` 创建实体前逐个过滤，跳过项写日志：`本车不支持，跳过按钮：...`。

### 13.2 本车（启源 A07 / C236ICA1）实测能力

| 功能 | 开关 | 说明 |
|---|---|---|
| 锁车 / 解锁 | `#lockCar` `#unlockCar` =1 | 已实测 |
| 后备箱开启 | `#trunkBtn` =1 | `#openTail`/`#closeTail`=0 → 尾翼无 |
| 车窗留缝 | `#WindowSeamsBtn` =1 | `#windowBtn`=0 → 升降窗无 |
| 车门控制 | `#doorBtn` =1 | |
| 座椅加热 | `#seatBtn` =1 | `carHeatSeatGears={"heat3":1}`，3 挡制；通风全 0 |
| 空调 | 走速冷/速热 | `#airBtn`=0（App 不显示空调快捷按钮），但 `OpenAir` 实测可用 |
| 寻车（闪灯/鸣笛） | `#lightBtn` =1 | **`#lightBtn` 的语义是寻车，不是车灯**（见 `assets/zhixing/car_function_config.xml`） |
| 空气净化 | `#purificationBtn` =1 | 指令名未知 |
| 天窗 / 起翘 | `#dormerBtn` `#SkylightWarpingBtn` =0 | 无 |
| 发动机 / 香氛 / 紫外杀菌 / 方向盘加热 / 灯光秀 / 充电枪解锁 | 全 0 | 无 |

`assets/zhixing/car_function_config.xml` 是这些 `#` 开关的**中文语义字典**（23KB 明文），
排查「App 到底有没有这个功能」时直接查它，比猜指令名快得多。

### 13.3 深度唤醒类指令（寻车 / 哨兵）为什么发不出去

两者都被服务端受理（`code:0 指令发送成功`），然后 ~5 分钟后变成：

```
handleCode=2007  handleStatus=Timeout
handleStatusDesc=车辆可能处于信号不稳定地区，激活车辆失败
```

同一时期 App 自己的点击是 `handleStatus=Completed`，且 `requestId` 是 16 位
（`yyyyMMddHHmmss`+2 位序号），我们的是空串或 17 位。结论：这类指令需要
**App 那条 MQTT/前台唤醒通道**先把 T-BOX 激活，纯 HTTP 发不到车上。
`SearchCar`（我猜的名字）走同一路径，同样 Timeout。

---

## 14. 座椅加热 / 哨兵模式（v0.5.0，2026-10-25 真车实测通过）

### 14.1 为什么这次能拿到参数

APK 里的 `classes.dex` 只有 10 KB（爱加密壳），业务代码全在 `assets/ijiami.dat`（53 MB 加密），
静态反编译拿不到。真正的参数来源是 **App 运行时从服务端下载到本机的 H5 微应用**：

```
/data/data/com.changan.oushangCos1/files/dist/            (100 MB，全部明文 JS)
├── micro.cn.com.qiyuan.oneButtonStart/assets/index-legacy-*.js
├── micro.cn.com.qiyuan.oneButtonStartCreate/assets/js/main.js      (486 KB)
├── micro.cn.com.qiyuan.sentinelModeV2/assets/index-legacy-*.js     (19 KB)
└── root/car_function_config.xml  (23 KB，所有 # 开关的中文语义字典)
```

这些微应用**不在 APK 里**（由 `module_upgrade` 动态下发），所以之前扫 APK 才一无所获。
只要手机能 root（本文用 Magisk + Shamiko 让 App 在 root 下正常启动），`adb pull` 出来就是源码级明文。

### 14.2 座椅加热

```jsonc
{"cmd": "SeatHeating", "lfSeatStatus": 1}   // 主驾 1 档
{"cmd": "SeatHeating", "rfSeatStatus": 3}   // 副驾 3 档
{"cmd": "SeatHeating", "lfSeatStatus": 0}   // 主驾关闭
```

* 参数名就是 `lfSeatStatus` / `rfSeatStatus`，**值直接是档位**，`0` = 关闭。
* 回读 `driverSeatHeatStatus` / `passengerSeatHeatStatus`：`6` = 关闭，`1/2/3` = 档位。
* 实测：`lfSeatStatus=1` → 主驾 6→1（从关闭态可直接开启）；`=3` → 1→3；`=0` → 3→6。
* 同一族还有 `SeatVentilation`（通风），本车 `carVentilateSeatGears` 全 0，无此配置。

### 14.3 哨兵模式

```jsonc
{"cmd": "setSentryMode", "sendSentryModeSwitchSts": 1}                              // 开启
{"cmd": "setSentryMode", "sendSentryModeSwitchSts": 0}                              // 关闭
{"cmd": "setSentryMode", "sendSentryModeSwitchSts": 1, "sendSentryModeSensitivity": 2}  // 开启并设灵敏度
```

* 回读 `sentryModeSwitchSts`：`3` = 站岗中，`0` = 关闭，`1/4` = 已设置但未开启，`2` = 低电量预开启。
* **状态机滞后 10~40 秒**：刚下发 `switch=1` 会先读到 `1`，过一会才变 `3`；不要以第一次回读判失败。
* 灵敏度字段 `SentryModeSensitivity`：实测 `1→2` 生效；下发 `3` 后回读仍为 `2`，档位上限待再验。
* 实测：`switch=1` → `0→3`（站岗中）；`switch=0` → `3→0`。

### 14.4 更正 §13.3

§13.3 曾推断「哨兵/寻车必须走 App 的 MQTT 通道才能激活 T-BOX」——**这个结论对哨兵是错的**：
哨兵用纯 HTTP `setSentryMode` 就能真正生效（回读 `3`）。当时之所以 `Timeout`，
是因为参数名根本没猜对（`sentryModeStatus` 之类全被服务端丢弃，任务空转后超时）。
寻车 `SearchCar` 仍未验证，可能也存在正确的参数名。

### 14.5 v0.5.0 新增实体

| 平台 | 实体 | 说明 |
|---|---|---|
| `switch` | 哨兵模式 | 开/关；属性里带原始值、状态说明、灵敏度 |
| `select` | 主驾座椅加热 / 副驾座椅加热 | 关 / 1档 / 2档 / 3档 |

空调（`climate`）仍为 v0.4.0 的实现，未改动。

### 14.6 授权窗口（1004）的实操结论

`POST /app2/api/control/auth {carId, authCode}` 换到的授权是**会话级、按分钟计**：

* 短信码本身有效期约 1 分钟（实测 80 秒后提交直接 `100511 验证码无效或已过期`）；
* 授权成功后可连发多条指令，实测 01:53 授权、01:55:45 仍在有效期内（≥2.5 分钟）；
* 失效后再发指令一律 `code:1004 存在账户安全，请输入校验码` → 重新走「发短信 → 授权」。

## 十五、最终实测结论（2026-10-25 定稿）

### 15.1 文档索引（交接用）

| 文档 | 内容 |
|---|---|
| `../../a07_probe/控车指令码_实测_20261024.md` | **最终实测表**：每条指令的参数、是否需要控车码、实测状态、生效判据 |
| `../../a07_probe/启源A07_控车二次开发说明.md` | **交接级说明**：调用链、接口字典、错误码、能力表、参考实现、侦察方法论，拿到即可二次开发 |

### 15.2 更正 14.6 关于授权时效的说法

14.6 写的「会话级、分钟级」**已被后续实测推翻**：

* 2026-10-25 01:53 完成短信授权后，02:05、02:14、02:15 连续多轮 `execute` 均未再要校验码
* 过夜后 11:01 再次执行 `SeatHeating` 仍是 `code:0`（期间只做过 token 刷新）
* 结论：**短信授权有效期 ≥ 9 小时**，不需要每条指令都重新授权；当天那两次 `1004` 出现在「尚未做过授权」的阶段

### 15.3 逐项验收状态

| 功能 | 实体 | 状态 |
|---|---|---|
| 空调开关 / 温度 18–32 / 风量 1–5 | `climate.空调` | ✅ 实测（含跨夜多轮） |
| 速冷 / 速热 | `button` | ✅ 实测（速冷=开+18℃+大风，速热=开+32℃+大风） |
| 哨兵模式 | `switch.哨兵模式` | ✅ 实测（0↔3，灵敏度 0/1/2 = 低/中/高） |
| 主副驾座椅加热 1–3 档 | `select.主驾/副驾座椅加热` | ✅ 实测（主驾 2、3 档用户肉眼确认；回读 6=关） |
| 解锁 / 锁车 | `button` | ✅ 实测 |
| 车窗全关 | `button` | ✅ 实测 |
| 后备箱开/关 | `button` | ⚠️ 已实现，**未上车实测** |
| 寻车 | `button` | ⚠️ 服务端受理但任务 `Timeout(2007)`（深度唤醒类），未上车验证 |
| 车窗留缝 / 方向盘加热 | `button` | ⚠️ 已实现，未实测；本车能力位为 0，可能不出现 |
| 天窗起翘 / 发动机 / 香氛 / 紫外线 / 灯光秀 / 充电枪 | — | ❌ 本车不支持（`#flag=0`，按钮自动隐藏） |

### 15.4 两个关键坑（写成常量以防后人踩）

1. **空调开关不能看 `airStatus`**（恒为 1），要看 `frontReqWindLevel`（0=关）。
2. **座椅“关”的回读是 `6`**，不是 `0`；`sentryModeSwitchSts` 的 `1` 是「已设置未开启」，`3` 才是站岗中。

## 十六、0.6.3：属性中文化 + 时间戳（2026-10-25）

按用户要求，实体上不再直接暴露原始回读值，改成中文描述并带三段时间：

| 属性 | 含义 |
| --- | --- |
| 上次指令 | 指令中文名 + 代码（如 `闭锁车门（LockDoor）`） |
| 指令下发时间 | HA 调用云端 control/execute 的时刻（本地时区 `MM-DD HH:MM:SS`） |
| HA 首次看到变化 | 密集回读第一次读到新状态的时刻，括号里是相对下发的秒数 |
| 车端开始执行 | 云端任务里的 `cmdSendTime`（车端 T-BOX 收到指令） |
| 车端执行完成 | `cmdFinishTime` |
| 车端任务状态 | `Initialized/Completed/Timeout/RunFailure` 的中文解释 |

实现要点：`coordinator.timeline_text()` 统一生成这些键，climate / switch / select 三个平台都 `attrs.update(...)`。
同时修掉一个隐蔽 bug：`extra_state_attributes` 以前直接返回 `coordinator.last_timeline` 这个**可变字典对象**，
而密集回读会原地修改它 —— HA 状态机比较新旧 state 时看到的是同一个对象，判定“属性没变”就不写 state，
于是时间线里新增的 `first_readback_at` 永远不出现。现在一律返回副本/新字典。

---

## 十六、v0.6.5 延迟与掉线修复（2026-10-25 实测）

### 16.1 「下发后 20 秒才生效」的三个真因

1. **快回读卡死**：`query_task()` 没有超时，云端偶发不返回时协程永久挂起 →
   密集回读只跑第 1 次就停摆，面板一直停在旧值。
   修复：所有云端请求加 10 秒硬超时（`FAST_POLL_TIMEOUT`）。
2. **状态不刷新**：`extra_state_attributes` 直接返回 `coordinator.last_timeline`
   这个可变字典，密集回读原地改它 → HA 比较新旧 state 时看到同一对象，判定
   「属性没变」而不写 state，时间线里新增字段永远看不到。
   修复：一律返回副本 `dict(...)`。
3. **电源按钮 500**：`supported_features` 没带 `TURN_ON/TURN_OFF`，HA 服务层直接抛
   `ServiceNotSupported: Entity ... does not support action climate.turn_on`。
   修复：`_FEATURES |= ClimateFeature.TURN_ON | TURN_OFF`（用 getattr 兼容旧版）。

### 16.2 token 只有 1 小时 → 集成每小时掉线

`/cac/api/v1/oauth2/refresh_token` 可用（`client_id=bce34639784c4b159e7ebeda9a5d13bb`，
`grant_type=refresh_token`）。登录响应里的 `refreshToken` 现会存进配置条目，
`coordinator.async_try_refresh_token()` 在 `code:2 token已过期` 时自动续期并写回，
只有续期也失败才要求用户重新登录。

### 16.3 实测延迟（v0.6.5，密集回读 3 秒/次）

| 指令 | 下发 → HA 首次看到变化 | 备注 |
| --- | --- | --- |
| `OpenAir`（设温 26℃） | 3.8 秒 | 车处于唤醒态 |
| `LockDoor` | 3.4 秒 | 车已锁 → 车端 `RunFailure`（正常） |
| `CloseAir` | 9.8 秒 | 车在休眠，T-BOX 被唤醒后才生效 |

结论：**车端执行本身 1~2 秒**，多出来的时间是「T-BOX 唤醒 + 云端任务状态回写」；
解锁/寻车这类深唤醒指令在车休眠时会 15~60 秒甚至 `2007 Timeout`。

---

## 十七、v0.8.0 变更（2026-10-25）

### 17.1 品牌图标（brand/）
用官方「启源标 + 长安启源」矢量稿重制，8 个文件放在
`custom_components/changan_qiyuan/brand/`：

| 文件 | 用途 |
| --- | --- |
| `icon.png` / `icon@2x.png` | 列表、集成详情页的方形图标（蓝标，透明底） |
| `logo.png` / `logo@2x.png` | 集成详情页横版标题（标 + 文字） |
| `dark_icon*.png` / `dark_logo*.png` | 深色主题变体（重着色为白色） |

HA 通过 `/api/brands/integration/changan_qiyuan/icon.png` 提供，已实测 200。
改完品牌图后浏览器要 `Ctrl+F5` 强刷才看得到。

### 17.2 空调面板显示「开 / 关」
`HVACMode.HEAT_COOL` 会被 HA 译成「制热/制冷」。改用**自定义模式字面量**
`AC_MODE_ON = "开"`（`const.py`），`hvac_modes = ["off", "开"]`；
HA 对未知 hvac_mode 原样显示，于是卡片上就是「开 / 关」。
`async_set_hvac_mode` 对除 `off` 外的任何值都按开机处理，兼容旧的 heat_cool/cool。

### 17.3 成对按钮改为原生实体

| 原按钮 | 新实体 | 状态回读 |
| --- | --- | --- |
| 解锁 + 闭锁 | `lock.…_che_men_suo`（车门锁） | `leftFrontDoorLock` 0=已锁 / 1=未锁 |
| 开后备箱 + 关后备箱 | `cover.…_hou_bei_xiang`（后备箱） | `trunk` 3=已关 |
| 车窗留缝 + 关闭车窗 | `switch.…_che_chuang_liu_feng`（车窗留缝） | `window` 0=关闭 |
| 寻车 / 启动 / 熄火 | 仍为 button | — |

代码：`lock.py`、`cover.py`（新增），`switch.py` 增加 `ChanganWindowSeamsSwitch`；
`BUTTON_CMDS` 只留 `OpenEngine/CloseEngine/SearchCar`；`CONTROL_PLATFORMS`
增加 `lock`、`cover`。旧按钮的注册表条目已用 WS `config/entity_registry/remove` 清掉。

实测（17:49:37）：`lock.lock` → **3.1 秒** HA 首次看到变化，车端正确回报
「车端拒绝执行（已处于锁定状态）」。实体总数 92。
