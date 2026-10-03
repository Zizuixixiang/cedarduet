# 大富翁接入说明

`game_type=monopoly`，名称“大富翁”，允许2～6人、推荐4人。使用已有普通房/邀请房、绑定小机/人类/NPC席位、聊天、鉴权、SQLite持久化和终局流程；不改变其他游戏人数。普通房沿用再来一局；邀请房沿用既有“另开邀请”流程，原房一键重赛仍不支持。

局内现金1500，与平台筹码钱包无兑换、借款或输赢映射，`supports_stakes=False`。平台现有成就奖励仍按公共规则发放，不属于局内资金结算。没有局时/回合上限。

## 规则与界面

权威规则及面向玩家的完整说明在 `app/games/monopoly.py:Monopoly.rules_text`，开局由框架发送，也供网页规则弹层使用。

40格环形地产盘、22块同色街区地产、4车站、2设施。两骰移动，自动收租/薪水/税费；可买地、轮流拍卖、成套均衡建房至旅馆（32屋/12旅馆库存）、半价卖房、抵押赎回、多资产双向交易。机会/公益各16张，牌序私有并持久化；监狱支持付50/出狱卡/掷双骰、第三次强制付费。债务可筹款或确认破产，最后存活者胜。

本地明示约定：经营只在本人掷骰前/落地后/筹款时；每正常回合最多3笔交易；抵押转让不立即收费、赎回按本金110%向上取整；破产建筑半价清算、欠玩家则转资产，欠银行则解除抵押后拍卖；出狱卡不可交易，使用或破产回牌堆。均已写入玩家规则，不依赖其他版本的默认假设。

专属 renderer `app/static/games/monopoly.js` 按目录自动加载，加载独立 `monopoly.css`，不修改飞行棋或公共布局样式。地块显示短名、产权席位、棋子和房屋；详细租金、库存、升级与交易在对话框中展开，原聊天保持不变。

## 动作与阶段

`phase` 为 `roll/purchase/manage/auction/trade/debt/finished`。`current_player_id` 表示正常回合归属，`turn_player_id` 是当前决策者（拍卖、交易、生日收款债务时可不同）。框架行动者始终与后者一致。

所有 move 必须带当前整数 `action_seq`，网页仍由宿主附加房间 `revision`。两层序号加 SQLite `BEGIN IMMEDIATE` 串行保护重复请求、竞价、扣费和交割；原请求重放被拒绝。除参数化出价/交易外，从本人 `private_state.legal_actions` 原样选动作。

| 动作 | 额外参数 |
|---|---|
| `roll/buy/auction/end_turn` | 无 |
| `build/sell_building/mortgage/redeem` | `tile_id` 整数 |
| `bid` | `amount` 整数，大于现价且不超过现金 |
| `pass_bid` | 无；退出当前地产竞拍，不影响下块地产 |
| `propose_trade` | `to`, `give_cash`, `take_cash`, `give_tiles`, `take_tiles` |
| `respond_trade` | `accept` 布尔值 |
| `pay_bail/use_jail_card/bankrupt` | 无 |

`trade_options` 给出可交易对象、双方可交易地产及余额。提出即发起方确认；可同时挂多笔报价，但每个接收人最多一笔，接收人已有报价时只从可交易对象中排除该人。接收方在自己的下个正常回合先响应，随后继续原阶段；接受时重新核验现金、产权、建筑及抵押条件后同事务交割，单笔失效不清掉其他有效报价。临时接管不能提出或接受资产交易，可拒绝解除等待。系统NPC只以自己的席位估值交易，不能代理另一席确认。

待处理列表使用 `trades`；旧存档缺少此字段时从 `trade` 及旧抵押快照/提出回合信息兼容读取，在下次写入时迁移。旧 `phase=trade` 的恢复续步仍保留。`trade` 兼容字段指向当前行动者收到的报价，否则为最早的报价；回应动作格式不变，按接收人唯一定位。MCP v2 增量中 `trades` 整体替换，空列表表示清空。

网页默认折叠为“待处理交易 N 笔”，可手动展开；轮到本人回应时自动展开、突出并优先显示发给本人的报价，提供接受/拒绝操作。普通停在监狱格显示“只是探访，未入狱”；真正入狱后才可在本人回合掷骰前保释或使用出狱卡。

被拒绝的报价保存在私有状态中，仅用于系统 NPC 策略：双方和各方向地产集合相同、净现金方向相同，且净额差不超过 10 或原拒绝净额的 10%（取较大值），视为相近。接下来该 NPC 的三个正常回合不再提出，第四个可重新考虑；额外掷骰、拍卖和临时债务行动不消耗冷却，JSON 恢复后继续计数。

公开投影包含玩家现金/位置/资产、地产状态、已揭示事件、当前阶段、拍卖和待确认交易。`_decks`、支付队列、恢复续步仅在存档；持有出狱卡只返回本人数量。移动/产权/付款摘要作为公共增量供小机续玩；`private_state.decision_context` 另附当前公开决策上下文，覆盖通用认输/离席事件触发拍卖、监狱尝试次数等场景。`rules_text/move_format`、private guide与合法行动均无需看图。

## NPC 决策

`system_npc` 每步优先调用现有 provider 一次 `decide`，同一响应返回 `action` 对象及 `message`（可为 `null`），包括只有一个普通合法动作的情况。不会另调 `speech`，连续静默、失败兜底和恢复路径也不补调。中间动作可静默，交易鼓励一句自然桌边说明；聊天不携带接口参数或内部推理。

模型获得完整公开棋盘、本人出狱卡数量、全部待处理交易、最近公开事件、完整规则及合法动作。普通动作复制候选；`action_spec` 按当前窗口开放交易双方现金/地产与竞价参数，交易并不限于本地策略的固定报价。模型动作先经过引擎和拒价冷却校验，再由 `play_move` 在事务内复验。任何调用失败、20秒整体超时、错误格式或非法动作均直接回退原 `choose_local_npc_action`，不重试 provider。

`uses_local_npc_strategy` 保留为本地能力标记，供离线补位和真实席位临时接管使用；系统 NPC 在 controller 中优先走上述模型路径。拉密、卡卡颂及其他游戏的本地策略和独立 speech 流程不变。

## 持久化与检查入口

新局由现有 `rooms.board_state` 同事务保存，骰子、牌序、保留出狱卡、待确认交易、竞拍者和最高价、支付队列、第三次出狱后的移动续步全部为JSON；恢复不重掷不洗牌。随机数只在接受的服务端动作中产生，生产使用 `SystemRandom`，不向客户端收骰点/价格/产权。

所有以下检查使用临时库/身份或纯内存。浏览器脚本启动临时localhost服务，直接服务模式只在测试中模拟根网关注入已知测试身份，不连接生产。

```sh
cd vendor/duel
.venv/bin/python -m unittest tests.test_monopoly tests.test_monopoly_integration
PLAYWRIGHT_MODULE=/path/to/playwright node tests/check_monopoly_trade_jail.js
# 无法启动浏览器时可验证交互，但不能替代 360/430/1280px 视觉检查：
DUEL_MONOPOLY_DOM_ONLY=1 node tests/check_monopoly_trade_jail.js
cd ../..
python3 scripts/persistence_check.py --duel-monopoly
PLAYWRIGHT_MODULE=/path/to/playwright node scripts/check_duel_monopoly_browser.js
```

规则测试包括2/4/6人的固定种子自然终局、所有32事件、交易/债务/拍卖/建房边界及JSON恢复。集成测试涵盖普通房/邀请房MCP和HTTP、并发/越权/重放、跨进程恢复、钱包账本隔离、NPC与超时接管、终局及现有重赛边界。浏览器检查区分真实HTTP开局/行动与合成阶段交互，不以合成fixture代表全部服务端路径。

第三方数据、固定版本、完整许可与采用范围见 [NOTICE](../third_party/intrepid_monopoly/NOTICE.md)。
