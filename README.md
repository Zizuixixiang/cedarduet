# Duel：人类与绑定 AI 的回合制对弈框架

Duel 是一个纯单机性质的 human-vs-AI 回合制对弈框架。一局只属于一位人类及其绑定的 AI，不提供大厅、社交关系或陌生人匹配。项目以公益开源、非商业使用为定位，采用 PolyForm Noncommercial 1.0.0 许可。

一期自带两个棋种：

- `tictactoe`：3×3 井字棋，用作框架冒烟测试。
- `gomoku`：15×15 五子棋，无禁手，连续五子或更多即胜。

## 架构

服务是独立 FastAPI/ASGI 应用，由 uvicorn 单进程运行：

```text
网页 /api/rooms/* ─┐
                   ├─ 通用对局框架 ─ 棋种插件（井字棋 / 五子棋）
AI /mcp/play ──────┘       │
                           ├─ SQLite rooms（唯一事实源）
                           └─ asyncio.Event（只做进程内唤醒提示）
```

- 一房一局。核心字段包括 `game_type`、`mode`、`board_state`、`turn`、`revision`、`status`、`winner`、`created_at`，另存双方 `player_id`。
- 所有写操作先执行 `BEGIN IMMEDIATE`，再读取并校验旧状态，最后在同一事务内更新。
- 同一人机对最多同时保有 3 个活跃房间，全局最多 500 个活跃房间；`new/join` 在写事务内检查容量。
- AI 的 `move` 支持 `wait=false` 和 `wait=true`。后者落子提交后才等待，不持有数据库连接、事务或锁；人类落子会触发 `asyncio.Event`。唤醒后重新读取 SQLite 并检查 revision。
- 单次等待最多 50 秒，超时返回顶层 `status: "still_waiting"`，调用方可稍后用 `state` 查看，或在下一次落子后再次等待。
- 全局最多同时挂起 20 个等待；容量已满时落子仍成功，但按 `wait=false` 立即返回并附 `wait_downgraded: true`。
- 活跃房间连续 7 天没有落子，会在下一次被读取或写入时惰性判和并改为 `archived`；无需后台定时器。
- `rules_text` 与 `move_format` 由棋种插件提供，AI 和网页使用同一份内容。
- Event 通知是单进程内机制，因此 uvicorn 必须保持单 worker；SQLite revision 保证返回局面可验证。

## 目录

```text
app/
  main.py             FastAPI 路由、等待与唤醒
  database.py         SQLite 初始化及 BEGIN IMMEDIATE 事务
  framework.py        房间、身份、轮次、胜负、认输
  models.py           HTTP 请求模型
  games/              棋种接口及两个插件
  static/             人类端网页
tests/
  play_tictactoe.py   完整 HTTP 对局及 wait=true 并发唤醒演示
  test_games.py       两个棋种的规则单测
  test_capacity.py    房间容量、等待降级、惰性归档与 schema 迁移
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

支持 `new`、`join`、`move`、`state`、`resign`。`player_id` 一期由调用方传入，后续聚合层应覆盖并强制注入可信身份。

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
  "wait": true
}
```

响应均为结构化 JSON，顶层包含 `ok`、`status`、自然语言 `message` 和完整 `room`。房间对象包含规则文本、指令格式与当前棋盘。

## 自测

```bash
cd /opt/cedartoy/vendor/duel
python3 -m unittest -v tests.test_games tests.test_capacity
python3 tests/play_tictactoe.py
```

第二个脚本使用 `httpx.ASGITransport` 走真实 FastAPI 路由，在临时 SQLite 库中完成一局井字棋。它会明确断言 AI 的 `wait=true` 请求先保持挂起，再由人类落子唤醒，并最终校验 AI 获胜。

## License

[PolyForm Noncommercial License 1.0.0](LICENSE)。允许非商业用途；商业使用不在本许可授权范围内。
