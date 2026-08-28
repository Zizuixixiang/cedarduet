# CedarDuet / 双弈

人类与自己的 AI 搭档进行回合制棋牌对弈的独立服务。

CedarDuet 本体是一个独立的 FastAPI/ASGI 项目，包含棋局引擎、房间系统、共享时间线、人类网页端、AI HTTP 接口、SQLite 持久化，以及正在建设中的全局娱乐筹码系统。CedarToy 是当前官方部署所使用的认证、绑定关系和 MCP 聚合层，但游戏逻辑并不放在 CedarToy 主仓库里。

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
  chips_routes.py      筹码中心页面与 API
  games/               棋种插件
  npc_personas.py      NPC 人设目录加载与严格校验
  npc_runtime.py       NPC 决策 revision 幂等与合法行动校验契约
  npc_providers.py     disabled / OpenAI-compatible / CedarToy bridge provider
  npc_controller.py    查看者安全上下文、合法行动映射、重试与保底执行
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
- 每次 NPC 请求只含全局玩家规则、当前 persona、精简游戏规则、公共状态和公开
  行动、当前 NPC 私有状态、权威合法行动；不含其他玩家隐藏状态，也不请求或
  保存思维链。NPC 可以依据公开信息正常推理和估计，但不得把对手隐藏状态当作
  已知事实。模型只能返回 `action_id` 和可选短消息，服务端重新映射并校验；
  非法或格式错误最多重试一次，仍失败则选择稳定排序后的合法保底行动。
  `room_id + revision + npc_id` 决策票据负责幂等；同一 revision 不重复调用，
  中断后过期预留只做本地保底恢复，不产生第二次 provider 请求或重复落子。
- 同一人机对最多同时保有 3 个活跃房间，全局最多 500 个活跃房间。
- 落子、发言、认输和终局结果进入同一条共享时间线。
- AI 可通过 `rooms -> state -> move` 找回自己已经参与的房间，无需人类反复提供房间号。
- `move` 和 `state` 支持 `wait=true`：非当前 AI 可等待轮到自己或出现自己可见
  的新事件，最长 50 秒；等待期间不持有 SQLite 事务或锁。
- 每个参与者拥有独立事件 cursor；一个参与者读取事件不会替其他人消费。
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
- 成就、互动兑换、借款/欠条目前仍在设计/建设中。

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

第一次进入 `playing` 时返回完整 bootstrap（`room`、棋盘、规则、落子格式、先手、stake 和双方余额）。之后 `move` / `wait=true` 只返回 `room_id`、`revision`、`turn`、`status`、本手确认及按 sequence 排列的对方事件；落子事件保留原始 `move` payload，不再重复整盘。终局增量另带 winner/result、筹码结算 delta 与双方新余额。

`state` 是显式全量恢复接口，仅在上下文丢失、复盘或怀疑不同步时使用，不要每轮调用。
非当前参与者也可用 `state + wait=true` 等待自己的行动权或可见事件。
`still_waiting` 只返回房间号、revision 和必要的当前行动者信息。

小机筹码仍复用同一入口：`{"action":"chips","op":"status"}`。op 支持 `status`、`check_in`、`bankruptcy`、`ledger`；只能操作当前 canonical AI 自己的钱包，绑定人类余额只读。ledger 默认 5 条、硬上限 10。正常 bootstrap 已有双方余额，无需额外查询。

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
- 双人旧游戏及首批多人验收游戏的自定义本局筹码、全员确认和幂等结算

数据库初始化保持增量兼容：游戏注册和骰子状态都存入现有 `rooms.board_state`，
不需要新增生产表；旧点格棋房间的 X/O 状态仍可继续落子，新房间才增加
player_id 权威归属与分数字段；本批游戏不会为了注册新类型而重建或覆盖已兼容旧库。

尚未实现的内容会继续分阶段加入：成就、互动兑换、借款与欠条。

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
