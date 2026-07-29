# Duel：人类与绑定 AI 的回合制对弈框架

Duel 是一个纯单机性质的 human-vs-AI 回合制对弈框架。一局只属于一位人类及其绑定的 AI，不提供大厅、社交关系或陌生人匹配。项目以公益开源、非商业使用为定位，采用 PolyForm Noncommercial 1.0.0 许可。

当前自带六个全明规则棋种：

- `tictactoe`：3×3 井字棋，用作框架冒烟测试。
- `gomoku`：15×15 五子棋，无禁手，连续五子或更多即胜。
- `othello`：8×8 黑白棋，支持无合法步自动跳过与终局数子。
- `connect4`：7×6 四子连珠，按列重力落子。
- `dots_boxes`：5×5 点阵的点格棋，成格得分并保留行动权。
- `jungle`：7×9 标准斗兽棋，含河道、跳河、陷阱和兽穴规则。

## 架构

服务是独立 FastAPI/ASGI 应用，由 uvicorn 单进程运行：

```text
网页 /api/rooms/* ─┐
                   ├─ 通用对局框架 ─ 六个棋种插件
AI /mcp/play ──────┘       │
                           ├─ SQLite rooms / room_participants / room_messages
                           └─ asyncio.Event（只做进程内唤醒提示）
```

- 一房一局。`rooms` 保存局面字段，`room_participants` 以席位关系保存 `player_id`、`role`、`seat_index`，不再把双方写死成两列；当前协议仍投影 `human_player_id` / `ai_player_id` 以保持兼容，并为未来多人棋局预留扩展空间。
- 所有写操作先执行 `BEGIN IMMEDIATE`，再读取并校验旧状态，最后在同一事务内更新。
- 同一人机对最多同时保有 3 个活跃房间，全局最多 500 个活跃房间；`new/join` 在写事务内检查容量。
- AI 的 `move` 支持 `wait=false` 和 `wait=true`。后者落子提交后才等待，不持有数据库连接、事务或锁；人类落子会触发 `asyncio.Event`。唤醒后重新读取 SQLite 并检查 revision。
- 单次等待最多 50 秒，超时返回顶层 `status: "still_waiting"`，调用方可稍后用 `state` 查看，或在下一次落子后再次等待。
- 全局最多同时挂起 20 个等待；容量已满时落子仍成功，但按 `wait=false` 立即返回并附 `wait_downgraded: true`。
- 活跃房间连续 7 天没有落子，会在下一次被读取或写入时惰性判和并改为 `archived`；无需后台定时器。
- `rules_text` 与 `move_format` 由棋种插件提供，AI 和网页使用同一份内容。
- 插件可通过 `MoveResult.retain_turn` 表明成格、自动跳过等情况下继续由本方行动，框架统一处理轮次。
- AI 的 `join/move/state/resign` 可附带最长 500 字的 `message`；人类可随落子说话或独立留言。独立留言不增加 revision，也不唤醒等待者。
- AI 的返回包含一次性 `new_messages`：未读人类消息读取后即在 SQLite 标记，避免重复占用 token。网页时间线按顺序显示双方落子和发言。
- 人类页面不接受自报身份或房间号。聚合层进门只验证人类登录态，并通过可信请求头注入人类身份与其全部绑定小机清单；`GET /api/whoami` 返回该人类名下的全部房间，活跃对局优先。开房时才选择对手，服务端再按可信绑定清单校验。裸连 8772 只显示回主站登录的引导。
- Event 通知是单进程内机制，因此 uvicorn 必须保持单 worker；SQLite revision 保证返回局面可验证。

## 目录

```text
app/
  main.py             FastAPI 路由、等待与唤醒
  database.py         SQLite 初始化及 BEGIN IMMEDIATE 事务
  framework.py        房间、身份、轮次、胜负、认输
  models.py           HTTP 请求模型
  games/              棋种接口及六个插件
  static/             人类端网页、棋盘与对局时间线
tests/
  play_tictactoe.py   完整 HTTP 对局及 wait=true 并发唤醒演示
  test_games.py       井字棋和五子棋规则单测
  test_new_games.py   四个新增棋种与保留行动权单测
  test_messages.py    消息唤醒、暂存投递与一次性已读单测
  test_capacity.py    房间容量、等待降级、惰性归档与 schema 迁移
  test_identity.py    可信身份、全量房间、选机校验与参与者鉴权
```

## 本地启动

要求 Python 3.10+。

```bash
cd /opt/cedartoy/vendor/duel
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8772
```

打开 <http://127.0.0.1:8772/>。默认数据库为 `data/duel.db`，也可用 `DUEL_DB_PATH` 指定。

本项目选择 8772；部署时如已被占用，应依次改用 8773、8774，并同步修改 supervisor 示例或启动命令。当前 CedarToy 部署使用本目录 `.venv`，配置安装在 `/etc/supervisor/conf.d/cedartoy-duel.conf`，program 名为 `cedartoy-duel`。仓库内的 `duel.supervisord.conf.example` 是对应的可审查副本。

## AI HTTP 接口

所有 AI 操作统一提交到：

```http
POST /mcp/play
Content-Type: application/json
```

支持 `new`、`join`、`move`、`state`、`resign`。直连时由调用方传入 `player_id`；经 CedarToy 聚合层时会覆盖并强制注入可信身份。

AI 创建房间：

```json
{
  "action": "new",
  "player_id": "ai-42",
  "game_type": "gomoku",
  "mode": "human_first"
}
```

AI 加入人类创建的房间：

```json
{"action":"join","player_id":"ai-42","room_id":"ABCDEFGH"}
```

AI 落子并等待人类回应：

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

响应均为结构化 JSON，顶层包含 `ok`、`status`、自然语言 `message`、`new_messages` 和完整 `room`。房间对象包含规则文本、指令格式与当前棋盘。`new_messages` 只投递尚未读取的人类发言。

人类独立留言：

```http
POST /api/rooms/ABCDEFGH/messages
Content-Type: application/json

{"player_id":"human-42","message":"我还在想这一手。"}
```

该接口不会推进 revision，也不会唤醒 AI 的 `wait=true` 请求；留言会在 AI 超时返回、被落子唤醒或下次调用时送达。

人类身份与房间列表由主站代理提供：

```http
GET /api/whoami
X-Duel-Human-Player: <trusted human id>
X-Duel-Human-Name: <percent-encoded human name>
X-Duel-Bound-Ais: <base64url JSON [{"id":"...","name":"..."}]>
```

上述头只应由 loopback 聚合代理注入。网页不会展示或允许填写这些内部 ID；开房请求只提交所选 `ai_player`，后端会拒绝任何不在可信清单中的小机。

## 自测

```bash
cd /opt/cedartoy/vendor/duel
python3 -m unittest discover -s tests -v
python3 tests/play_tictactoe.py
```

第二个脚本使用 `httpx.ASGITransport` 走真实 FastAPI 路由，在临时 SQLite 库中完成一局井字棋。它会明确断言 AI 的 `wait=true` 请求先保持挂起，再由人类落子唤醒，并最终校验 AI 获胜。

## License

[PolyForm Noncommercial License 1.0.0](LICENSE)。允许非商业用途；商业使用不在本许可授权范围内。
