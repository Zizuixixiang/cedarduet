# 酒桌版结束结算

- 不限制轮数；真实在场玩家可 `end_session`，NPC/代操作不可结束。只统计已开骰或投降结算的回合，不给未完成回合扣分。
- 复用 `score_half_units` 半分账本，结束只生成结果快照，不新增积分表、钱包、平台筹码或成就奖励。每轮封顶规则保持原样。
- `room.result` 保存 `completed_rounds`、`rankings`、`winner_player_ids`。排名按分数降序，同分并列（如 1、1、3），并列第一均获胜。组队行含 `team_id` 和 `player_ids`，队分仅排一次，另有 `winner_team_ids`。
- 无已结算回合时 `outcome=no_contest`、排名为 `null`、胜方为空；否则 `outcome=ranked`。`draw=true` 仅保留共享框架技术兼容，不能据此显示“和棋”。旧终局房间读时补投影，不回写数据库。
- 原 `resultModal` 内显示排行榜；重进已结束房间直接显示相同结果，关闭后轮询不重复弹出。原版及其他游戏弹窗沿用原行为。
- MCP 终局变更仅给简短结果和获胜 ID，未变更时不重复发送；完整排名/历史用 `full_state:true` 按需读取。结束不额外公开尚未揭盅的骰子，只保留既有公开揭盅记录和查看者自己的私骰。

局部检查（均使用临时数据库/ASGI）：

```sh
.venv/bin/python -m unittest -q tests.test_liars_dice_results tests.test_liars_dice_party tests.test_liars_dice_rules_final tests.test_liars_dice_teams
.venv/bin/python -m unittest -q tests.test_liars_dice_lobby tests.test_liars_dice_frontend tests.test_frontend_visuals tests.test_room_listing tests.test_room_refresh_frontend
DICE_DOM_ONLY=1 JSDOM_MODULE=/opt/cedartoy/node_modules/jsdom node tests/liars_dice_results_browser.cjs
git diff --check
```

已有 Chromium/Playwright 环境可去掉 `DICE_DOM_ONLY`，指定 `PLAYWRIGHT_MODULE` 与 `DICE_SCREENSHOTS`，脚本检查 360、430、1280。DOM 检查不是视觉验收；本次环境无浏览器，截图、真实溢出与触控检查待复核。
