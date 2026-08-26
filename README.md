# CedarDuet / 双弈

人类与自己的 AI 搭档进行回合制棋牌对弈的独立服务。

CedarDuet 本体是一个独立的 FastAPI/ASGI 项目，包含棋局引擎、房间系统、共享时间线、人类网页端、AI HTTP 接口、SQLite 持久化，以及正在建设中的全局娱乐筹码系统。CedarToy 是当前官方部署所使用的认证、绑定关系和 MCP 聚合层，但游戏逻辑并不放在 CedarToy 主仓库里。

> 当前定位：公益、非商业、娱乐用途。筹码仅为站内娱乐数值，不支持充值、提现或与真钱兑换。

## 当前游戏

- `tictactoe`：3×3 井字棋
- `gomoku`：15×15 五子棋，无禁手
- `othello`：8×8 黑白棋，支持自动跳过与终局数子
- `connect4`：7×6 四子连珠
- `dots_boxes`：点格棋
- `jungle`：7×9 斗兽棋

当前六种均为双人局。框架已经使用参与者席位表保存身份，为后续多人棋牌预留了扩展空间。

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
  static/              人类端网页、棋盘、时间线、筹码中心
tests/                  单元测试与前端行为测试
data/                   本地运行数据目录；真实数据库不会提交到 Git
```

## 核心能力

- 一房一局，人类与 AI 作为独立参与者保存。
- 同一人机对最多同时保有 3 个活跃房间，全局最多 500 个活跃房间。
- 落子、发言、认输和终局结果进入同一条共享时间线。
- AI 可通过 `rooms -> state -> move` 找回自己已经参与的房间，无需人类反复提供房间号。
- `move` 支持 `wait=true`：AI 落子后可等待人类动作，最长 50 秒；等待期间不持有 SQLite 事务或锁。
- 全局最多 20 个并发等待；超过容量时落子仍然成功，只是不继续挂等。
- 活跃房间长期无动作时可惰性归档。
- 终局房间支持“保留 / 取消保留 / 手动删除”。当前版本已暂停自动物理删除终局记录，避免旧对局在正式公告前被清理。
- 人类普通聊天、AI 普通聊天、落子事件和结果事件使用不同的前端视觉层级。
- 全局娱乐筹码第一版已经包含：首次 200、每日签到 +20、允许负数、`<= -500` 可自愿破产、破产后重置 50、破产次数与状态标记、统一筹码流水。
- 成就、互动兑换、借款/欠条，以及每局筹码下注与结算目前仍在设计/建设中。

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

当前官方 CedarToy 部署会在聚合层认证 AI，并强制覆盖为 canonical `player_id`；客户端自报的 `player_id` 不参与身份选择。

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
  "move": {"row": 7, "col": 7},
  "message": "我先占住中心。",
  "wait": true
}
```

响应为结构化 JSON，包含 `ok`、`status`、自然语言 `message`、`new_messages` 和完整 `room`。AI 可以读取共享时间线中的落子、聊天和裁判终局事件。

## 人类网页 API

常用接口包括：

```text
GET  /api/whoami
POST /api/rooms
GET  /api/rooms/{room_id}
POST /api/rooms/{room_id}/move
POST /api/rooms/{room_id}/messages
POST /api/rooms/{room_id}/resign
POST /api/rooms/{room_id}/retention
POST /api/rooms/{room_id}/delete
```

终局保留和删除仅允许该房间中的可信人类参与者操作。

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

尚未实现的内容会继续分阶段加入：每局筹码、双方确认、对局结算、成就、互动兑换、借款与欠条。

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
