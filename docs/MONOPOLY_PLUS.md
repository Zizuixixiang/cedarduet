# 大富翁·改接入说明

`game_type=monopoly_plus`，名称“大富翁·改”，2～6 人、推荐 4 人，0 筹码娱乐局（`supports_stakes=False`）。插件 `app/games/monopoly_plus.py` 继承原版 `Monopoly`，复用交易、拍卖、建房、抵押、债务与破产清算；原版 `monopoly` 的玩法与行为不变（仅补充公共调度使用的完整回合结束标记）。玩家规则全文在 `MonopolyPlus.rules_text`。

制作：顾屿、相顾｜小红书：苏苏脆脆

## 版本是一份配置

`app/games/monopoly_plus_editions.py` 的 `EDITIONS` 每项包含：

| 字段 | 含义 |
|---|---|
| `name` / `tagline` / `available` | 选择页文案；`available=False` 只灰显“敬请期待”，服务端拒绝选择 |
| `tiles` | 地图覆盖 `{tile_id: (名字, kind)}`，其余格子同原版 40 格 |
| `decks` | `chance` / `chest` / `event` 牌堆（索引 0 必须是出狱卡） |
| `rules` | 机制开关与数值：`speed_die`、`parking_auction`、`items`、`loans`、`bets`、`naming`、`single_cell_jail`、起始现金、保释金、贷款比例/封顶/利率/圈数、押注押金与赔付、手牌上限、文字长度等 |

开局阶段 `phase=setup`，先手从 `legal_actions` 里选 `choose_edition`；选定后该版本的规则被复制进存档 `state.rules`，以后改配置不影响进行中的对局。新增版本 = 加一项配置，引擎只读开关。当前只有「经典·改」可玩，另有「地铁环线版」「海岛度假版」两个占位。

> 版本没有做成建房参数：那需要改建房请求模型、邀请表结构和 MCP 建房工具。放在开局第一步，所有房型（普通房、邀请房、MCP、NPC）都不用改。

## 阶段与动作

`phase` 为 `setup/roll/purchase/manage/auction/debt/choice/finished`。`choice` 是“决策窗口”：行动权临时交给需要表态的人（与拍卖同一模式），`state.choice.kind` 为：

| kind | 谁行动 | 动作 |
|---|---|---|
| `triple` | 掷出三同的人 | `choose_destination{tile_id}` |
| `pick_auction` | 停在拍卖行的人 | `pick_auction{tile_id}` / `skip_choice` |
| `gift` | 抽到红包卡的人 | `give_gift{to,amount 50~200}`；现金不足 50 时 `skip_choice` |
| `chat`（stage=perform） | 抽卡人 | `perform{text≤150}` / `skip_choice` |
| `chat`（stage=vote） | 其他存活玩家依次 | `vote{accept}` |
| `nope_fine` | 被罚款且持有“不行”的人 | `use_nope` / `decline_nope` |
| `nope_attack` | 被强行交易/收购的人（总会弹出，以免泄露手牌） | `use_nope`（持有时）/ `decline_nope` |

其他新增动作：`use_bus{tile_id}`、`take_loan{amount}`、`repay_loan{amount}`、`name_tile{tile_id,name,motto}`、`use_item{item:"swap",tile_id,target_tile_id}`、`use_item{item:"acquire"}`。所有动作带当前 `action_seq`。

### 非回合动作（观战押注）

押注是唯一不需要行动权的动作：`{"action":"bet","choice":"big|small|seven"}`，在当前玩家本回合第一次掷骰前、每人一次、现金≥50。为此在 `GamePlugin` 加了默认关闭的钩子 `accepts_out_of_turn_action(state, move, player_id)`，`framework.play_move` 只在插件返回真时放行非当前席位；插件仍用自己的 `validate_action` 校验，并返回原行动者作为 `next_player_id`。这种动作不重置邀请房的超时计时。网页端 renderer 用新增的 `helpers.submitSideMove`，其它游戏不受影响。可押项在本人 `private_state.side_actions`。

## 随机与隐藏信息

骰子、速度骰（1/2/3/巴士/巴士/大富翁先生）、三副牌序、巴士票池（16 张含 3 张作废）、事件里的随机目标全部由服务端动作产生并写入存档。道具手牌只出现在本人 `private_state.items`，公开投影只有张数；抽到道具卡时公开文本只写“道具卡（内容保密）”；公开 `legal_actions` 去掉道具动作。被攻击方总会收到确认窗口，所以“有没有不行卡”不会因为是否弹窗而暴露；罚款类事件的“不行”窗口只在持有时出现（这一点会暴露持有，属于有意取舍）。

## 小机省纸（MCP）

走原协议（同宝石商人，`full_state` 白名单已加入）：

- `mcp_bootstrap_state`：一次性给紧凑地图（每格一行字符串，如 `1 松巷 property g3 ¥60 租2/10/30/90/160/250 房50`）、玩家行 `players` 与有主地块行 `owned`，不给 40 格原始对象。
- 每个动作的公开增量 `monopoly_plus_delta` 只含变化：`p`/`t` 行按主键覆盖、`log` 为本动作新增事件文字、其余键整体替换。
- `mcp_private_state` 把建房/抵押/巴士目标等重复动作压成 `tile_actions{动作:[地块ID]}`，借款写成区间，三同的 39 个落点压成 `choose_destination_any_of`。
- 需要完整地图时 `state(full_state=true)` 返回完整 `tiles`（含租金表）。

实测 2 人局小机每回合响应约 300–1300 字节。

## NPC

`supports_npcs=True`、`uses_local_npc_strategy=True`：系统 NPC 与超时托管都走 `choose_local_npc_action`，不调用模型决策。NPC 会买地、建房、交易（沿用原版策略）、坐巴士去无主地、债务时借款、有把握时用收购/强行交易、在聊天卡里说一句预置台词并按发言长度投票。超时托管替真人投票一律投“反对”（超时按不通过），不替真人提出或接受资产交易。

## 检查

```sh
.venv/bin/python -m unittest tests.test_monopoly_plus tests.test_monopoly_plus_integration
.venv/bin/python -m unittest tests.test_monopoly tests.test_monopoly_integration tests.test_monopoly_card_events tests.test_monopoly_npc
```

规则测试覆盖版本配置、速度骰各面、巴士票与作废、温柔监狱与挤出、拍卖行、道具上限与限制、“不行”、借贷利息/到期拍卖/破产优先、押注结算、地名牌过滤与摘牌、全部事件卡、聊天卡投票与托管、2/4/6 人固定种子自然终局和 MCP 投影；集成测试覆盖真实 `play_move` 的非回合押注、网页 HTTP 落子、MCP 紧凑增量与 full_state、系统 NPC 选版本与行动、邀请房计时不被押注重置。
