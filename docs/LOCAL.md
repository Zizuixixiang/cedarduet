# CedarDuet 本地 Web + MCP

## 1. 准备环境

需要：

- Python 3.10 或更高版本；
- Node.js（规则桥直接运行仓库内 JS，不需要 npm install）；
- C++ 编译工具链，用于从 vendored 源码构建 PyMahjongGB。

Windows 需安装 Visual Studio Build Tools，并勾选“使用 C++ 的桌面开发”和 Windows SDK。macOS 缺编译器时运行 `xcode-select --install`。Debian/Ubuntu 通常需要 `build-essential`、`python3-dev`、`python3-venv`。

## 2. clone 并启动浏览器版

```bash
git clone https://github.com/Zizuixixiang/cedarduet.git
cd cedarduet
```

macOS / Linux：

```bash
./scripts/start-local.sh
```

Windows PowerShell：

```powershell
.\scripts\start-local.ps1
```

Windows cmd 或双击：

```bat
scripts\start-local.cmd
```

launcher 会创建 `.venv`、安装 `requirements-local.txt`、编译并导入检查 PyMahjongGB、依次探测象棋/国际象棋/军棋/围棋四个 Node bridge，然后以单 worker 启动 `app.local_gateway:app`。浏览器入口默认为 `http://127.0.0.1:8772/`；可用 `DUEL_LOCAL_PORT` 改端口。

本地 gateway 只接受 loopback client 与 localhost/127.0.0.1/::1 Host。它会剥离浏览器伪造的 `X-Duel-*`、`player_id`、`opponent_id`、`ai_player(s)`，再注入：

- 人类：`local-human`；
- 绑定小机：`local-ai`。

本地数据固定使用 `data/local-duel.db`，不会读取或写入生产 `data/duel.db`。停止 launcher 后再次启动会继续使用本地棋局。

## 3. 配置标准 stdio MCP

保持 gateway 运行，另开终端生成配置：

```bash
python3 scripts/local.py mcp-config
```

Windows：

```powershell
py -3 scripts\local.py mcp-config
```

把输出的 `mcpServers.cedarduet` 放入支持 stdio MCP 的宿主配置并重启宿主。配置使用 `.venv` 内的绝对 Python 路径执行 `python -m app.local_mcp`，无需依赖宿主当前工作目录。

adapter 只暴露 `play` 工具，输入字段与生产 `POST /mcp/play` 一致，但不暴露 `player_id`、`opponent_id`、`participant_ids`。每次调用都会强制写入 `local-ai` / `local-human`，再通过 loopback HTTP 请求 gateway 的现有 `/mcp/play`；不会直接调用 `_mcp_play_impl`。因此 bootstrap、compact delta、full_state、revision、wait/still_waiting、viewer privacy、事件 cursor、unread/notices 均由原生产路由实现。

## 4. NPC 与多人桌

所有现有桌型保持不变。`local-human + local-ai` 占两个真实席位，其余席位只有在请求 `fill_with_npcs=true` 时才补 NPC。仓库提供“下棋助手 1–4”四个中性本地 persona，不带头像，也不定义任何行动策略。

- `uses_local_npc_strategy=true` 的游戏继续使用已有本地策略，不需要 API。
- 其他游戏必须配置现有 provider。没有配置时，开房会在写库前拒绝并提示：“该游戏 NPC 需要配置 API/模型通道，或加入更多真实小机/减少 NPC”。
- 不缺座、未启用补位或减少到无需 NPC 的桌型不检查 provider。
- 本地层没有 `local_legal`、random 或随机合法动作 provider。

原前端在本地会保持 NPC 补位选项可操作；provider 能力延迟到上述开房请求检查，避免尚未缺座时提前判断。该投影只存在于 local gateway，生产 `/api/whoami` 仍返回真实 provider capability。

如需 OpenAI-compatible provider，新建不会提交的 `.env.local`：

```dotenv
DUEL_NPC_PROVIDER=openai_compatible
DUEL_NPC_API_BASE=https://your-provider.example/v1
DUEL_NPC_API_KEY=replace-with-your-secret
DUEL_NPC_MODEL=your-model
```

launcher 也接受现有的 `DUEL_NPC_TIMEOUT_SECONDS`、`DUEL_NPC_MAX_TOKENS`、`DUEL_NPC_MAX_CONCURRENCY`。API key 只进入 gateway 进程环境，不写入网页、房间数据库或日志。

## 5. 自检与排错

只安装并检查环境：

```bash
python3 scripts/local.py doctor
```

不自动打开浏览器：

```bash
./scripts/start-local.sh --no-browser
```

自定义 Node：

```bash
DUEL_NODE_BINARY=/absolute/path/to/node ./scripts/start-local.sh
```

launcher 不会为缺失的 Node 或 PyMahjongGB 编译器提供规则降级。Windows 编译失败时先确认 Build Tools 的 C++ workload 与 Windows SDK 已安装；macOS/Linux 按错误提示补齐本机编译工具后重试。

## 6. 与生产的隔离边界

生产仍启动：

```bash
python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8772
```

`app.main:app` 不导入本地 gateway 或 adapter，也不知道 `local-human/local-ai`。toy.cedarstar.org 的可信 Header、CedarToy canonical MCP 身份覆盖及所有生产 API 协议保持原样。只有明确启动 `app.local_gateway:app` 时，本地身份层才存在。
