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

`trade_options` 给出可交易对象、双方可交易地产及余额。提出即发起方确认；接收方独占响应行动权，接受时重新核验现金、产权和建筑条件后同事务交割。临时接管不能提出或接受资产交易，可拒绝解除等待。系统NPC只以自己的席位估值交易，不能代理另一席确认。

公开投影包含玩家现金/位置/资产、地产状态、已揭示事件、当前阶段、拍卖和待确认交易。`_decks`、支付队列、恢复续步仅在存档；持有出狱卡只返回本人数量。移动/产权/付款摘要作为公共增量供小机续玩；`private_state.decision_context` 另附当前公开决策上下文，覆盖通用认输/离席事件触发拍卖、监狱尝试次数等场景。`rules_text/move_format`、private guide与合法行动均无需看图。

## 持久化与检查入口

新局由现有 `rooms.board_state` 同事务保存，骰子、牌序、保留出狱卡、待确认交易、竞拍者和最高价、支付队列、第三次出狱后的移动续步全部为JSON；恢复不重掷不洗牌。随机数只在接受的服务端动作中产生，生产使用 `SystemRandom`，不向客户端收骰点/价格/产权。

所有以下检查使用临时库/身份或纯内存。浏览器脚本启动临时localhost服务，直接服务模式只在测试中模拟根网关注入已知测试身份，不连接生产。

```sh
cd vendor/duel
.venv/bin/python -m unittest tests.test_monopoly tests.test_monopoly_integration
cd ../..
python3 scripts/persistence_check.py --duel-monopoly
PLAYWRIGHT_MODULE=/path/to/playwright node scripts/check_duel_monopoly_browser.js
```

规则测试包括2/4/6人的固定种子自然终局、所有32事件、交易/债务/拍卖/建房边界及JSON恢复。集成测试涵盖普通房/邀请房MCP和HTTP、并发/越权/重放、跨进程恢复、钱包账本隔离、NPC与超时接管、终局及现有重赛边界。浏览器检查区分真实HTTP开局/行动与合成阶段交互，不以合成fixture代表全部服务端路径。

第三方数据、固定版本、完整许可与采用范围见 [NOTICE](../third_party/intrepid_monopoly/NOTICE.md)。
