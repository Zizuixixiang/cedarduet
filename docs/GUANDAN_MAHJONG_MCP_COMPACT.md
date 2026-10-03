# 掼蛋 / 麻将普通 MCP 私有状态

仅两款游戏的 `mcp_turn_private_state(private, public)` 做 ordinary-only 投影。
现有 `/mcp/play` 的 `state` / `move` 响应调用此钩子；bootstrap 与
`state, full_state=true` 继续使用完整 `mcp_private_state`。不新增查询入口，
不改共享游标、聊天、公开增量、隐私边界或其他游戏。

## 掼蛋

普通 `private_state` 保留 `hand`、`legal_action_count`、`option_count`，以及
`legal_actions.format/action_id_prefix/options`。仅省略重复的
`fields/pattern_labels/submit/coverage`；这些字段每次 full_state 都可恢复。
`mcp_move_format` 解释各列、物理手牌索引、base36 提交方式与贡还/接风动作。

`guandan_parametric_v1` 行的完整列顺序由 full_state 的 `fields` 提供：

```json
["suffix", "kind", "pattern", "main_rank", "size", "wild_count", "suit", "example_hand_indexes"]
```

手牌索引从 0 开始，始终对应**当前** hand 数组；同牌名的两个位置是两张实体牌。
每个语义选项的 `example_hand_indexes` 是完整、可直接提交的选牌方案。
普通响应保留全部语义选项，没有截取前 N 项；核心的其他同语义选牌也可通过
该选项后缀与对应实体索引提交。核心仍验证选牌，不应仅凭牌名交换副本索引。

有牌动作：`action_id_prefix + suffix + '.' + 升序索引的 base36，以 ',' 连接`。
无牌动作（过/接风）：`action_id_prefix + suffix`。提交对象为
`{"action":"act","action_id":"构造所得字符串"}`，携带当前房间 revision。
不能只拼 prefix 和 suffix 后忽略有牌动作的索引部分。

生成表和解析提交均使用带 `wild` 的同一手牌投影。原始引擎牌对象没有此字段，
直接用原始牌重建选项会合并逢人配数量不同的组，导致编号错位；适配层已修正。
核心原始 `g_...` 动作 ID 的接受方式不变。

## 麻将

普通 `private_state.format` 为 `mahjong_tiles_v1`：

- `hand` 和 `own_melds[].tiles` 每项为 `[id, 中文牌名]`，如 `["W1-1", "1万"]`。
- 真实 136 张牌 ID 均为 `code-副本号`；副本号 1–4。W/B/T 为万/筒/条，
  F1–F4 为东南西北，J1–J3 为中发白。另留中文牌名以便直读。
- 副露保留 `kind`、所有实体牌及 `source_player_id`。仅省略可由 tiles 长度
  得到的 `tile_count`；自己的暗杠保留真实牌面。
- `drawn_tile_id`、`shanten`、`shanten_basis`、完整 `legal_actions` 均原样保留。
  向听依据只有短字符串，保留它可区分当前向听与最佳弃牌后的向听。
- legal_actions 是当前窗口的完整、可提交动作对象，复制其中一项即可；
  不从牌 ID 推测吃碰杠胡。当前响应来源和优先级仍由既有公开状态/事件给出。

bootstrap/full_state 仍返回 id/code/label/suit/rank 完整牌对象，完整副露
（含 tile_count、kind、真实牌面、来源）、向听与合法动作。

## 验证与采样

`tests/test_guandan_mahjong_compact.py` 验证完整 core→普通 alias 的唯一覆盖、
响应驱动解析器、全部动态选项、136 个麻将 ID、冷恢复、普通/邀请房与特殊阶段。
场景和解析器在 `tests/guandan_mahjong_compact_support.py`；解析器的 fields 和
submit 来自真实 full_state，不从测试常量补回被省略的协议说明。

采样使用临时 SQLite 与真实 ASGI `/mcp/play`，固定 `PYTHONHASHSEED=0` 和游戏种子。
用项目 Python 运行 `scripts/sample_guandan_mahjong_compact.py`；`--before-dir`
可加载两个未修改的游戏模块。用带 tiktoken 的 Python 运行
`scripts/compare_guandan_mahjong_compact.py`，逐值验证动作轨迹和未改字段，
输出 cl100k_base 的 p50/p95/max。full_state 仅记录大小，不作压缩目标。
