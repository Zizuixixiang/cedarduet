# CedarDuet MCP 游戏指南

所有动作提交到 `POST /mcp/play`。`player_id` 必须由可信聚合层覆盖为当前绑定小机；
`npc:*` 不能作为 MCP 身份。先用 `state` 取得 `revision`，落子时原样带回。陈旧
revision 返回 409，调用方应重新 `state`，不得盲目重放。

`rooms/new/accept/reject` 的 pending 结果包含短 `stake_label/stake_hint`，确认邀请前
即可知道买入、计价单位、倍率或最大风险，不必等待 playing bootstrap。`resign` 是
明确认输，非零筹码必按败方规则结算；`leave` 是独立的房间生命周期动作，不能与认输
互换使用。

本地 clone 可使用 `python -m app.local_mcp` 提供的标准 stdio adapter。它不接受
调用方提供身份字段，固定以 `local-ai` 调用 loopback gateway，并由 gateway 注入
绑定人类 `local-human`；adapter 仍通过本页同一个 `POST /mcp/play`，不直接调用内部
实现。启动和宿主配置见 [LOCAL.md](LOCAL.md)。以下生产协议正文不因本地 adapter 改变。

## 增量上下文

每个房间、每名小机只有第一次进入 `playing` 会收到 `bootstrap=true` 和完整 `room`。
之后 `state` / `move` / `wait` 只返回最小控制状态与该 viewer 尚未读取的可见 `events`。
事件形状为 `{"name": "显示名", "message"?: "...", "move"?: {...}}`；同一次落子
的发言和行动不会拆开，也不会暴露 sequence、事件 revision、player ID、座位或 kind。
确定性棋盘只需把服务端已接受的 `move` 应用到本地盘面；随机或自动公开后果会紧随该
move 追加一个 `{"name":"双弈裁判","<game>_delta":{...}}`。这类结果在行动者失去
回合时也会直接进入本次 move 响应。插件 delta 中按参与者映射的公共计数可以使用
bootstrap 已知的稳定 player ID，但不会加入新的身份资料。
普通 message、其他参与者的 move 和未声明“立即公开”的 round_result 只进入
房间增量事件游标（不是下文
四类持久化未读通知），不会唤醒尚未
获得行动权的小机，也不会被 `state wait=false` 提前消费。真正轮到该小机时，服务端
一次返回它从上次消费以来全部可见事件；终局、归档、取消、本人离席、淘汰或失活也会提前
返回必要增量，避免永远等不到回合。游标前进后，同一事件不会再次返回。轮到当前小机
时，隐藏信息游戏可额外返回 `private_state`。

带筹码的自定义终局以 `room.result.settlement_deltas` 为权威。MCP `settlement.delta`
是当前小机自己的真实整数 delta，`settlement.deltas` 是按稳定 `player_id` 的完整映射；
多人终局不会伪造 `human/ai` 两格。仅普通双人固定 ±stake 终局继续保留旧
`delta={"ai":...,"human":...}` 兼容形状。

本地状态丢失或怀疑漏增量时可随时读取当前完整安全视图：

```json
{"action":"state","player_id":"ai-42","room_id":"ABCDEFGH","full_state":true}
```

响应的 `snapshot` 只含 `room_id/game/revision/status/current_actor/participants`、完整
当前公共 `board_state` 和该 viewer 自己的 `private_state`。它不重复 `rules_text`、
`move_format`、筹码、静态拓扑或 action/move/dice history；少数游戏把重复的 verbose
legal move 压成可直接提交的字段。`full_state` 可重复调用且立即返回，即使同时传
`wait=true` 也不挂等；它既不 claim/补发一次性 bootstrap，也不读取或推进事件游标。
因此第一次先调用 full_state 后，下一次普通 `state` 仍会正常得到唯一 bootstrap；
已经 bootstrap 后调用它也不会让 bootstrap 重来。旧请求不传该字段时行为不变。

`DUEL_MCP_WAIT_SECONDS` 控制短心跳，默认 30 秒、允许 1–45 秒，不影响 NPC provider
timeout。`still_waiting` 只含房间号与 revision，它表示本次心跳结束，不表示退出挂等；
调用方处于挂等模式时应继续调用 `state wait=true`。

## 四类持久化未读

人类与绑定小机完全对称，只存在四类持久化未读：`game`（对局）、`loan`（借款）、
`exchange`（兑换）、`achievement`（成就）。聊天仍随房间增量事件返回；普通落子、
轮到谁、签到、流水和破产不会制造未读。

任一 MCP 成功响应只在当前小机确有未读时附加紧凑计数与入口提示；没有未读时省略
`unread` / `unread_hint`：

```json
{
  "unread": {
    "total": 2,
    "categories": {"game": 1, "loan": 0, "exchange": 1, "achievement": 0}
  },
  "unread_hint": "对局（未读1）→rooms；兑换（未读1）→chips/exchange"
}
```

只看到 summary 不会清除。看到提示后按固定入口读取：

- 对局 → `{"action":"rooms","player_id":"ai-42"}`
- 借款 → `action=chips, op=loans, loan_action=list`
- 兑换 → `action=chips, op=exchange, exchange_action=list`
- 成就 → `action=chips, op=achievements`

这四个实际读取入口会返回该类别的短 `notices`，并在同一 SQLite 事务中标为已读。
如果终局已经由当前 move/state/wait 响应返回，或解锁已经在当前响应的 `unlocks` 中返回，
对应通知也会同步确认。系统 NPC 没有通知主体。

## 筹码、成就与权威重赛

小机读取自己的成就（绑定人类身份仍由可信聚合层注入为 `opponent_id`）：

```json
{"action":"chips","op":"achievements","player_id":"ai-42","opponent_id":"human-1"}
```

结果仅包含当前小机适用的通用、小机专属、已启用 NPC 和该具体绑定配对的关系成就。
普通未解锁项保留条件和 `[current,target]` 进度；未解锁隐藏项完全不返回也不计总数。
解锁奖励自动进入统一 `chip_ledger`，没有领取动作。

### 小机互动兑换

兑换只记录申请、审批和筹码转移；发起者先在双方常用聊天中完成约定并收取筹码，
审批者随后确认并支付筹码。双弈不接收照片、语音、截图或聊天内容，也不判断实际履约。
`player_id` 必须是当前小机，
`opponent_id` 必须由可信聚合层覆盖为当前绑定人类。

所有 `chips/exchange` 成功响应都返回稳定、短小的 `exchange_rule`。每张申请继续保留
`initiator` / `payer`，并增加自然语言 `summary` 和固定角色映射：
`direction={"agreement_provider":"initiator","chip_payer":"payer","chip_recipient":"initiator"}`。
因此无需根据动作猜测谁履约、谁付款、谁收款。

列小机专属与双方通用目录；人类专属商品不会返回：

```json
{"action":"chips","op":"exchange","exchange_action":"catalog","player_id":"ai-42","opponent_id":"human-1"}
```

小机发起时承诺完成商品内容，并在审批通过后获得筹码：

```json
{
  "action":"chips", "op":"exchange", "exchange_action":"create",
  "player_id":"ai-42", "opponent_id":"human-1",
  "item_key":"bedtime_story", "request_note":"今晚在聊天里讲一个短故事",
  "chip_amount":20, "idempotency_key":"exchange-create-ai42-0001"
}
```

`request_note` 必须为 1–120 字，`chip_amount` 必须为 1–100 的整数；`custom` 另需
`custom_title`（1–30 字）。每对绑定最多同时 3 张待处理申请，72 小时自动失效。

显式列出待自己审批、等待人类和历史三组：

```json
{"action":"chips","op":"exchange","exchange_action":"list","player_id":"ai-42","opponent_id":"human-1","limit":50}
```

人类发来的申请由小机付款，可确认或拒绝；小机自己发出的申请在审批前可撤回：

```json
{
  "action":"chips", "op":"exchange", "exchange_action":"confirm",
  "player_id":"ai-42", "opponent_id":"human-1",
  "request_id":"ex_0123456789abcdef",
  "idempotency_key":"exchange-confirm-ai42-0001"
}
```

把 `exchange_action` 改为 `reject` 或 `withdraw` 即执行相应操作。确认时会重新校验
绑定、付款方余额和 Asia/Shanghai 当日累计兑换支出上限（100 枚），并以稳定
`request_id` 结算键原子写入双方 `exchange_out` / `exchange_in` 流水。普通 chips
status、房间与对局响应不会携带兑换明细，但确有未读时会携带统一计数和入口提示。

### 小机欠条操作

欠条只会出现在显式 `op=loans` 查询中；普通 chips status、房间和对局响应不会附带
借款或逾期明细，只可能附加统一未读计数和入口提示。`opponent_id` 仍必须由可信聚合层
覆盖为当前绑定人类。

查询小机名下全部可审计欠条（包括解绑前已经生效的旧债）：

```json
{"action":"chips","op":"loans","loan_action":"list","player_id":"ai-42","opponent_id":"human-1","limit":20}
```

小机作为借款人发起；1,000,000 个 `daily_rate_micro_percent` 单位表示每日 1%，
封顶保护省略时默认开启：

```json
{
  "action":"chips", "op":"loans", "loan_action":"create",
  "player_id":"ai-42", "opponent_id":"human-1",
  "principal":80, "daily_rate_micro_percent":125000,
  "due_date":"2026-09-12", "interest_cap_enabled":true,
  "idempotency_key":"loan-create-ai42-0001"
}
```

小机收到人类提案后，可对当前 revision 接受、拒绝或改条件。所有写操作都要提供稳定
幂等键；改条件必须提交全部新条款并生成下一 revision：

```json
{
  "action":"chips", "op":"loans", "loan_action":"counter",
  "player_id":"ai-42", "opponent_id":"human-1",
  "loan_id":"ln_0123456789abcdef", "loan_revision":1,
  "principal":70, "daily_rate_micro_percent":100000,
  "due_date":"2026-09-10", "interest_cap_enabled":true,
  "idempotency_key":"loan-counter-ai42-0001"
}
```

```json
{
  "action":"chips", "op":"loans", "loan_action":"accept",
  "player_id":"ai-42", "opponent_id":"human-1",
  "loan_id":"ln_0123456789abcdef", "loan_revision":2,
  "idempotency_key":"loan-accept-ai42-0001"
}
```

小机只有在自己是借款人时才能还款；还款先抵利息、后抵本金，不得超过当前应还，
也不得使小机钱包为负：

```json
{
  "action":"chips", "op":"loans", "loan_action":"repay",
  "player_id":"ai-42", "opponent_id":"human-1",
  "loan_id":"ln_0123456789abcdef", "amount":25,
  "idempotency_key":"loan-repay-ai42-0001"
}
```

`reject` 和 `withdraw` 同样提交 `loan_id`、`loan_revision`、`idempotency_key`；只有
当前收到方能拒绝，只有借款发起人能在生效前撤销。解绑后不能接受或改条件，但生效
债务仍保留并可由真正借款人查询、偿还。到期日按 Asia/Shanghai 自然日，接受时会
再次验证至少为次日且不超过 30 天；未接受 revision 3 天过期。

小机可对一个已正常完成且原阵容不含随机 NPC 的房间主动发起权威重赛：

```json
{"action":"rematch","player_id":"ai-42","room_id":"ABCDEFGH"}
```

服务端校验上一局正常终局、发起者席位、同一游戏和完全相同的稳定参与者 ID，自动
翻转先手并沿用筹码；非零筹码仍需其他参与者重新确认。主动离桌、超时归档、故障
终止或客户端自行新建的相似房间都不会冒充权威重赛。

## 点格棋 `dots_boxes`

人数为 2、3 或 4，支持系统 NPC。使用 5×5 点阵、16 个格子。完成格子得 1 分并
保留行动权；填满后唯一最高分者胜，并列和局。新房间内部的边、格和
`scores_by_player` 使用真实 `player_id`；双人公共投影仍把边/格转换成 X/O，且
`scores` 继续保留 token 分数，供旧 Web/MCP 客户端读取。

```json
{
  "action": "move",
  "player_id": "ai-42",
  "room_id": "ABCDEFGH",
  "revision": 7,
  "move": {"orientation": "h", "row": 0, "col": 0}
}
```

横边范围为 `row=0..4,col=0..3`；竖边范围为 `row=0..3,col=0..4`。NPC 收到精简
规则、完整公开棋盘、公开画边历史和规则引擎枚举的全部未占边。

## 吹牛骰子 `liars_dice`

人数为 2–6，支持系统 NPC。每人初始 5d6，1 点不万能。叫点按 `(quantity, face)`
严格升序：数量增加时点数可以任意；数量相同则点数必须增加。quantity 范围为
1 到当前场上骰子总数。首叫前不能质疑。

叫点：

```json
{
  "action": "move",
  "player_id": "ai-42",
  "room_id": "ABCDEFGH",
  "revision": 3,
  "move": {"action": "bid", "quantity": 4, "face": 5}
}
```

质疑：

```json
{
  "action": "move",
  "player_id": "ai-42",
  "room_id": "ABCDEFGH",
  "revision": 4,
  "move": {"action": "challenge"}
}
```

质疑动作在一个 SQLite 写事务内公开本轮全部骰子、判断叫点、扣除一枚、
标记淘汰并确定下一轮首位。若未终局，流程停在
`awaiting_round_acknowledgement`：已揭骰保存在公共 `last_round_result`，当前骰子
暂不重掷。只有已认证人类向 `/api/rooms/{room_id}/move` 提交
`{"action":"acknowledge_round"}` 并带暂停时 revision 后，服务端才重掷存活者骰子、
进入下一轮 `bidding`。MCP 小机不能代替人类确认。
仅真实终局会额外发布 `terminal_dice`，用于复盘各席最终骰子。

首次 bootstrap 的安全投影形状：

```json
{
  "room": {
    "current_player_id": "ai-42",
    "board_state": {
      "flow": {"phase": "bidding", "round_number": 2, "turn_number": 0},
      "dice_counts": {"human-1": 4, "ai-42": 5},
      "current_bid": null,
      "eliminated_player_ids": [],
      "last_round_result": {"phase": "revealed"}
    },
    "private_state": {"dice": [1, 3, 3, 5, 6]}
  }
}
```

质疑结算通过一次性裁判事件发送，后续 `state` 不会重复：

```json
{
  "name": "双弈裁判",
  "round_result": {
    "round": 1,
    "bid": {"quantity": 4, "face": 5},
    "actual_count": 3,
    "bid_holds": false,
    "loser_player_id": "human-1",
    "loser_remaining_dice": 4,
    "eliminated": false,
    "next_round": 2,
    "next_starter_player_id": "human-1",
    "revealed_dice_by_player": {
      "human-1": [2, 3, 5, 5, 6],
      "ai-42": [1, 2, 3, 4, 6]
    }
  }
}
```

该事件在 challenge 的 MCP 响应当次即使行动者暂时无回合也立即返回，与之前
未读的公开 move 保持时间顺序；事件游标前进后不再重复。`summary` 和显示名可由
bootstrap 的参与者与结构化字段生成，因此不在裁判事件内重发。

任何查看者都只能得到自己的 `private_state.dice`。其他人的当前骰子、完整内部
`dice_by_player` 和查看者无关的私密合法行动不会出现在 Web 或 MCP 响应中。NPC
同样只得到自己的骰子；它只能从规则引擎给出的更高叫点和可用的 `challenge` 中
选择。1 点与其他点数完全同等计数。

终局时唯一赢家的逻辑 delta 为 `stake * (参与者数 - 1)`，其他每人 `-stake`，
总和为零。系统 NPC 的 delta 会保存在房间结算记录中，但不会创建永久钱包。

## 快艇骰子 `yahtzee`

人数为 2–6，支持系统 NPC，但 `supports_stakes=false`，只能开娱乐局。当前玩家每回合
至少掷一次、最多三次；第一次不保留骰子，后两次按零起始位置保留骰子：

```json
{
  "action": "move",
  "player_id": "ai-42",
  "room_id": "ABCDEFGH",
  "revision": 5,
  "move": {"action": "roll", "hold_indices": [0, 2, 4]}
}
```

`hold_indices` 也可换成长度恰好为 5 的布尔 `held_mask`。服务端只重掷未保留位置，
把合成后的五枚骰子和原始随机记录一起持久化；刷新不会调用随机源。掷骰后可在第三次
之前提前计分：

```json
{
  "action": "move",
  "player_id": "ai-42",
  "room_id": "ABCDEFGH",
  "revision": 6,
  "move": {"action": "score", "category": "full_house"}
}
```

类别为 `ones/twos/threes/fours/fives/sixes`、`three_of_a_kind`、
`four_of_a_kind`、`full_house`、`small_straight`、`large_straight`、`yahtzee`、
`chance`。一般回合不符合组合时自动记 0；即使当前本可得分，也可传
`zero:true` 明确划掉任意未用类别。公开 `board_state` 包含全员 `scorecards`、
`totals_by_player`、当前 `dice`、`held_mask`、`rolls_used` 和权威
`score_previews`。NPC 只从规则引擎给出的保留方案和计分动作中选择；第三次
掷骰后只剩计分动作。
裁判 `yahtzee_delta` 在 roll 后只补随机的五枚 `dice`；score move 已有类别，
因此只补服务端权威 `score`，实际发生额外快艇时再加非零 `yahtzee_bonus`。

上半区 63 分加 35 分。快艇格已记 50 后，每个重复快艇另加 100 分并累计进总分。
此时启用 Joker：匹配点数的上栏格未填就必须优先填；已填才能选下栏，葫芦/小顺/
大顺固定为 25/30/40，三条、四条和机会按骰点总和。快艇格已记 0 时不得 +100，
但仍按 Joker 规则填格。Joker 回合不能主动划掉有分的强制选项。全员填完 13 类后按
含奖励的总分排名；最高分并列即 `draw=true`，`tied_player_ids` 按稳定座位顺序返回。

## 21点 `blackjack`

人数为 2–6，支持系统 NPC，也支持非零 stake。虚拟庄家不是 participant，
没有玩家 ID 或钱包。每个房间只进行一局，使用服务端持久化的 4 副标准牌 shoe；
刷新、进程重启或重复读取不会重洗、重发或重抽。玩家按座位行动，只能从当前 viewer
的 `private_state.legal_actions` 原样选择：

```json
{
  "action": "move",
  "player_id": "ai-42",
  "room_id": "ABCDEFGH",
  "revision": 4,
  "move": {"action": "hit"}
}
```

停牌使用 `{"action":"stand"}`。第一版没有 split、double、insurance 或 surrender。
A 自动按 1/11 取不爆牌最优值；自然 Blackjack 仅指首两张 A + 10 值牌。所有玩家
结束后，庄家在同一次权威状态推进中翻暗牌并补牌，软 17 停牌（S17）。自然牌胜过
庄家非自然 21，同为自然或普通同点时推和。

公开 `board_state.players` 含每名玩家已经发出的牌、soft/hard 点数、手牌状态与终局
outcome。庄家阶段前，`board_state.dealer.hand[1]` 恒为 `{hidden:true}`；投影不返回
原始 `cards`、shoe 计数或任何 `card_id`，增量 move 事件也只包含 `hit/stand`。
hit 的裁判 delta 只补随机 `new_card`；stand 不发重复 delta，最后一家结束后才一次补庄家亮牌/补牌与 outcomes。
`private_state` 至少包含查看者自己的权威 `hand`、`value`、`status` 和
`legal_actions`。NPC 只能选择同一份服务端合法行动，不接收或推导 shoe 内容。
正常结算和房间级终局都会翻开庄家暗牌，但仍不公开 shoe 或 `card_id`。认输席记为
`loss/-stake`；只要仍有参与者可对庄家完成该手，房间继续而不提前结算其他席。

由于每名玩家分别与庄家比较，通用单赢家字段以 terminal `draw=true` 收口；真实结果
在 `room.result` 与公共 `board_state.game_result` 中完整返回：

```json
{
  "draw": true,
  "terminal_result": "blackjack_dealer_comparison",
  "result_text": "21点结算：1 胜 · 0 负 · 1 推和",
  "outcomes_by_player": {
    "human-1": {"outcome": "win", "result_text": "自然 Blackjack，胜"},
    "ai-42": {"outcome": "push", "result_text": "与庄家同点，推和"}
  }
}
```

实际 outcome 还包含 `total/soft/natural_blackjack/bust`；庄家摘要包含相同的点数与
自然牌/爆牌标记。每席独立结算为胜 `+stake`、负 `-stake`、推和 `0`，自然牌不另付
3:2；参与者总和可以非 0，差额由系统增发/回收。该非零和校验豁免只对 21点显式开启。
NPC 席进入 `settlement_deltas`，但不创建钱包。

## UNO `uno`

行动必须从自己的 `private_state.legal_actions` 原样选择；手牌只存在于自己的
`private_state.hand`。公共 move 可见打出的牌、选色与 UNO 声明，但摸牌永远不公开
牌面。普通出牌、摸牌、抓 UNO 和接受 WDF 都可由前态与 move 推导，不再发裁判
delta。只有 WDF challenge 依赖未公开的出牌前手牌，因此当次补一次
`challenge_succeeded/penalized_player_id/draw_count/deck_count`。
`pending_wild_draw_four.was_legal` 和任何新摸牌身份都不会进入公共投影或
事件。仅真实终局会发布按原手牌稳定顺序排列的 `terminal_hands`，牌堆内容仍隐藏。
完整功能牌与终局规则仍以 bootstrap `rules_text` 为准。

## 干瞪眼 `gandengyan`

只能从自己的 `private_state.legal_actions` 选择 `play` 或 `pass`。出牌 move 的
`card_ids` 在打出后成为公开信息，牌型、倍率和手牌张数可由前态与该 move 推导，
普通出牌不再重发裁判 delta。其余人全过后发一次 `trick_end`，只含本墩赢家、
各席非零摸牌张数和剩余牌堆数，绝不包含新摸牌身份。炸弹、跟牌与筹码倍率细则看
bootstrap 规则。带 stake 时每名输家按 `stake × 剩余手牌张数 × multiplier` 扣除，
炸弹使倍率翻倍且倍率最高 8 倍，赢家获得所有负值的绝对值之和。仅真实终局会发布各席
`terminal_hands`，按该游戏牌力从左强到右弱排列；牌堆剩余内容继续隐藏。

## 开火车 `train_cards`

2–6 人，只有一个权威动作 `flip`。进行中牌堆顺序始终隐藏；裁判增量只补本次随机翻出的 `revealed_card`，公开牌列、同点收牌和各席剩余张数均由前态与该牌推导；终局时才附加 winner 或 draw_reason。仅真实终局以 `terminal_hands` 公开各席剩余牌，未翻牌堆仍不公开。大小王共同按“王”匹配，不是万能牌；本项目采用 54 张严格轮流版。调用方不得预知或推算下一张牌。带 stake 时每名败者扣一个 stake，唯一赢家获得其余席位的总和；循环/动作上限导致的平局全部结算 0。

## 斗地主 `doudizhu`

固定三人。叫分和出牌都只从本人的 `private_state.legal_actions` 原样选择。牌型识别、比较和合法组合由 vendored `onestraw/doudizhu` 0.1.5 语义提供；物理 `card_ids` 由服务端绑定，调用方不得自行枚举。普通叫分和 pass 的公开变化已全在 move 与前态中，不重发裁判 delta；若出牌 move 已带 `card_ids/pattern_type` 也不重复。兼容只提交短 `action_id` 的调用方时，裁判仅补本次公开的 `card_ids/pattern_type/pattern_label`。只有确定地主时另一次补 `landlord_player_id` 和随机公开的三个 `bottom_card_ids`。地主确定前 3 张底牌隐藏，确定后公开；进行中其他人的手牌只显示张数，真实终局才以 `terminal_hands` 按高到低公开各席剩余牌。`pass` 仅在当前牌墩允许时出现。带 stake 时最终单位为 `stake × multiplier`：炸弹或王炸继续使倍率翻倍，但最终 multiplier 最高 16；地主胜收两份、两农民各付一份，农民胜反向结算。认输者所在阵营立即判负并按最高 16 的 capped multiplier 结算；地主未定时采用确定性三人 forfeit：认输者 `-2×stake`，其余两席各 `+stake`。

## 掼蛋 `guandan`

固定四人、对家组队、两副牌，房间是一场从 2 打到 A 的完整升级赛。运行时规则核心为 vendored `rlcard-guandan` v0.1.0。MCP 的 `private_state.legal_actions` 为 `guandan_parametric_v1`：`hand` 是稳定索引的牌面列表，`options` 每行按 `fields` 给出 suffix/kind/pattern/main_rank/size/wild_count/suit 和一个可提交示例索引组。选择任一符合该权威语义项的本人手牌索引，排序后用 base36 且以逗号连接，提交 `action_id_prefix + suffix + "." + indexes`；pass/接风等无牌动作省略点号与 indexes。服务端将前缀绑定当前全部核心合法集，并把参数动作还原为实际核心 `action_id`；所有花色、牌面、赖子用法和牌型解释均可选，示例不限制其他合法索引组。不要构造未发布的语义项。进贡/还贡、抗贡、接风、级牌和升级状态都由服务端持久化；进行中他人手牌不进入 bootstrap、delta 或 full_state，真实终局才以 `terminal_hands` 公开剩余牌。带 stake 时获胜队两人各 +stake，败方两人各 -stake；任一席认输即其所在队判负。

## 炸金花 `zhajinhua`

2–6 人，52 张无王。本人牌在执行 `peek` 前即使对自己也以牌背处理；看牌后只进入自己的 `private_state`。跟注、加注、弃牌和比牌必须直接采用 `private_state.legal_actions` 给出的 `cost/unit/target_player_id`。牌型 evaluator 基于 vendored Golden Flower MIT 核心；本地固定 A23 最小顺子、不启用 235 吃豹子、花色不破平。强制比牌终局按规则亮牌，弃牌或未展示席位不因复盘而强制公开。每席虚拟投入封顶 32；带 stake 时每个虚拟下注单位价值一个 stake，终局按各席实际投入零和结算；精确并列退还，单席最大真实亏损 32×stake。

## 军棋 `junqi`

固定双人暗棋陆战军棋。布阵阶段 `legal_actions` 只列出 `shuffle/ready/auto_setup`；手工 swap 保留在 `junqi_setup_v1` 的 `legal_action_spec.swap`，按 `own_squares` 选两个己方占用格，并用 `destinations_by_rank` 同时检查两枚棋交换后的布阵限制，提交 `{"action":"swap","from":"...","to":"..."}`。进入行棋后仍从权威 `move` 列表提交 `from/to`。对手未公开棋子的军衔在进行中不会出现在公共棋盘、MCP bootstrap/delta/full_state 或 legal actions；碰撞、司令阵亡、军旗等按规则必须公开时才揭示，真实终局再公开棋盘上全部剩余军衔。棋子碰撞、棋盘拓扑、铁路/工兵转弯和布阵合法性由 vendored `online-junqi` 核心判定。带 stake 时按双人标准赢家 +stake、败者 -stake。

## 德州扑克 `texas_holdem`

2–6 人 no-limit Hold'em，一房一手，每席固定 1000 内部筹码，盲注保持 5/10（100BB）。进行中底牌只在本人的 `private_state`；公共状态包含按钮/盲注、公共牌、各席剩余 stack、已投入、fold/all-in 状态和可审计的 pot/side-pot。普通下注信息已在 move 中，不重发整个 players/pots；自动转街时才补 `street/board_added`，真正摊牌时补 `showdown`。showdown 只公开按规则应亮的仍有资格席位，fold/muck 不因终局复盘而强制公开。只能从 `private_state.legal_actions` 选择 `check/fold/call/bet/raise/all_in`；`bet/raise.amount` 表示本街下注总额，必须位于服务端给出的 `min_amount..max_amount`。PyPokerEngine 提供牌桌、下注事务、牌力与 side-pot 核心，本地适配锁定 heads-up 顺序、short all-in 与 raise reopening 等语义。带 stake 时 stake 仍是每席完整真实买入而非内部筹码单价；真实总买入池为人数×stake，终局按最终内部栈比例分配，换算基准为每 1000 内部筹码对应一个 stake，单席最多亏 stake。

## 围棋 `go`

固定双人 19×19，黑先，白贴 7.5，禁止自杀，positional superko，中国面积计分。MCP 不枚举 361 个 play JSON：`coordinate_rows_v1.columns_by_row` 用 `0-18` 或逗号分隔区间列出每一行当前合法列，用其 `action` 与列出的 `row/col` 提交；`pass`/`confirm_score` 仍直接列在 `legal_actions`。连续两次 pass 后进入死子确认阶段，可 `toggle_dead(row,col)`，任一方修改死子集合会清空双方确认，双方对同一集合执行 `confirm_score` 后才结算。Tenuki 规则核心负责落子、提子、劫/超级劫、死子分组和计分；完整历史随状态持久化以保证刷新后 superko 不丢失。带 stake 时按双人标准赢家 +stake、败者 -stake，和棋双方 0。

## 麻将 `mahjong`

固定四人、136 张无花、东一局，采用国标 8 番起和的首版配置。进行中摸牌、手牌与本人可响应动作只进入 `private_state`；公开状态只含弃牌、副露、牌数、轮次/座风以及规则要求公开的信息。`mahjong_delta` 是真正 patch：每次只发生变化的 `phase/turn_player_id/wall_remaining`、单张 `discard_added/discard_removed`、单个 `meld_added/meld_changed`与紧凑 `response`；不每步重发全部牌河或副露历史。终局才一次附 `game_result/terminal_hands`。调用方以 bootstrap 为底按事件顺序应用 patch，丢失时用 `full_state` 恢复。真实终局以 `terminal_hands` 公开各席剩余牌并补全暗杠，牌墙内容继续隐藏。吃、碰、明杠、暗杠、加杠、抢杠和、自摸、点炮及响应优先级由服务端状态机管理，胡牌/番数调用 vendored PyMahjongGB `MahjongFanCalculator`，向听调用 `MahjongShanten`。调用方只能提交本人当前 `action_id`，不得自行算番或构造响应。首版无花、单手结束、单和制。带 stake 时自摸由三家各付 stake；点炮或抢杠和由来源玩家独付 3×stake；荒牌全部 0；认输按娱乐筹码包赔，认输者 `-3×stake`、其余三家各 `+stake`。

## 翻翻棋 `banqi`

`flip` 的普通 move 只有坐标；紧随的 `banqi_delta` 给出该格实际翻开的公开棋子，首翻
还给出行动者定色。`move` delta 给出移动棋、公开被吃棋或 `captured="hidden"` 哨兵，
不会泄露暗子真实身份。full_state 的 8×4 `board` 用 `hidden` 表示所有仍未翻开的棋。
仅真实终局会返回全部棋子身份，用于终局棋盘复盘。

## 飞行棋 `aeroplane_chess`

`roll` 后看 `aeroplane_delta` 的骰点、连续 6、`movable_plane_ids`、auto-pass、第三个
6 惩罚与退回机场列表，再从权威行动中选机。`move` delta 给出 from/to、沿途落点、
跳跃/跨盘、碰撞击落和到家结果。full_state 保留全部飞机的当前 `route_step/zone` 与
当前 legal actions，不重复 bootstrap 的四色固定路径表。

## 中国跳棋 `chinese_checkers`

普通 move 的 `from/to/kind` 已足够确定性重建棋面；跳跃不吃子。每一段跳跃都沿六方向
直线取该方向遇到的第一颗任意玩家棋子为跳板；起点与跳板之间若有 k 个连续空孔，只有
跳板另一侧也有 k 个连续空孔且对称落点为空时才能跳。k=0 即相邻跳；连续跳可混合相邻跳与
等距跳，但不能重复落点或混入普通一步。

bootstrap 提供 121 孔的固定 node 坐标和营区，调用方应缓存。full_state 只重发当前 `pieces`、
营区归属、进度和可走终点；其中 `legal_moves` 压成可直接提交的 `from/to/kind`，不重复
canonical path、固定 `nodes/camps` 或历史。调用方必须从服务端的 `legal_moves` 选择终点，
无需也不得自行提交连跳 path。
六人桌有人认输时不会进入非法五人状态：认输者 `-5×stake`，其余五席各
`+stake`。

## 国际象棋 `chess`

移动用零起始 `from_row/from_col/to_row/to_col`，升变必须保留 `promotion=q|r|b|n`。
当前快照的 `legal_actions` 是可直接提交的权威列表，并保留棋盘、FEN、将军/将死、
重复局面与半回合计数；不重复 verbose `legal_moves` 或棋谱。若列表出现
`{"action":"claim_draw"}`，表示当前已满足三次重复或 50 回合规则，可提交该动作申和；
五次重复、75 回合及其他死局仍由服务端自动裁决。

## 象棋 `xiangqi`

象棋固定为 1 个人类 + 1 只真实绑定小机，先手执红，不支持系统 NPC。合法走棋、
将军、将死、困毙和胜负由 vendored `xiangqi.js`（BSD-2-Clause）判定。
第一版不裁决竞赛级长将长捉责任。

棋盘坐标为零起始真实坐标：`row=0` 是黑方底线，`row=9` 是红方底线，
`col=0..8`。移动必须带当前房间 revision：

```json
{
  "action": "move",
  "player_id": "ai-42",
  "room_id": "ABCDEFGH",
  "revision": 3,
  "move": {
    "from_row": 0,
    "from_col": 0,
    "to_row": 1,
    "to_col": 0
  }
}
```

`state` 的 `board_state` 包含 10×9 `board`、`marks`、`fen`、`turn_color`、
`in_check`、`in_checkmate`、`in_stalemate`、`legal_moves`，走棋后还会包含
`move_history` 和 `last_move`。
棋子编码为颜色与棋种，例如红车 `r:r`、黑将 `b:k`。`legal_moves` 已过滤马腿、
象眼、炮架、九宫、过河、将帅照面和送将等非法着法，是调用方选择行动的唯一合法
目标真源；不要在客户端或提示词里重写一套规则。落子事件另带服务端生成的
`move_label`，用于历史展示。

## 西洋跳棋 `checkers`

西洋跳棋是固定双人的 8×8 English draughts，不是 10×10 国际跳棋。棋子只在深色
格行动，普通棋向前斜走/跳，王棋可前后斜走/跳但不能远距离飞行；有吃必吃。一次
跳吃后若同一枚棋仍可吃，房间会继续保持当前 `current_player_id`，并只在
`board_state.legal_moves` 返回该棋的下一跳。普通棋跳到王线时升王且该手结束。
同一棋子位置、普通棋/王棋身份且轮到同一方的完整局面第三次出现时自动判和；若双
方各自此前连续 40 手都没有把普通棋向王线推进、也都没有吃子，也自动判和。王棋
的普通移动不算普通棋推进；连续多跳只在整手完成后结算一次和棋计数。

每次移动以及多跳中的每一跳都带当前 revision 单独提交：

```json
{
  "action": "move",
  "player_id": "ai-42",
  "room_id": "ABCDEFGH",
  "revision": 3,
  "move": {
    "from_row": 5,
    "from_col": 0,
    "to_row": 4,
    "to_col": 1
  }
}
```

棋子编码为 `X:m`、`O:m`、`X:k`、`O:k`。`m` 是普通棋，`k` 是王棋；X 先行并
向 row 减小方向前进。调用方必须从 `legal_moves` 原样选择动作，不能在 NPC 提示词
或客户端重写强制吃子、连跳、升王与胜负规则。
