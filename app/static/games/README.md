# 浏览器游戏 UI renderer

每个新游戏可以把浏览器界面放在独立的 `app/static/games/<game_type>.js` 中，
无需修改 `app.js` 的棋盘分发。脚本使用经典浏览器脚本格式、无构建步骤，并在加载时
完成注册：

```javascript
(function registerExampleGameUI() {
  "use strict";

  window.DuelGameUI.register("example_game", {
    // 默认为 true：点击格子后使用双弈现有的“落子”确认条。
    usesStandardMoveConfirmation: true,

    renderBoard(context) {
      const {board, state, legalMoves, uiState, helpers} = context;
      helpers.setBoardLayout({
        rows: state.rows,
        cols: state.cols,
        ariaLabel: "示例游戏棋盘",
      });

      state.board.forEach((row, rowIndex) => {
        row.forEach((piece, colIndex) => {
          const move = {row: rowIndex, col: colIndex};
          const legal = legalMoves.some(
            (item) => item.row === rowIndex && item.col === colIndex
          );
          const cell = document.createElement("button");
          cell.type = "button";
          cell.className = "cell";
          cell.textContent = piece || "";
          cell.disabled = !context.canMove || !legal;
          cell.setAttribute(
            "aria-label", `第 ${rowIndex + 1} 行第 ${colIndex + 1} 列`
          );
          cell.setAttribute(
            "aria-pressed", String(helpers.isMoveSelected(move))
          );
          cell.addEventListener("click", () => helpers.selectMove(move));
          board.appendChild(cell);
        });
      });

      // uiState 是同一房间、同一 revision 内持久的可变草稿，适合保存
      // “已选起点”等只属于界面的状态；revision 变化后会自动清空。
      uiState.lastRenderedAt = Date.now();
    },
  });
}());
```

## 注册契约

全局稳定入口是 `window.DuelGameUI`：

- `register(gameType, renderer)`：注册一次；`gameType` 只允许小写字母、数字、
  `_`、`-`，重复注册或缺少 `renderBoard` 会抛错。
- `get(gameType)`：返回 renderer；没有注册时返回 `null`。
- `load(gameType, scriptUrl?)`：按需加载脚本并返回 renderer Promise；省略 URL 时
  使用 `/static/games/<gameType>.js`。
- `has(gameType)` 和 `registeredGameTypes()`：供诊断和测试使用。

renderer 必须实现同步的 `renderBoard(context)`。还可实现：

- `renderControls(context)`：在棋盘下方的 `context.controls` 中绘制专属操作区。
  容器每次重绘前都会清空。
- `usesStandardMoveConfirmation: false`：隐藏通用“落子”确认条。此时专属控件通常
  应调用 `context.helpers.submitMove(payload)`，并自行提供可访问的按钮、状态和禁用态。
- `ownsPrivateStatePresentation: true`：renderer 已在自己的桌面中呈现手牌等私有状态，
  宿主不再重复显示通用 JSON 私有状态面板。未声明时保留通用面板作为降级展示。

`context` 每次渲染都会重建；除 `uiState` 外都应视为只读：

| 字段 | 含义 |
|---|---|
| `board`, `controls` | 已清空的棋盘与专属控件 DOM 容器 |
| `room`, `state`, `privateState` | 当前房间、公共棋面、当前 viewer 私有投影 |
| `participants`, `viewer`, `identity` | 席位、当前 viewer 与网页身份 |
| `timeline` | 当前共享时间线 |
| `canMove`, `isTerminal` | 本次渲染时的人类行动权和终局状态 |
| `pendingMove` | 通用确认条当前待提交动作的浅拷贝 |
| `legalMoves` | `state.legal_moves`，不存在时为空数组 |
| `legalActions` | 优先取 `privateState.legal_actions`，否则取公共同名字段 |
| `uiState` | 仅在同一 `game_type + room_id + revision` 内保留的界面草稿 |
| `helpers` | 与宿主交互的受控 helper |

helpers 的稳定用途如下：

- `setBoardLayout({rows, cols, visualRows?, visualCols?, large?, ariaLabel?})`：设置现有
  响应式棋盘 CSS 变量和可访问名称。非网格桌面可直接给 `board` 添加自己的 class。
- `selectMove(payload)` / `isMoveSelected(payload)`：选择待确认动作并重绘；动作比较为
  浅层键值比较。
- `clearSelection({render?})`：清除待确认动作和 `uiState`；默认重绘。
- `submitMove(payload)`：直接带当前 revision 提交，返回 `Promise<boolean>`。
- `rerender()`：仅当原 room/revision 仍是当前页面时重绘。
- `canMove()`：事件触发时再次检查行动权，避免只依赖渲染快照。
- `participantByPlayerId`、`participantForOwner`、`pieceClass`、
  `ownerDescription`：复用通用席位、颜色和可访问描述。
- `announce(message, {error?, emphasize?})`：写入现有 `role=status` 的对局提示。

renderer 应直接使用服务端投影的合法行动，不要在浏览器复制权威规则。事件回调应
绑定在本次创建的 DOM 节点上；旧节点重绘时会移除。复杂选择流程放进 `uiState`，
最终动作放进 `pendingMove` 或直接传给 `submitMove`。

## 加载方式

推荐把脚本放到约定路径。目录返回新 game_type 时，`app.js` 会自动调用
`DuelGameUI.load(gameType)`；现有 8 个 legacy 游戏不会发起额外请求。脚本加载失败或
没有注册时，宿主继续执行原来的 legacy fallback。

如需固定版本或非约定文件名，可在 `index.html` 中显式加载。顺序必须是 registry、
renderer、`app.js`：

```html
<script src="/static/game_ui_registry.js?v=0.9.0"></script>
<script src="/static/games/example_game.js?v=1"></script>
<script src="/static/app.js?v=0.9.0"></script>
```

只有服务端 game catalog 正式返回游戏时，它才会进入“棋/牌/骰”选择器；仅增加
renderer 文件不会把未完成游戏写进生产目录。

## 规则文案格式

`rules_text` 是供玩家阅读的轻结构纯文本，不是 Markdown。用独占行的 `【目标】`、
`【行动】`、`【特殊规则】`、`【胜负】` 分段，连续要点以 `- ` 开头，板块间留空行；
普通文本会按段落安全回退。动作参数和数据格式只写在 `move_format`，不要混入规则文案。
