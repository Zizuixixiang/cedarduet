# 老 25 款 full_state 完整性增量修补

这是任务 `1a429a67-c26b-4320-8f04-cf26f71a96c2` 的续修，基于上一轮成果，
没有重新实现协议。原 patch、报告、token 表和测试产物保持原文件、原内容。
本轮输出单独位于 `artifacts/full-state-completeness-fix/`。

## 决定：没有可靠的中途重查证明，就直接保留

上一轮 `app/full_state.py` 删除清单中的 22 个游戏字段全部恢复。
不把“一次性 bootstrap 有过”作为可省略的依据，也没有新增查询服务。
`STATIC_FIELDS` 现在为空，生产投影不再执行静态字段删除；原来被省掉的参与者
`role/kind/handle` 同样恢复。**本次清单中仍删除的字段：无；对应另一个查询入口：无须依赖。**
恢复的值就在可重复调用的原 `state(full_state=true)` 响应中。

| 游戏 | 恢复到 board_state 的全部字段 | 中途重查验收 |
|---|---|---|
| banqi | draw_quiet_turns | 当前 quiet_turns 和和棋阈值同时可见 |
| blackjack | shoe_decks | 四席中局庄家遮蔽/各席手牌/牌靴副数同时恢复 |
| texas_holdem | initial_stack、small_blind、big_blind | 与当前 street、盲注席、pot/投入、底牌一起恢复 |
| gandengyan | max_multiplier | 当前倍率与上限同时可见，公开弃牌补全仍在 |
| guandan | engine、engine_version | 原编码/引擎版本信息与完整本人手牌、参数动作表一起恢复 |
| yahtzee | categories、upper_bonus_threshold、upper_bonus_score、max_rolls | 13 类 key/label/section、63→35 上栏奖励、3 次掷骰上限，以及当前骰子/scorecard/动态预览 |
| jungle | terrain | 9×7 当前棋盘、双方兽穴/陷阱、水域、坐标方向与地形语义一起恢复 |
| junqi | bunkers、headquarters、rail_lines、rules_version | 60 格当前棋盘、10 个行营、大本营、铁路、本人完整军衔/位置与公开战斗同时恢复 |
| zhajinhua | ante、raise_tiers、max_blind_unit、virtual_budget、max_rounds | 当前 pot/轮次/投入/比牌状态与固定规则参数同时恢复 |

以上九款以及 `doudizhu/mahjong/uno` 的 participants 均恢复 `role/kind/handle`。
其余原有参与者字段、active 状态、公开资源和私有状态保持原值。

复核还发现更早的共享 snapshot hook 省略了两款地图。这些地图直接影响丢失开局
上下文后的棋盘解释，所以**只在 full_state 专用层**从已有安全 public projection 补回：

| 游戏 | 额外恢复 | 信息检查 |
|---|---|---|
| aeroplane_chess | path_mappings、ring_length、home_lane_length、finish_route_step | 每色52格完整路径、当前飞机位置与终点参数，不只验证能 roll/move |
| chinese_checkers | nodes、camps | 全121孔的坐标、六个10孔营区、全部当前棋子与所属/目标营区，所有棋子/营区都能映射回地图 |

这些补回没有修改游戏共享 snapshot/bootstrap hook，未改变 legal_actions/legal_moves 的
现有编码。原有历史日志裁剪和重复合法走法表示的压缩保留；它们不是这次删除清单里的
地图/规则参数。既有完整手牌、FEN、合法响应窗口、弃牌补全、军棋公开战斗、终局结果、
聊天恰好一次、快照覆盖的旧动作不重放等修复全部保留。

## 完整性验收方式

`tests/test_legacy_full_state_completeness.py` 独立记录原删除清单，防止生产代码只恢复部分字段
却让测试跟着缩小检查范围。测试先消费一次 bootstrap，实际走到中局，再丢弃早期响应。
恢复阶段只调用两次 full_state，且禁止 bootstrap helper 被调用；重复快照必须相同。

对涉及恢复的14款，逐 viewer 检查全部现有公共字段、参与者、当前行动者及完整私有状态，
再逐一核对22个原删除字段、6个继承遗漏地图字段和3个参与者字段。三个重点游戏还独立
验证具体地形/军棋布局/全部计分类别及动态 score_previews。测试中选择动作仅用于构造
中局，**完整性结论不依赖“第一条合法动作执行成功”**。

## 普通协议与范围保护

本轮运行时代码只修改 `app/full_state.py`。`app/main.py`、`app/framework.py`、所有
`app/games/*.py`、普通 state/move/bootstrap/private_state/legal actions 都与续修开始时
字节一致。`monopoly/rummikub/bomb_plane/carcassonne` 的代码及原有成果没有改变。
保存于 `preserved-sha256.json` 的48个源文件/旧产物哈希用于结束时逐个核验。

普通响应对比复用原采样器：分别使用保存的修补前 `app/full_state.py` 和修补后的模块，
用相同种子/行动，在各自全新的临时SQLite中完整跑50个样本。两次都从头连续执行，
避免旧采样分批续跑导致累计未读通知数不同。没有忽略 unread、事件或合法动作字段。
仅规范化随机 room_id 和 ISO 时间值。原采样报告仍保留，不用本轮结果覆盖。

## 产物与复跑

最终通过 **164项测试**：完整性5项、原 full_state 专项10项、MCP compact23项、隐藏信息/
重点地图游戏/多人/消息/新四款回归126项。50组中局对应 **996条普通响应逐值一致**
（112 bootstrap、442 state、442 move）。另逐字段核验50组前后快照：只增加预期恢复
字段，修补前已有字段、完整私有信息均保持原值；游标函数和响应包装函数的AST未改。
48个保护文件SHA256全部一致，原patch/报告/测试产物及普通协议源文件未被覆盖。

| 代表性样本（种子11） | 修补前 | 修补后 | 增加原因 |
|---|---:|---:|---|
| jungle | 1233 | 1413 | 兽穴/陷阱/水域/坐标语义及参与者字段 |
| junqi | 1518 | 1755 | 铁路、行营、大本营、版本及参与者字段 |
| yahtzee | 543 | 792 | 全计分类别/奖励参数/掷骰上限及参与者字段 |
| aeroplane_chess | 751 | 1518 | 完整固定路径及长度/终点参数 |
| chinese_checkers | 886 | 3142 | 全121孔坐标及六营区 |

上述数字均为 cl100k_base token；14款增加、11款不变。旧“必须小于bootstrap的65%”
断言改为仅检查去掉非局面包装后仍小于bootstrap，不再用任意压缩比例否决必要地图。

- 增量补丁：`artifacts/full-state-completeness-fix/completeness-fix.patch`，应用在上一轮成果上。
- 修补前源码：`before-sources/`；原产物哈希：`preserved-sha256.json`。
- 原始HTTP回复：`before-rerun.json` 与 `after.json`。
- [cl100k_base 逐样本对比](../artifacts/full-state-completeness-fix/token-comparison.md)。
- 完整性/MCP回归日志：`completeness-tests-final.log`；隐藏信息等回归：`regression-tests.log`。
- 逐字段/源码/旧产物保护核验：`preservation-verification.json`。
- 早先检查日志也保留，不把未通过的试跑算作最终通过结果。

```sh
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_legacy_full_state_completeness tests.test_legacy_full_state tests.test_mcp_compact
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest \
  tests.test_hidden_information_audit tests.test_junqi tests.test_yahtzee \
  tests.test_aeroplane_chess tests.test_chinese_checkers tests.test_four_game_incremental \
  tests.test_multiplayer_games tests.test_messages
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python \
  scripts/sample_full_state_completeness.py \
  --before-module artifacts/full-state-completeness-fix/before-sources/app/full_state.py \
  --out /tmp/completeness-before-fresh.json
PYTHONHASHSEED=0 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python \
  scripts/sample_full_state_completeness.py --out /tmp/completeness-after-fresh.json
python3 scripts/compare_legacy_full_state.py /tmp/completeness-before-fresh.json \
  /tmp/completeness-after-fresh.json --out /tmp/completeness-tokens.md
git diff --check
```

采样输出路径须尚不存在，以保证两边都从头运行；最后的分析解释器需已有 tiktoken。
token 包含完整 full_state JSON（紧凑序列化、ensure_ascii=False），不含未读文字；文字
交付由专项回归单独检查。恢复地图等信息导致token增加是本次修补的预期结果。

原掼蛋参数表部分示例被校验器拒绝的既有问题仍保留原报告说明；本轮没有改其编码或
校验器，也不以掼蛋的单个可执行动作替代字段完整性验证。

全部执行仅在隔离副本和临时数据库中进行。未停止其他任务、合并主工作树、commit/push
或部署，原工作区和旧产物未删除。
