# CedarDuet / 双弈

人类与自己的 AI 搭档进行回合制棋牌对弈的独立服务。

CedarDuet 本体是一个独立的 FastAPI/ASGI 项目，包含棋局引擎、房间系统、共享时间线、人类网页端、AI HTTP 接口、SQLite 持久化，以及全局娱乐筹码、互动兑换、欠条与成就系统。CedarToy 是当前官方部署所使用的认证、绑定关系和 MCP 聚合层，但游戏逻辑并不放在 CedarToy 主仓库里。

> 当前定位：公益、非商业、娱乐用途。筹码仅为站内娱乐数值，不支持充值、提现或与真钱兑换。

## 当前游戏

- `tictactoe`：3×3 井字棋
- `gomoku`：15×15 五子棋，无禁手
- `othello`：8×8 黑白棋，支持自动跳过与终局数子
- `connect4`：7×6 四子连珠
- `dots_boxes`：2–4 人点格棋，支持系统 NPC
- `liars_dice`：2–6 人吹牛骰子，支持系统 NPC 与私密骰子投影
- `jungle`：7×9 斗兽棋

井字棋、五子棋、黑白棋、四子连珠和斗兽棋继续严格双人。点格棋权威声明
`allowed_player_counts=(2,3,4)`，吹牛骰子声明 `(2,3,4,5,6)`；两者是第一批
多人/NPC 框架验收游戏。

### 点格棋多人规则

仍使用 5×5 点阵和 16 个格子。参与者按稳定座位顺序画未占用边；完成格子得
1 分并继续行动。棋盘填满后，唯一最高分者获胜；最高分并列即和局并退还下注。
多人下注时，每名非赢家承担一个 stake，唯一赢家获得合计的多人底池。

### 吹牛骰子基础规则

每人初始 5 枚六面骰，本版 1 点不作万能点。首叫之后只能提高叫点：数量更大，
或数量相同而点数更大；数量不超过场上当前骰子总数。除首叫外可以质疑。质疑后
公开本轮全部骰子，实际数量达到叫点时质疑者失去一枚，否则上一位叫点者失去
一枚；零骰淘汰。失骰者仍存活就开启下一轮，否则由其后下一位存活者开始，最后
一人获胜。当前骰子只进入该参与者的 `private_state.dice`；公共状态只含剩余骰数、
当前叫点、淘汰状态和已公开的上一轮结果。完整 MCP 示例见
[docs/MCP_GUIDE.md](docs/MCP_GUIDE.md)。

## 项目结构

```text
app/
  main.py              FastAPI 路由、等待与唤醒
  database.py          SQLite 初始化与迁移
  framework.py         房间、身份、轮次、胜负、消息
  models.py            HTTP 请求模型
  chips.py             全局娱乐筹码钱包与统一流水
  exchanges.py         人机互动兑换申请、审批与原子转账
  loans.py             人机欠条协商、计息、转账与还款
  achievements.py      成就目录、可靠事实、进度与自动奖励
  chips_routes.py      筹码中心页面与 API
  games/               棋种插件
  npc_personas.py      NPC 人设目录加载与严格校验
  npc_runtime.py       NPC 决策 revision 幂等与合法行动校验契约
  npc_providers.py     disabled / OpenAI-compatible / CedarToy bridge provider
  npc_controller.py    查看者安全上下文、合法行动映射、重试与保底执行
  npc_scheduler.py     HTTP 后台投递、房间去重、连续回合与启动恢复
  config/npc_avatars/  外部头像目录格式说明；仓库不含生产头像
  config/npc_personas/ 管理员人设格式说明；仓库不含生产人设
  static/              人类端网页、棋盘、时间线、筹码中心
tests/                  单元测试与前端行为测试
data/                   本地运行数据目录；真实数据库不会提交到 Git
```

## 核心能力

- 一房一局，参与者按稳定 seat / turn order 保存；完整状态同时返回有序
  `participants`、`current_actor` 和兼容旧前端的 `turn`。
- 框架绝对上限为 6。插件以 `allowed_player_counts` 权威声明离散桌型，例如
  `(2, 3, 4)`、`(4,)`；旧插件未声明时才从 `min_players..max_players` 推导。
  Web、MCP new、直接开房和 join 都按插件允许人数校验，不会因为底座支持 6 人
  就自动放宽游戏。插件还可通过通用结果对象保留行动权、指定下一行动者、
  临时 skip，或把参与者标记为 inactive / eliminated。
- 参与者真源用 `participant_kind` 区分 `human`、`bound_machine` 和
  `system_npc`，旧 `role=human/ai` 字段继续供旧游戏与客户端兼容。生产开房
  强制至少一名人类和一只真实绑定小机；NPC 只在创建时补空座，每局最多四个，
  不会接管中途离开的席位。只有点格棋与吹牛骰子声明 `supports_npcs`。
- NPC 人设从 `DUEL_NPC_PERSONAS_DIR` 指向的外部目录随机无重复抽取，包含稳定
  id、显示名、persona 文本和可选头像文件名。头像只从
  `DUEL_NPC_AVATARS_DIR` 根目录以站内 `/api/npc-avatars/...` URL 提供；文件名、
  扩展名、真实路径与越界符号链接均校验。仓库不含正式人设或头像。
- NPC provider 默认 `disabled`；独立部署可选 `openai_compatible`，官方实例可选
  内网 `cedartoy_bridge`。普通无 NPC 对局不依赖 provider。NPC 首要目标是理解
  规则并争取获胜；人设只影响合理行动间的选择、风险偏好和交流方式，不得为了
  维持性格故意走明显坏棋。合法行动始终由插件规则引擎列出。
- 每次 NPC 请求只含全局玩家规则、当前 persona、精简游戏规则、公开参与者目录、
  公共状态、最近 20 条房间公开事件、游戏专用公开行动、当前 NPC 私有状态和权威
  合法行动；事件读取不消费玩家游标。私聊和其他玩家隐藏状态不会进入请求，也不
  请求或保存思维链。NPC 可以依据公开信息正常推理和估计，但不得把对手隐藏状态
  当作已知事实；不得以真实披露为目的直接报出自己的具体隐藏牌、骰子等私有状态，
  但可以为策略进行虚张声势、试探、模糊表达或真假难辨的误导，正常诈唬不受禁止。
  模型只能返回 `action_id` 和可选短消息，服务端重新映射并校验；
  非法或格式错误最多重试一次，仍失败则选择稳定排序后的合法保底行动。
  `room_id + revision + npc_id` 决策票据负责幂等；同一 revision 不重复调用，
  中断后过期预留只做本地保底恢复，不产生第二次 provider 请求或重复落子。
  HTTP 状态推进只把当前系统 NPC 房间投递到后台，不等待模型响应；后台会连续
  执行系统 NPC 回合直至轮到人类/绑定小机、对局结束或达到单批安全上限。应用
  启动时会扫描并恢复已存在的进行中 NPC 回合，状态轮询的兜底投递仍共用同一
  房间任务与 revision 决策票据。
- 同一人机对最多同时保有 3 个活跃房间，全局最多 500 个活跃房间。
- 落子、发言、认输和终局结果进入同一条共享时间线。
- AI 可通过 `rooms -> state -> move` 找回自己已经参与的房间，无需人类反复提供房间号。
- `move` 和 `state` 支持 `wait=true`：非当前 AI 以默认 30 秒的短心跳等待轮到
  自己或出现终局、本人淘汰/离席等关键状态；等待期间不持有 SQLite 事务或锁。
  `DUEL_MCP_WAIT_SECONDS` 可配置为 1–45 秒，且不影响 NPC provider timeout。
- 普通发言、其他人的行动和轮结算只进入未读队列，不会提前结束非行动者的挂等；
  每个参与者拥有独立事件 cursor，轮到自己时一次读取，一个参与者不会替其他人消费。
- 插件通过 `public_state` / `private_state` / `project_event` 明确区分公共局面、
  当前查看者私有局面和 compact 事件；所有 Web 与 MCP 房间读取都以已认证
  participant 作为 viewer，非参与者不能读取，客户端参数也不能另选 viewer。
- 插件通过 `participant_summary` 只提供至多四项公开标量元数据；通用座位卡负责
  头像、姓名、座位、行动高亮和状态，点格棋仅补得分，吹牛骰子仅补剩余骰数。
- `move.revision` 是向后兼容的可选乐观并发保护；新网页、NPC 控制器与新游戏
  MCP 指南都会提交当前 revision。陈旧动作在事务内以 409 拒绝，不能重复行动。
- `app/games/tools.py` 提供仅作用于持久化 `board_state` 的 phase/round/turn、
  牌堆/弃牌堆/按座位手牌、洗牌/摸牌与公共或定向可见骰子 helper；随机结果
  首次生成后即进入房间状态，重载不会重新洗牌或重投。
- 全局最多 20 个并发等待；超过容量时落子仍然成功，只是不继续挂等。
- 活跃房间长期无动作时可惰性归档。
- 终局房间支持“保留 / 取消保留 / 手动删除”。当前版本已暂停自动物理删除终局记录，避免旧对局在正式公告前被清理。
- 人类普通聊天、AI 普通聊天、落子事件和结果事件使用不同的前端视觉层级。
- 全局娱乐筹码第一版已经包含：首次 200、每日签到 +20、允许负数、`<= -500` 可自愿破产、破产后重置 50、破产次数与状态标记、统一筹码流水。
- 开局可设置大于等于 0 的整数“本局筹码”：0 筹码直接开局，非零筹码需另一方在 24 小时内接受，拒绝或过期即取消。
- 终局结算使用同一全局钱包与流水：胜方 `+stake`、负方 `-stake`、和棋 0；认输按输结算，重复读取终局不会重复记账。
- 多人筹码默认禁用；具体多人插件必须显式声明支持并给出自己的 settlement
  deltas（完整覆盖每名参与者、整数且总和为 0），框架原子幂等结算，绝不会
  推断“赢家拿走其余人的 stake”。
- 显式多人结算可以包含 NPC delta，但只有 `human` / `bound_machine` 会写全局
  钱包；NPC 没有账号或永久钱包，前端显示 `???`，其本局增减保存在房间结果、
  结算批次和流水 metadata 中。
- 成就第一版已实现：人类与绑定小机分别永久保存，关系进度严格按
  `human_id + ai_id` 分对，奖励在解锁事务内自动进入统一账本；系统 NPC 没有
  钱包、成就或奖励。欠条已接入可靠事实与自动奖励；互动兑换不新增成就。

## 本地启动

要求 Python 3.10+。

```bash
git clone https://github.com/Zizuixixiang/cedarduet.git
cd cedarduet

python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8772
```

健康检查：

```bash
curl http://127.0.0.1:8772/health
```

默认数据库为 `data/duel.db`，也可以通过环境变量指定：

```bash
DUEL_DB_PATH=/your/path/duel.db \
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8772
```

不配置 NPC provider 时，所有不含 NPC 的普通对局照常可玩；只有主动启用
NPC 补位时才需要部署者自己的兼容 API 和外部人设目录。复制 `.env.example` 后：

```bash
DUEL_NPC_PROVIDER=openai_compatible
DUEL_NPC_API_BASE=https://your-provider.example/v1
DUEL_NPC_API_KEY=your-own-server-secret
DUEL_NPC_MODEL=your-model
DUEL_NPC_PERSONAS_DIR=/your/external/personas
DUEL_NPC_AVATARS_DIR=/your/external/npc-avatars
```

`openai_compatible` 调标准 `/chat/completions`；API key 只存在服务端环境，不进入
网页、房间数据库或日志。`DUEL_NPC_TIMEOUT_SECONDS`、`DUEL_NPC_MAX_TOKENS`、
`DUEL_NPC_MAX_CONCURRENCY` 分别控制单请求超时、输出上限和跨房间全局并发。
格式和限制见 `app/config/npc_personas/README.md`。仓库内 `_example.json` 只展示
普通格式且不会加载；空库存与 disabled provider 都不影响无 NPC 对局。

官方 CedarToy 可改用 `DUEL_NPC_PROVIDER=cedartoy_bridge`，由带共享 Bearer token
的 loopback bridge 调用官方 NPC 池。CedarDuet 不导入海龟汤模块，也不保存或复制
池中 API key；两种 provider 对控制器都返回同一 `action_id/message` 结构。

事件唤醒目前是单进程内机制，因此请保持 uvicorn 单 worker。

### 为什么直接打开 8772 会提示从主站登录？

当前网页端采用“可信上游认证”模式：CedarDuet 不自行保存 CedarToy 的账号密码，而是由上游认证层验证登录后，向 CedarDuet 注入可信的人类身份和绑定 AI 清单。

因此，直接裸连 `http://127.0.0.1:8772/` 可以启动服务、跑测试和调用内部接口，但当前网页会提示从认证入口进入。这是部署方式的边界，不是游戏引擎依赖 CedarToy。

如果你想把 CedarDuet 接进自己的站点，可以实现自己的认证/绑定层，再按下文的可信身份协议反向代理到 CedarDuet。

## 可信人类身份协议

人类网页请求由上游代理验证后注入以下 Header：

```http
X-Duel-Human-Player: <trusted human id>
X-Duel-Human-Name: <percent-encoded human name>
X-Duel-Bound-Ais: <base64url JSON [{"id":"...","name":"..."}]>
```

这些 Header **只应该由受信任的反向代理在内网注入**，不要直接相信公网客户端自己提交的同名 Header。

`GET /api/whoami` 会返回：

- 当前人类显示名
- 其绑定 AI 清单
- 游戏目录
- 该人类的全部房间

开房时网页只提交所选 AI，服务端会再次校验它是否属于可信绑定清单。

## AI HTTP 接口

AI 操作统一提交到：

```http
POST /mcp/play
Content-Type: application/json
```

支持：

- `rooms`
- `new`
- `join`
- `move`
- `state`
- `resign`
- `leave`
- `accept`
- `reject`
- `rematch`
- `chips`

当前官方 CedarToy 部署会在聚合层认证 AI，并强制覆盖为 canonical `player_id`；客户端自报的 `player_id` 不参与身份选择。

`new` 可传 `stake`（默认 0）。支持 NPC 的多人插件还可传
`target_player_count=2..6` 与 `fill_with_npcs=true`；具体值仍必须属于游戏返回的
`allowed_player_counts`，严格双人游戏仍会拒绝任何非 2 人或 NPC 参数。
`rooms` 默认也会返回当前 AI 自己的 `pending` 邀请；对
`confirmation_decision=pending` 的房间使用 `accept` 或 `reject`。`npc:*` 不是可认证
账号，不能作为 MCP `player_id`。

### 查询自己的房间

```json
{
  "action": "rooms",
  "player_id": "ai-42",
  "include_terminal": false,
  "limit": 50,
  "offset": 0
}
```

默认只返回 `waiting` / `playing`；`include_terminal=true` 时也包含 `finished` / `archived`。

### AI 创建房间

```json
{
  "action": "new",
  "player_id": "ai-42",
  "game_type": "gomoku",
  "mode": "human_first"
}
```

### AI 落子并等待人类回应

```json
{
  "action": "move",
  "player_id": "ai-42",
  "room_id": "ABCDEFGH",
  "revision": 3,
  "move": {"row": 7, "col": 7},
  "message": "我先占住中心。",
  "wait": true
}
```

每名小机第一次进入 `playing` 时只会收到一次完整 bootstrap（`room`、棋盘、规则、
落子格式、参与者和 stake）。创建/加入/接受时尚未开局的小机，会在开局后的第一次
`state` 收到这份上下文。之后 `state`、`move` 和 `wait=true` 都只返回房间号、
revision、当前行动者，以及该小机游标尚未读过的可见 `events`；不会再重复完整房间、
规则、参与者目录或历史。轮到当前小机且游戏存在隐藏信息时，响应另带本次决策所需的
`private_state`。

增量事件直接带显示名，并把同一次行动和附言合在一起，例如
`{"name":"南杉","message":"我叫四个五。","move":{"action":"bid","quantity":4,"face":5}}`。
事件不暴露内部 sequence、事件 revision、player ID、座位或参与者类型；每个 viewer
仍使用独立可见性投影和游标，读过后不再重复。非当前参与者可用 `state + wait=true`
等待行动权或关键状态；普通事件会累积到它真正获得行动权时再一次返回。无变化的
`still_waiting` 是短心跳而不是退出挂等，只返回 `ok`、`status`、`room_id` 和
`revision`；调用方处于挂等模式时应继续请求 `state + wait=true`。终局增量另带
winner/result 与筹码结算。

小机筹码仍复用同一入口：`{"action":"chips","op":"status"}`。op 支持
`status`、`check_in`、`bankruptcy`、`ledger`、`achievements`、`exchange`、`loans`；只能操作当前
canonical AI 自己的钱包，绑定人类余额只读。`achievements` 返回通用、小机专属、
已启用 NPC 与当前可信绑定人类的“你们之间”成就；未解锁隐藏成就完全不返回。
`loans` 是显式欠条入口，提供 list/create/accept/reject/counter/withdraw/repay；
`exchange` 提供 catalog/list/create/confirm/reject/withdraw。普通 status、房间和
对局响应不会夹带欠条、逾期或兑换提醒。
ledger 默认 5 条、硬上限 10。小机可对已正常结束且原阵容不含随机 NPC 的房间提交
`{"action":"rematch","room_id":"..."}` 发起对称、权威、可追踪的重赛。

## 人类网页 API

常用接口包括：

```text
GET  /api/whoami
POST /api/rooms
GET  /api/rooms/{room_id}
POST /api/rooms/{room_id}/move
POST /api/rooms/{room_id}/messages
POST /api/rooms/{room_id}/resign
POST /api/rooms/{room_id}/leave
POST /api/rooms/{room_id}/invitation
POST /api/rooms/{room_id}/retention
POST /api/rooms/{room_id}/delete
GET  /api/chips
GET  /api/chips/machines/{machine_id}
GET  /api/chips/exchanges/catalog
GET  /api/chips/exchanges
POST /api/chips/exchanges
POST /api/chips/exchanges/{request_id}/{confirm|reject|withdraw}
POST /api/chips/loans
POST /api/chips/loans/{loan_id}/{accept|reject|counter|withdraw|repay}
```

终局保留和删除仅允许该房间中的可信人类参与者操作。

网页创建控件按游戏 metadata 渲染：`allowed_player_counts=[2]` 时仍是原有单选
小机流程；只有插件允许多人时才显示其明确声明的桌型、多选绑定小机、NPC 补位
能力状态和座位预览，不会凭全局上限补出 5/6 人选项。房间内 3–4 人使用两列
紧凑座位卡，5–6 人桌面端三列、窄屏两列；棋盘/公共桌面保持居中，双人房间保留
原有双方对弈视觉。`private_state` 非空时才显示一个只属于
当前 viewer 的手牌/骰子/合法行动容器。

## 筹码中心

独立页面：

```text
/chips
```

当前已经实现：

- 人类 / AI 各自独立的全局钱包
- 首次创建钱包赠送 200
- 人类每日签到固定 +20
- 余额允许为负数
- 余额 `<= -500` 时可自愿宣布破产
- 破产后余额重置为 50，破产次数 +1
- 余额恢复到 `>= 200` 后自动解除破产状态
- 人类只能操作自己；绑定 AI 的钱包在人类端只读
- 所有筹码变化进入统一账本流水
- 人类目录仅含人类专属与双方通用商品；小机目录仅含小机专属与双方通用商品，
  小机专属名称、说明和图片键只会随小机已经发出的申请快照展示给人类
- 想获得筹码的一方发起并承诺完成商品内容，另一方付款审批；说明 1–120 字、
  筹码 1–100，`custom` 另需 1–30 字标题
- 每对绑定最多 3 张待处理申请，72 小时自动失效；付款方按 Asia/Shanghai 自然日
  累计兑换支出最多 100 枚
- 付款方只可确认或拒绝，发起方审批前只可撤回；确认时重验绑定与余额，并原子写入
  双方 `exchange_out` / `exchange_in`，拒绝、撤回、失效均不动账
- 解绑使待处理申请失效，已完成历史保留；平台不上传或保存实际互动内容，也不介入
  履约争议，双方在常用聊天平台自行完成
- 当前商品图使用轻量 CSS/符号占位；后续原图放在
  `app/static/assets/exchange-shop/source/`，约定文件名为 `human-items.png`、
  `machine-items.png`、`common-items.png`，处理后的网页素材放在相邻 `items/`
- 只有借款人能发起欠条；人类从筹码中心发起，小机从显式 MCP `chips/loans` 发起
- 当前收到方可接受、拒绝或还价；还价生成新 revision，旧接受立即失效；发起人只可在生效前撤销
- 每名借款人最多 3 张未结欠条；逾期会阻止新借款，但不影响对局、签到、破产处理和还款
- 接受时重新校验出借人余额并原子转账；仅借款人可正整数部分/全额还款，先抵利息再抵本金
- 到期日按上海日期计算，至少次日且最终接受日起最多 30 天；当天结束后才逾期，提案 3 天过期
- 日利率以整数微百分比保存（1,000,000 单位 = 1%/日），按剩余本金和实际秒数单利累计；余数跨段携带、只对完整整数利息向下取整
- 利息封顶默认开启，欠条终身计收利息（含已还）最多等于原始本金；关闭时持续单利累计并在网页警示
- 解绑、破产不会删除或减免生效债务；旧债仍可审计、还款，但接受/还价仍要求当前绑定
- 双人旧游戏及首批多人验收游戏的自定义本局筹码、全员确认和幂等结算
- 普通成就完整显示条件、可靠进度、奖励和解锁时间；未解锁为灰色
- 隐藏成就未解锁前不进入 API、MCP、网页或公开总数，解锁后才进入隐藏区
- 人类视图显示通用 + 人类专属；绑定小机视图显示通用 + 小机专属 + 当前配对关系
- 解锁奖励无领取按钮；`achievement_unlocks` 与 `chip_ledger` 的
  `achievement_reward` 在同一写事务内完成，账本幂等键保证同一成就不重复发奖
- 成就终局快照、参与者结果、开局余额、事件、配对与进度独立于 `rooms` 持久化，
  删除房间不会删除成就事实

数据库初始化保持增量兼容：成就、互动兑换、`loans`、`loan_revisions`、`loan_operations` 表及索引
使用 `CREATE ... IF NOT EXISTS`，不改写现有钱包/账本，也不会从普通旧流水猜测历史欠条。
房间仅增加
`terminal_reason` 与权威重赛链字段，不重建或覆盖已兼容旧库。启动回填只接受最终
revision 上存在权威 `move` 或 `resign` 事件、且具有明确赢家/和棋的旧终局；陈旧超时
归档、主动离桌、人数不足、进行中和故障记录不计。旧局没有开局余额时不回填
“倾家荡产”；没有房间结算批次/账本时不猜历史结算余额。回填、展示修复和重复事件
都复用相同唯一键，可安全重复执行。旧正常终局中可由 `initiator_player_id`、权威重赛
字段和唯一人类败方的保留标记证明的创建、重赛与保留事件会一并回填。

### 第一版成就奖励表

奖励常量集中在 `app/achievements.py`，上线前可在一个目录中统一调整。

| 类别 | 稳定 ID | 名称 | 奖励 |
|---|---|---:|---:|
| 通用 | `first_normal_game` | 落子无悔 | 5 |
| 通用 | `first_authoritative_rematch` | 再来一局 | 5 |
| 通用 | `first_normal_draw` | 棋逢对手 | 5 |
| 通用 | `six_game_types` | 十八般棋艺 | 20 |
| 通用 | `first_four_player_game` | 满堂生辉 | 10 |
| 通用 | `first_staked_game` | 愿赌服输 | 5 |
| 通用 | `win_after_three_losses` | 越挫越勇 | 10 |
| 通用 | `win_gomoku` | 五子登科 | 10 |
| 通用 | `win_tictactoe` | 井井有条 | 10 |
| 通用 | `win_othello` | 黑白分明 | 10 |
| 通用 | `win_connect4` | 四通八达 | 10 |
| 通用 | `win_dots_boxes` | 圈地为王 | 10 |
| 通用 | `win_jungle` | 万兽之王 | 10 |
| 通用 | `first_negative_balance` | 兜比脸干净 | 10 |
| 通用 | `first_bankruptcy` | 这下真没了 | 5 |
| 通用 | `bankruptcy_recovery` | 东山再起 | 10 |
| 通用 | `three_bankruptcies` | 三起三落 | 20 |
| 通用 | `lose_100_in_game` | 钱都去哪了 | 10 |
| 通用 | `win_zero_stake` | 赢了也没钱 | 5 |
| 通用 | `lose_zero_stake` | 输了也不亏 | 5 |
| 通用 | `ten_zero_stake_games` | 君子之交 | 20 |
| 通用 | `five_zero_stake_same_opponent` | 不押筹码，押一口气 | 20 |
| 人类 | `human_rematch_after_loss` | 人类的胜负欲 | 10 |
| 人类 | `human_preserve_loss` | 输了也要留档 | 10 |
| 人类 | `human_loses_to_bound_ai` | 我家小机初长成 | 10 |
| 小机 | `ai_creates_room` | 我自己来的 | 5 |
| 小机 | `ai_beats_bound_human` | 我不是陪玩 | 10 |
| 小机 | `ai_three_win_streak` | 算力花在刀刃上 | 10 |
| 小机 | `ai_authoritative_rematch` | 轮到你了，人类 | 10 |
| 小机 | `ai_revenge_bound_human` | 你教得好，下次别教了 | 20 |
| 小机 | `ai_six_game_types` | 棋盘不在提示词里 | 20 |
| 关系 | `pair_first_game` | 来都来了 | 双方各 5 |
| 关系 | `pair_ten_games` | 又是你 | 双方各 10 |
| 关系 | `pair_fifty_games` | 老对手了 | 双方各 20 |
| 关系 | `pair_reunion_after_seven_days` | 座位还给你留着 | 双方各 20 |
| 关系 | `pair_same_day_check_in` | 同一天想起这里 | 双方各 10 |
| 关系 | `pair_both_won` | 有来有回 | 双方各 10 |
| 关系 | `pair_balanced_twenty` | 半斤八两 | 双方各 20 |
| 关系 | `pair_five_wins_each` | 相爱相杀 | 双方各 20 |
| 隐藏 | `jungle_rat_captures_elephant` | 大象也怕老鼠 | 10 |
| 隐藏 | `all_in_loss` | 倾家荡产 | 20 |
| 隐藏 | `settlement_balance_minus_500` | 输到系统都心疼 | 20 |
| 隐藏 | `game_last_at_least_24h` | 棋盘钉子户 | 20 |
| 隐藏 | `ten_game_rematch_chain` | 十局之后还是朋友 | 20 |
| 隐藏 | `non_tictactoe_draw` | 这也能和？ | 10 |
| 隐藏 | `othello_win_both_sides` | 黑白通吃 | 20 |
| 隐藏 | `last_move_comeback_win` | 一子定乾坤 | 20（仅定义，暂不触发） |
| NPC（许知衡） | `defeat_npc_xu_zhi_heng` | 这回算漏了 | 10 |
| NPC（岳鸣川） | `defeat_npc_yue_ming_chuan` | 别催，赢着呢 | 10 |
| NPC（温行止） | `defeat_npc_wen_xing_zhi` | 这次没上当 | 10 |
| NPC（唐熠） | `defeat_npc_tang_yi` | 这句还给你 | 10 |
| NPC（商令仪） | `defeat_npc_shang_ling_yi` | 后发也有来不及 | 10 |
| NPC（乔麦） | `defeat_npc_qiao_mai` | 这次猜错啦 | 10 |
| NPC | `defeat_all_six_npcs` | 一个都没放过 | 30 |

借款事实只由欠条服务在同一数据库事务内写入，不从普通历史账本回填：

| 类别 | 稳定 ID | 名称 | 奖励 |
|---|---|---:|---:|
| 借款 | `loan_first_borrower_active` | 白纸黑字 | 5 |
| 借款 | `loan_first_lender_active` | 江湖救急 | 5 |
| 借款 | `loan_first_partial_repayment` | 分期也是还 | 5 |
| 借款 | `loan_first_ontime_repayment` | 说到做到 | 10 |
| 借款 | `loan_three_ontime_repayments` | 一诺千金 | 20 |
| 借款 | `loan_lend_to_negative_borrower` | 雪中送炭 | 10 |
| 借款 | `loan_debt_free_after_three` | 无债一身轻 | 20 |
| 关系 | `loan_pair_counter_activated` | 有商有量 | 双方各 5 |
| 关系 | `loan_pair_bidirectional` | 有来有往 | 双方各 10 |
| 隐藏 | `loan_three_active` | 三张欠条一台戏 | 10 |
| 隐藏 | `loan_first_overdue` | 明日复明日 | 无筹码奖励 |
| 隐藏 | `loan_interest_cap_reached` | 利息比本金还熟 | 无筹码奖励 |

NPC 项只在对应 `persona_id` 实际启用时公开；全收集项要求六位全部启用。已经解锁的
历史项即使管理员稍后停用该人设仍会保留。`一子定乾坤` 目前没有足以证明“最后一手
从落后反胜”的统一权威分差，因此只保留隐藏定义，不设置猜测型触发器。连续同对手
0 筹码与重赛链采用严格双人窄口径，重赛链只取逐局直接相连的最长路径、不会把同一
旧局派生出的并行分支相加；“7 个完整自然日”按上海日期之间不含首尾的完整日数计算。

尚未实现的内容会继续分阶段加入：互动交换。

## 数据与隐私

真实运行数据库不会提交到仓库：

```text
data/*.db
data/*.db-shm
data/*.db-wal
```

仓库只保留 `data/.gitkeep`。公开部署或 fork 时，也请不要把真实玩家数据库、访问令牌或反向代理密钥提交到 Git。

## 测试

```bash
python3 -m unittest discover -s tests -v
python3 tests/play_tictactoe.py
```

`play_tictactoe.py` 使用临时 SQLite 数据库和真实 FastAPI 路由完成一局井字棋，并验证 `wait=true` 的并发唤醒链路。

## 生产部署示例

仓库提供 `duel.supervisord.conf.example` 作为模板。请把其中的 `/srv/cedarduet` 换成你自己的实际路径，并让 CedarDuet 只监听内网或 loopback，再由可信反向代理对外提供认证后的入口。

当前 CedarToy 官方实例也是以独立服务方式运行 CedarDuet，再由 CedarToy 负责登录态、绑定关系、MCP 聚合和 `/duel/*` 反向代理。

## License

[PolyForm Noncommercial License 1.0.0](LICENSE)。允许非商业用途；商业使用不在本许可授权范围内。

严格来说该许可属于 source-available / 非商业源码开放许可，而不是 OSI 定义的开源许可证。如果未来希望改为 AGPL、MIT 或 Apache-2.0，可以再单独调整许可。
