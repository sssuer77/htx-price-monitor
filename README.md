# HTX 合约自然语言价格监控

一句话盯盘：输入 **「BTC 跌破 83000 提醒我」**，程序自动把它变成监控规则，
本地实时盯 HTX（火币）USDT 本位永续合约，命中后立刻通过界面 / 声音 / Windows 系统通知 / Webhook 通知你。

- 只做**合约**行情（HTX `linear-swap`，即 USDT 本位永续），**不涉及现货、不下单、不需要 API Key**。
- **零第三方依赖**，只用 Python 标准库（3.9+ 可用）。
- 设计原则是「**Agent 写规则，引擎跑规则**」：自然语言解析只在建规则时跑一次，
  实时判断交给本地确定性状态机，因此**没有幻觉、没有额外网络延迟**。
- **桌面悬浮球**：常驻置顶、可拖动，实时显示行情，有提醒就弹气泡——不用一直开着浏览器。

---

## 快速开始

```bat
:: 方式一：双击运行
run.bat

:: 方式二：命令行
cd /d D:\Programming\_Object\htx-price-monitor
python -m htxmon
```

启动后浏览器会自动打开 <http://127.0.0.1:8971/>，在顶部输入框写一句话，回车即可。
桌面右下角同时会出现一个**悬浮球**，实时显示行情涨跌，有提醒时会弹气泡并亮起角标。

首次使用建议先做连通性自检：

```bat
python -m htxmon --check
```

输出示例：

```
[证书] 根证书 150 个，校验=开启 来源=D:\Git\mingw64\etc\ssl\certs\ca-bundle.crt
[REST] BTC-USDT 最新价 84465.8，往返 541ms
[WS]   wss://api.hbdm.com/linear-swap-ws 订阅成功，312ms 内收到首条推送
```

WS 被防火墙拦截也不影响使用——程序会自动降级为 REST 轮询。

---

## 自然语言语法

解析器是纯正则实现，不需要联网、不需要大模型。下面是**实测通过**的写法：

| 你说的话 | 解析结果 |
| --- | --- |
| `BTC 跌破 83000 提醒我` | `cross_down` 下穿 83000 |
| `比特币涨到 86000 叫我` | `cross_up` 上穿 86000 |
| `BTC 站上 86000 通知` | `cross_up` 上穿 86000 |
| `BTC 高于 90000 每次提醒我` | `above` 高于 90000（可重复） |
| `ETH 5分钟涨2% 提醒` | `pct_up` 5 分钟内涨幅 ≥ 2% |
| `ETH 15分钟跌3% 通知我` | `pct_down` 15 分钟内跌幅 ≥ 3% |
| `BTC 5分钟振幅超过1.5% 提醒` | `pct_abs` 5 分钟内振幅 ≥ 1.5% |
| `sol 在 120 到 122.5 之间提醒我` | `in_range` 进入 120 ~ 122.5 |
| `BTC 回到 84000-86000 提醒我` | `in_range` 重新进入区间 |
| `BTC 突破 83000-85000 区间` | `out_range` 突破区间 |
| `BTC 跌出 84000-86000 提醒` | `out_range` 离开区间 |
| `ETH 接近 2700 提醒` | `near` 靠近 2700（默认 0.5% 内） |
| `BTC 距离 85000 0.3% 提醒` | `near` 靠近 85000（0.3% 内） |
| `BTC 触及 85000` | `touch` 触及 85000 |
| `BTC 突破 87000 或 跌破 83000` | 拆成 **2 条**规则，后一句自动沿用币种 |
| `BTC 8.45万跌破提醒` | 支持 `万 / k / 千 / 亿` 单位 → 84500 |
| `狗狗币 跌到 0.05` | 中文别名识别 → `DOGE-USDT` |
| `牛来 跌破 0.1 提醒我` | HTX 的中文合约代码 → `牛来-USDT` |
| `今天天气不错` | 正确报错：没识别出币种 |

### 币种写法

支持代码、中文名、圈内俗称，大小写不敏感：

```
BTC / 比特币 / 大饼 / bitcoin / xbt      ETH / 以太坊 / 二饼
SOL / 索拉纳        DOGE / 狗狗币 / 狗币     XRP / 瑞波
XMR / 门罗币        LTC / 莱特币           ZEC / 大零币
AVAX / 雪崩         DOT / 波卡              LINK / 链环
... 共 60+ 个币种，见 htxmon/symbols.py
```

不在表里的币种可以直接写全称，如 `PEPE-USDT`。

**HTX 有一部分合约直接用汉字当代码**（链上迷因币），例如这几个（都是实测存在的真合约）：

```
牛来-USDT   哈基米-USDT   币安人生-USDT   龙虾-USDT
```

写中文名也行，不用带 `-USDT`：

```
牛来 跌破 0.1 提醒我        哈基米 涨到 0.05 提醒
牛来-USDT 跌破 0.1          龙虾 突破 0.08 每次提醒
```

只要合约在「实时行情」列表里，它的中文名就能直接用来建规则，
包括别名表里没收录的新币。

### 时间周期

`秒 / s`、`分钟 / 分 / m`、`小时 / h`、`天 / d` 均可，英文（`5min`、`2h`）也认。
不写周期时默认按 **5 分钟**处理（会给出提示）。

### 重复提醒

- 默认 **只提醒一次**，触发后规则自动标记为「已触发」。
- 加上 `每次 / 重复 / 一直提醒` 会变成**可重复**规则，默认冷却 300 秒，避免刷屏。

---

## 界面功能

- **实时行情**：每个合约显示最新价、24h 涨跌幅，以及当前数据来源（WS / REST）；
  右上角 **×** 可把该合约移出监控（会提示同时删除它的几条规则）。
  刚加入、还没拿到行情的合约会显示成虚线「等待」卡片，不会让你以为添加失败了。
- **监控规则**：可暂停 / 恢复、删除、一键清理已触发规则。
- **提醒记录**：最近 200 条事件（含规则、触发价、通知通道结果）。
- **状态灯**：界面(SSE) / WS / REST / 延迟 / 证书，一眼看出链路是否健康。
- **设置面板**：开关 WS、轮询间隔、各类提醒通道、Webhook 地址、LLM 兜底解析。
- **一键测试提醒**：验证声音 / 系统通知 / Webhook 是否真的通。

浏览器通知需要点一次「开启浏览器通知」授权。

---

## 桌面悬浮球

启动后会有一个圆形小球常驻桌面，**始终置顶、不会被别的窗口盖住**。

| 操作 | 效果 |
| --- | --- |
| 左键拖动 | 移动位置（自动记住，下次启动回到原处） |
| 左键单击 | 打开控制台网页；若正在弹气泡，则先收起气泡 |
| 右键 | 菜单：打开控制台 / 清除角标 / 声音提醒开关 / 退出程序 |
| 有提醒时 | 球体边框变琥珀色、右上角出现红色未读角标、旁边弹出气泡显示完整提醒内容 |

球面上显示的是最近一次提醒涉及的币种（没有提醒时是监控列表的第一个），
中间是 24h 涨跌幅（涨绿跌红），下面是现价。

- 想关掉悬浮球：`python -m htxmon --no-ball`，或在设置面板取消「启动时显示桌面悬浮球」。
- 只想要桌面提醒、不要浏览器弹窗：在设置面板关掉「界面提醒」，保留「桌面悬浮球提醒」。
- 悬浮球依赖 Tkinter（Python 自带）。若当前 Python 没装 Tkinter，程序会打印一行提示并继续运行，
  其它功能不受影响。
- Linux / macOS 上会用半透明方块代替圆形（`-transparentcolor` 是 Windows 特性）。

---

## 提醒通道

| 通道 | 说明 |
| --- | --- |
| `ui` | 界面弹窗 + 提醒记录（通过 SSE 实时推送） |
| `ball` | 桌面悬浮球弹气泡 + 未读角标 |
| `sound` | Windows `winsound.Beep` 三声，1 秒内自动去重 |
| `toast` | Windows 系统通知中心横幅（PowerShell 调用 WinRT） |
| `webhook` | POST 到指定 URL，支持企业微信 / 钉钉 / 飞书 / Server酱 / Telegram / 通用 JSON |

`webhook_kind` 可选值：`generic`（默认）、`wecom`、`dingtalk`、`feishu`、`serverchan`、`telegram`。

注意：企业微信、钉钉、飞书、Server酱的机器人地址里自带密钥，**不要提交到 git**。

---

## 配置文件

首次运行会在项目根目录生成 `config.json`（已加入 `.gitignore`），
默认值参考 `config.example.json`：

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `host` / `port` | `127.0.0.1` / `8971` | 本地控制台监听地址 |
| `open_browser` | `true` | 启动时自动打开浏览器 |
| `symbols` | `["BTC-USDT","ETH-USDT","SOL-USDT"]` | 开机监控的合约列表 |
| `enable_ws` | `true` | WebSocket 实时推送（主通道） |
| `poll_interval_sec` | `3.0` | REST 轮询间隔（WS 正常时仅作兜底） |
| `history_bootstrap_min` | `120` | 启动时回补多少分钟 K 线，供涨跌幅规则使用 |
| `tls_verify` | `true` | 证书校验，**不建议关闭** |
| `notify.*` | 见上 | 各提醒通道开关与 Webhook 配置 |
| `llm.enabled` | `false` | LLM 兜底解析，仅在本地解析失败时调用 |
| `ball.enabled` | `true` | 启动时是否显示桌面悬浮球 |
| `ball.size` | `72` | 悬浮球直径（像素，48~160） |
| `ball.x` / `ball.y` | `null` | 悬浮球位置，拖动后自动写入；`null` = 默认在屏幕右侧 |
| `ball.bubble_sec` | `8` | 提醒气泡停留秒数 |

---

## 命令行

```bat
python -m htxmon                      :: 启动控制台
python -m htxmon --port 9000          :: 换端口
python -m htxmon --no-browser         :: 不自动开浏览器
python -m htxmon --no-ws              :: 强制只用 REST 轮询
python -m htxmon --no-ball            :: 不显示桌面悬浮球
python -m htxmon --check              :: 证书 / REST / WS 连通性自检
python -m htxmon --parse "BTC 跌破 83000 提醒我"   :: 只解析不启动，用于验证语法
python -m htxmon --version
```

跑单元测试：

```bat
python -m unittest discover -s tests
```

---

## HTTP API

本地控制台同时是一个 HTTP 服务，方便脚本调用：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/state` | 全量快照：行情、规则、事件、链路状态、配置 |
| GET | `/api/stream` | SSE 实时流（`tick` / `alert` / `rule` 事件） |
| POST | `/api/parse` | `{"text":"BTC 跌破 83000 提醒我"}` 解析并入库 |
| POST | `/api/rule/delete` | `{"id":"r_xxx"}` 删除规则 |
| POST | `/api/rule/toggle` | `{"id":"r_xxx"}` 暂停 / 恢复 |
| POST | `/api/rules/clear` | `{"status":"triggered"}` 清理规则 |
| POST | `/api/symbol/add` | `{"symbol":"DOGE-USDT"}` 加入监控 |
| POST | `/api/symbol/remove` | `{"symbol":"DOGE-USDT","purge_rules":true}` 移出监控（默认同时删掉该币种的规则） |
| POST | `/api/config` | 更新配置（WS、轮询、通道、LLM） |
| POST | `/api/test-notify` | 发一条测试提醒，返回各通道结果 |

---

## 工作原理

### 数据链路

```
HTX linear-swap WS (market.<合约>.trade.detail)  ──┐
                                                   ├─→ PriceFeed ─→ RuleEngine ─→ Notifier
HTX REST (detail/merged 轮询，WS 断开时兜底)      ──┘        │
启动时回补 120 分钟 1min K 线（供涨跌幅规则使用）           └─→ 内存价格环形缓冲
```

- WS 走 `wss://api.hbdm.com/linear-swap-ws`，发送 `{"sub":"market.BTC-USDT.trade.detail"}`，心跳 `{"ping": ms}`。
- WS 客户端是**纯标准库实现**（socket + ssl + 手写握手/分帧），不依赖 websocket-client。
- 断线自动重连，重连期间 REST 轮询接管，恢复后自动切回。
- 启动时会做**时钟偏移校正**，让「5 分钟涨 2%」这类窗口判定不受本机时钟影响。

### 规则状态机

所有价位型规则都归一到「位置量」`pos`：价格在价位上方 `1`、贴着 `0`、下方 `-1`。
穿越判定基于 `pos` 的跳变，因此：

- **穿越型**（`cross_up` / `cross_down` / `touch` / `out_range`）：建立规则时只记录基准，**绝不立即触发**，
  必须先到过对面再穿回来才提醒，避免「刚建好就误报」。
- **状态型**（`above` / `below` / `near` / `in_range` / `pct_*`）：问的是「现在是不是」，
  若创建时条件已成立，**立即提醒一次**。

---

## 目录结构

```
htx-price-monitor/
├─ run.bat              Windows 启动脚本（自动寻找 Python）
├─ config.example.json  配置模板
├─ htxmon/
│  ├─ __main__.py       CLI 入口
│  ├─ parser.py         自然语言 → 规则（纯正则）
│  ├─ symbols.py        币种别名表
│  ├─ models.py         Rule 数据模型、11 种规则类型
│  ├─ engine.py         规则状态机与触发
│  ├─ feed.py           行情源：WS 主通道 + REST 兜底 + 历史回补
│  ├─ wsclient.py       纯标准库 WebSocket 客户端
│  ├─ netutil.py        CA 证书自动发现 + HTTP 工具
│  ├─ store.py          规则持久化 + 事件日志
│  ├─ notify.py         界面 / 声音 / 系统通知 / Webhook
│  ├─ ball.py           桌面悬浮球（Tkinter，独立线程）
│  ├─ server.py         HTTP 服务 + SSE
│  ├─ llm.py            可选 LLM 兜底解析
│  └─ web/              控制台前端（原生 HTML/CSS/JS）
├─ tests/               单元测试
└─ data/                运行时数据（rules.json / events.jsonl，已 gitignore）
```

---

## 故障排查

| 现象 | 原因与处理 |
| --- | --- |
| `CERTIFICATE_VERIFY_FAILED` | 该 Python 没带 CA 证书。`netutil.py` 会自动从系统证书库 / certifi / Windows 证书存储三级兜底；仍失败可跑 `--check` 看证书来源 |
| WS 一直显示断开 | 防火墙或代理拦截了 `wss://api.hbdm.com`。程序自动降级 REST 轮询，不影响监控，只是延迟略高 |
| 状态灯「证书」是红色 | 当前 Python 环境没有根证书，见第一条 |
| 没有声音 / 系统通知 | 点「测试提醒」看各通道返回；Windows 勿扰模式会吞掉 Toast |
| 中文显示成乱码 | 控制台执行 `chcp 65001`；`run.bat` 已内置 |
| 规则一直不触发 | 看一眼规则卡片上的「就绪说明」，穿越型规则需要价格先到过对面 |
| 端口被占用 | `python -m htxmon --port 9000` |
| 悬浮球没出现 | 当前 Python 没装 Tkinter（启动时会打印提示）、加了 `--no-ball`，或设置里关了「启动时显示桌面悬浮球」 |
| 悬浮球挡窗口 | 直接拖走，位置会自动记住；想彻底关掉用 `--no-ball` |
| 移出合约后又想加回来 | 在「实时行情」下方输入框写 `DOGE` 或 `DOGE-USDT` 即可，即时生效、无需重启 |
| 加合约报「不是合法的合约代码」 | 写法是「币种-USDT」，中英文都行（`DOGE-USDT`、`牛来-USDT`）。报错说明混进了空格以外的符号、代码过长，或只有 `-USDT` 没有币种 |
| 规则建好了却一直不触发，就绪说明写着「等待行情…」 | 该合约不在「实时行情」列表里，拿不到价格。建规则时如果有这条提示，先去「实时行情」把合约加进来 |
| 加合约报「HTX 没有 XXX-USDT 这个永续合约」 | 代码拼错了，或 HTX 没上这个合约。可在 HTX 官网合约列表里核对 |
| 合约加了但一直没有行情 | 卡片会显示成虚线「等待」并给出拉取失败原因。网络正常却一直拉不到，说明合约代码有问题 |

---

## 关于实盘

本工具**只做行情监控，不连接账户、不下单**，所有提醒都需要你自己去交易所手动操作。
这样设计的好处是：**不需要 API Key，没有密钥泄露风险，也不会有程序误下单的可能**。

如果你后面想接自动交易，建议保持同样的分层：
**监控层（本项目）→ 决策层 → 执行层**，执行层单独跑、单独管密钥、单独限流。

---

## 免责声明

本项目仅用于行情监控与技术学习，所有输出不构成任何投资建议。
加密货币合约交易具有高杠杆、高风险特性，可能导致本金全部损失，请自行承担交易决策的全部后果。
