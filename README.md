# 长安启源 · Home Assistant 集成

把长安启源（启源 A07 等）的**车况读取**与**远程控车**接进 Home Assistant。

- **68 个传感器**：电量、续航、里程、车门/车窗/后备箱、四轮胎压、车内温湿度、空调状态、座椅加热、哨兵状态、GPS 位置……
- **可调控件**：空调（18~32℃ / 1~5 档风量 / 速冷 / 速热）、主副驾座椅加热（0/1/2/3 档）、哨兵（开关 + 低/中/高灵敏度）、车门锁、后备箱、车窗留缝
- 走官方 App 同一条云端接口，**不需要改装车机、不需要自建服务器**
- 附带一张配套的 Lovelace **车辆卡片**（另一个仓库，可选）

> 所有功能都在真车（启源 A07 / C236ICA1）上实测通过。首次使用只需要：手机号 + 短信验证码 + 6 位控车码。

---

## 目录

- [功能一览](#功能一览)
- [安装](#安装)
- [配置](#配置)
- [实体与服务](#实体与服务)
- [常见问题](#常见问题)
- [已知限制](#已知限制)
- [文档](#文档)

---

## 功能一览

| 分类 | 内容 |
| --- | --- |
| 车况 | 剩余电量 / 续航 / 总里程、四门与车窗状态、后备箱、四轮胎压、车内温湿度、12V 电量、充电状态、GPS 位置 |
| 空调 | 开关、设定温度（18~32℃）、风量（1~5 档）、速冷、速热 |
| 座椅 | 主驾 / 副驾加热，各 0/1/2/3 档 |
| 安防 | 哨兵模式开关 + 灵敏度（低/中/高）、车门锁、后备箱、车窗留缝、寻车 |
| 其他 | 发动机远程启动/熄火（部分车型）；所有能力按车辆实际配置自动过滤，App 里没有的功能不会出现 |

**用之前值得知道的三个坑**（集成里都已处理，这里只是让你心里有数）：

1. 控车接口有风控，偶尔返回「**存在账户安全，请输入校验码**」。这时用 `changan_qiyuan.send_control_sms` + `changan_qiyuan.authorize_control` 走一次短信验证，一次授权可用很久。
2. 云端任务是**串行**的，上一条没跑完会返回「上一条指令尚未执行完成」，集成会自动重试。
3. 接口返回「指令发送成功」**不代表车端已执行**，只有「车况回读变化」才是真的——集成已用回读校验代替这个判断。

## 安装

### 方式一：HACS（推荐）

1. HACS → 集成 → 右上角 ⋮ → **自定义存储库**
2. 地址填本仓库，类别选 **Integration**，添加
3. HACS 里搜索「长安启源」→ 下载 → **重启 Home Assistant**

### 方式二：手动

1. 下载本仓库，把 `custom_components/changan_qiyuan` 整个目录复制到 `/config/custom_components/changan_qiyuan`
2. 重启 Home Assistant

装好后集成图标是启源蓝标（`brand/` 已带图标，无需额外操作）。

## 配置

**设置 → 设备与服务 → 添加集成 → 搜「长安启源」**

| 步骤 | 填什么 | 说明 |
| --- | --- | --- |
| 1 | 手机号 | 与启源 App 同一个号 |
| 2 | 短信验证码 | 点「发送验证码」后收短信 |
| 3 | 控车码 + 是否保存 | 6 位数字，即 App 里的「控车码」。**不填也能读车况**，但无法控车 |

> 控车码只存在你自己的 HA 配置里（`core.config_entries`），不会外传，也不会写进日志。

## 实体与服务

命名规则：`<域>.qi_yuan_a07_<车架号后6位>_<中文名>`，例如：

```text
sensor.qi_yuan_a07_20c97d_sheng_yu_dian_liang      剩余电量
sensor.qi_yuan_a07_20c97d_sheng_yu_xu_hang         剩余续航
climate.qi_yuan_a07_20c97d_kong_diao               空调
lock.qi_yuan_a07_20c97d_che_men_suo                车门锁
cover.qi_yuan_a07_20c97d_hou_bei_xiang             后备箱
switch.qi_yuan_a07_20c97d_shao_bing_mo_shi         哨兵模式
select.qi_yuan_a07_20c97d_zhu_jia_zuo_yi_jia_re    主驾座椅加热
```

### 服务

| 服务 | 作用 |
| --- | --- |
| `changan_qiyuan.control` | 下发任意指令（高级用法） |
| `changan_qiyuan.check_pin` | 只校验控车码，不下发指令 |
| `changan_qiyuan.send_control_sms` | 发送控车校验短信 |
| `changan_qiyuan.authorize_control` | 用短信验证码换取授权（解决 1004 风控） |
| `changan_qiyuan.query_task` | 查询某条指令的车端执行状态 |
| `changan_qiyuan.refresh` | 立即刷新车况 |

### 示例：电量低于 30% 提醒插枪

```yaml
automation:
  - alias: 电量低于 30% 提醒插枪
    trigger:
      - platform: numeric_state
        entity_id: sensor.qi_yuan_a07_20c97d_sheng_yu_dian_liang
        below: 30
    action:
      - service: notify.mobile_app_你的手机
        data:
          message: >-
            电量只剩 {{ states('sensor.qi_yuan_a07_20c97d_sheng_yu_dian_liang') }}%，
            记得插枪
```

## 常见问题

<details>
<summary><b>实体显示「不可用 / unavailable」</b></summary>

授权过期。长安的 access token 只有 **1 小时**，集成会用 refresh token 自动续期；如果很多天没用、refresh token 也失效，重新走一次添加流程即可。
</details>

<details>
<summary><b>控车没反应 / 返回 1004</b></summary>

风控。先 `changan_qiyuan.send_control_sms` 收短信，再 `changan_qiyuan.authorize_control` 传入验证码；之后一段时间内不用再验证。
</details>

<details>
<summary><b>总里程显示「未知」</b></summary>

车端 T-BOX 里程字段溢出时会返回 `1677721.4 km`（App 里也一样）。集成判定超过 50 万公里为无效值并显示「未知」，避免污染统计；T-BOX 恢复或 4S 刷写后会自行正常。
</details>

<details>
<summary><b>空调设了 26℃ 但车里没反应</b></summary>

回读里 `airStatus` 恒为 1，不能用来判断开关；集成以 `frontReqWindLevel > 0`（有风量）作为「真的开着」的判据。另外远程空调默认 15 分钟后自动关闭。
</details>

<details>
<summary><b>寻车 / 开哨兵点不动</b></summary>

这类属于「深度唤醒」指令，车辆熄火休眠时云端会 90 秒左右超时（实测），不是配置问题；车辆唤醒状态下可正常使用。
</details>

<details>
<summary><b>安全吗？会伤车吗？</b></summary>

只调用官方 App 使用的同一套云端接口，行为与 App 等价，不改车机、不刷 ECU。但**任何远程控车都有风险**，请自行评估并遵守长安的用户协议。
</details>

## 已知限制

- 熄火一段时间后车辆深度休眠，部分指令（寻车、哨兵开启）成功率低
- 车窗只能「留缝 / 关窗」，不能任意百分比开合（官方接口限制）
- 座椅通风、风向、内循环等参数官方未开放
- 不同车型能力不同：集成按 `car/detail` 的能力表自动过滤，本车没有的功能不会出现

## 文档

- **[docs/用户手册.md](docs/用户手册.md)** —— 给非技术用户的图文步骤、常见问题、卡片安装
- **[docs/开发者文档.md](docs/开发者文档.md)** —— 协议细节、指令码与参数表、回读字段语义、错误码、侦察方法论（写成「换个人也能二次开发」的形式）\n- **[docs/发布指南.md](docs/发布指南.md)** —— 自己推 GitHub / 打 Release / 上 HACS 的逐步命令

## 许可

MIT。本项目与长安汽车无任何关联，为个人自用集成，使用风险自负。
