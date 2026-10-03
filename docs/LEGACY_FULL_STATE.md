# 老 25 款 full_state 验收

基线：`3fdab70d3c1ba3ad012b901264d5eeecc0d3ede2`。仅在隔离副本修改，所有执行使用临时
SQLite；没有部署、commit、push 或连接生产数据库。

## 实现边界

`app/main.py` 仅在 `body.action == "state" and body.full_state` 内，对明确列出的老 25 款
调用 `app/full_state.py`。未 opt-in 的游戏继续原分支。所有 `app/games/*.py`、
`app/framework.py`、普通 state/move/bootstrap、public/private/event/legal-action hook
均未修改。新投影以已有 viewer-safe snapshot 为基础；删除仅限逐款白名单中的静态字段。

12 款调整中局快照内容，13 款不再做字段瘦身。所有 25 款共用以下 full_state 恢复修正：

- 保留房间身份、revision、当前行动者、座次/棋色、参与者当前状态和公开游戏元数据；
  对非 active 席位明确补 `active:false`。
- 私有状态逐值保留，包括完整本人手牌、暗子、骰子、个人副露、合法行动、动态响应及编码。
- 终局补房间级 `winner/winner_player_id/result/terminal_reason`；仍使用原游戏终局隐私投影。
- 快照覆盖的旧棋局事件不再重放。未读聊天、走子附言、文字通知在本次 full_state 的
  `events` 中按序交付一次；后续 state/重复 full_state 不再返回。游标最多推进到快照
  revision 覆盖的连续事件前缀，不跨过并发的新 revision。保持原可见性和插件事件过滤。
  不 claim bootstrap，不动 Web 游标、不增加数据库字段。

因此“普通响应不变”指没有调用 full_state 的原有交互链完全不变；显式 resync 之后，
后续响应有意不再返回已经由快照覆盖的动作和已经交付的文字。

## 25 款逐项清单

下表“未改”指中局字段投影；上述 full_state 游标与终局恢复修正仍适用于全部 25 款。
“展示字段”仅指 participants 的 `role/kind/handle`；保留 `name/player_id/seat/token/status/game`。

| 游戏 | 中局是否改 | 删除内容 | 保留内容 / 必要补全 |
|---|---|---|---|
| aeroplane_chess | 未改 | 无新增删除 | 所有飞机、route_step/zone、阶段、骰点、连续6状态、合法行动；原快照已省固定路径 |
| banqi | 改 | draw_quiet_turns 常量、展示字段 | 完整明暗棋盘、颜色、quiet_turns、合法行动、终局揭示 |
| blackjack | 改 | shoe_decks、展示字段 | 全席公开手牌/点数/状态、本人手牌、庄家遮蔽、行动权及结果 |
| tictactoe | 未改 | 无 | 完整棋盘；中局仅257 token |
| texas_holdem | 改 | initial_stack/small_blind/big_blind 固定值、展示字段 | 底牌、公共牌、street、按钮/盲注席、stack/投入、所有pot/side-pot、合法下注范围；fold/muck不揭示 |
| train_cards | 未改 | 无 | 全部桌面牌、牌数、active/淘汰、当前收牌结果、flip动作；未翻个人牌堆仍隐藏 |
| gomoku | 未改 | 无 | 完整15×15棋盘、当前行动信息；中局491 token |
| go | 未改 | 无 | 全19×19棋盘、劫相关权威合法坐标、提子、死子/计分确认；不自行重算superko |
| gandengyan | 改 | max_multiplier 常量、展示字段 | 完整本人手牌、所有合法响应、当前墩/倍率；补全当前公开discard |
| guandan | 改 | engine/engine_version、展示字段 | 全手牌/参数行动、级牌/团队/贡还/接风/完赛顺序；补全本副played_cards |
| othello | 未改 | 无 | 完整棋盘、双方合法落点、分数 |
| connect4 | 未改 | 无 | 完整棋盘、最后落点；中局312 token |
| checkers | 未改 | 无 | 完整棋盘、forced_piece、强制连跳合法走法、draw_status |
| chess | 未改 | 无 | board和FEN均保留：FEN还承载易位权、吃过路兵与计数；全部legal_actions含申和 |
| chinese_checkers | 未改 | 无新增删除 | 全部pieces、营区归属、进度、可提交合法终点；原快照已省nodes/camps |
| dots_boxes | 未改 | 无 | 全部横纵边、格子归属、分数/认输信息；中局435 token |
| doudizhu | 改 | 展示字段 | 本人完整手牌、叫分/地主/底牌/倍率、当前墩、所有合法响应；补全公开discard |
| liars_dice | 未改 | 无 | 本轮本人骰子、当前叫价、轮次、淘汰、上轮公开骰子/结果、pending_next_round和邀请房确认动作 |
| mahjong | 改 | 展示字段 | 本人完整手牌/own_melds、摸牌ID、所有牌河/副露、响应窗口、合法action_id、终局手牌 |
| yahtzee | 改 | categories、upper_bonus_threshold/score、max_rolls、展示字段 | 当前骰子、held mask、掷骰次数、所有scorecards/totals、动态score_previews/Joker/奖励、合法计分类 |
| uno | 改 | 展示字段 | 本人完整手牌、顶牌/颜色/方向、WDF penalty/challenge、UNO窗口、合法响应；补全当前公开discard |
| jungle | 改 | terrain、展示字段 | 完整棋盘及双方合法走法；固定地形仍在bootstrap |
| junqi | 改 | bunkers/headquarters/rail_lines/rules_version、展示字段 | 本人完整棋子军衔/布局/布阵锁定与合法交换/行棋；公共棋盘、司令/军旗状态；从公开裁判事件补齐battles，含早于40条缓存的战斗 |
| xiangqi | 未改 | 无 | 完整棋盘/FEN、将军/困毙、所有权威合法走法 |
| zhajinhua | 改 | ante/raise_tiers/max_blind_unit/virtual_budget/max_rounds、展示字段 | 本人规则允许可见的三张牌、pot/投入、blind_unit、轮次/行动权、比牌/揭示/结果及合法响应 |

`monopoly/rummikub/bomb_plane/carcassonne` 不 opt-in，快照及游标协议保持原样。

## 响应与体积证据

[逐组 before/after cl100k_base 表](LEGACY_FULL_STATE_TOKENS.md) 包含全部25款、种子11/37，
共50个中局。完整原始HTTP回复在交付目录 `artifacts/legacy-full-state/legacy-before.json`
和 `legacy-after.json`，表内记录其SHA256。

两组中局共 **996条普通响应逐值相等**：112 bootstrap、442 normal state、442 move。
比较只统一随机room_id与ISO时间值；所有其他字段、数组顺序、public/private/event、
legal actions均参加比较。没有通过删掉大型字段或忽略合法行动来获得“相等”。
每组after都只从full_state选择下一动作并成功执行，无需再请求普通state/guide。

中局8款缩小、4款因补齐公开弃牌而增大、13款保持原大小。没有为了token指标删掉恢复
所需信息。大小包含full_state外层包装，以紧凑JSON和cl100k_base计数；测量前清空
未读事件以单独测当前局面，不把bootstrap或每轮delta混入full_state数字。真实未读文字
仍完整返回，不能据此表宣称包含聊天时也有固定token上限。

采样使用真实临时房间和规则引擎，不修改随机牌序或伪造低体积中局。21点为4席、
斗地主3席、掼蛋/麻将4席，其余2席。21点取1动作后仍有后续席位行动的中局，井字棋2动作，
德州3动作，其余10动作。掼蛋固定 `PYTHONHASHSEED=0` 以稳定核心集合枚举次序。

## 验证与复跑

最终通过361项：full_state专项10项、MCP compact/斗地主46项、hidden information/
多人/邀请/认输/重点游戏/排除四款/消息回归305项。`git diff --check` 通过。
完整日志在交付目录 `artifacts/legacy-full-state/`，不把早先失败后修复的运行计入通过数。

专用测试覆盖全部25款的字段保留/下一行动；UNO质疑、麻将碰牌响应、军棋公开战斗与
存活暗子隐私、吹牛骰子邀请房下一轮确认；聊天/附言恰好一次、新revision未消费、私密
文字过滤；默认分支/排除四款；正常终局与重点游戏认输终局。

```sh
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_legacy_full_state tests.test_mcp_compact tests.test_doudizhu
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_hidden_information_audit tests.test_multiplayer_games tests.test_multiplayer_phase1 \
  tests.test_invite_multiplayer tests.test_forfeit_lifecycle tests.test_guandan tests.test_guandan_engine \
  tests.test_mahjong tests.test_junqi tests.test_uno tests.test_texas_holdem tests.test_four_game_incremental \
  tests.test_banqi tests.test_blackjack tests.test_gandengyan tests.test_yahtzee tests.test_zhajinhua tests.test_messages
git diff --check
```

采样脚本可通过PYTHONPATH分别使用基线副本和本副本；基线副本只需额外复制测试辅助文件
`tests/full_state_support.py`。每次新复跑使用新的输出路径（脚本支持按已完成的游戏/种子续跑）。

```sh
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/tmp/duel-fullstate-baseline \
  .venv/bin/python scripts/sample_legacy_full_state.py --out /tmp/new-before.json
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
  .venv/bin/python scripts/sample_legacy_full_state.py --out /tmp/new-after.json
python3 scripts/compare_legacy_full_state.py /tmp/new-before.json /tmp/new-after.json --out /tmp/new-tokens.md
```

最后一条使用已安装tiktoken的分析解释器，不改项目依赖。测试/采样均不启动生产服务。

## 已存在的验收边界

采样过程中发现掼蛋原有 `guandan_parametric_v1` 表的部分示例索引组合会被
`_canonical_action_id` 拒绝。已在 `/tmp/duel-fullstate-baseline` 的基线代码单独复核，
不是本补丁引入。种子11、四席p0–p3，起手表中suffix=1等示例可复现。
本任务要求普通legal actions和动作校验不动，因此不顺带修复；掼蛋采样选用表中
首项动作完成10步及快照后的下一步。全量私有状态逐值保持，不能把这里的“下一步可执行”
扩大为“已经穷举验证掼蛋每个参数组合均可执行”。
