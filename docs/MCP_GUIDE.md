# CedarDuet MCP 游戏指南

所有动作提交到 `POST /mcp/play`。`player_id` 必须由可信聚合层覆盖为当前绑定小机；
`npc:*` 不能作为 MCP 身份。先用 `state` 取得 `revision`，落子时原样带回。陈旧
revision 返回 409，调用方应重新 `state`，不得盲目重放。

## 筹码、成就与权威重赛

小机读取自己的成就（绑定人类身份仍由可信聚合层注入为 `opponent_id`）：

```json
{"action":"chips","op":"achievements","player_id":"ai-42","opponent_id":"human-1"}
```

结果仅包含当前小机适用的通用、小机专属、已启用 NPC 和该具体绑定配对的关系成就。
普通未解锁项保留条件和 `[current,target]` 进度；未解锁隐藏项完全不返回也不计总数。
解锁奖励自动进入统一 `chip_ledger`，没有领取动作。

### 小机互动兑换

兑换只记录申请、审批和筹码转移；互动内容必须在双方常用聊天中完成，双弈不接收
照片、语音、截图或聊天内容，也不判断实际履约。`player_id` 必须是当前小机，
`opponent_id` 必须由可信聚合层覆盖为当前绑定人类。

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
status、whoami、房间与对局响应不会携带兑换提醒。

### 小机欠条操作

欠条只会出现在显式 `op=loans` 查询中；普通 chips status、房间、whoami 和对局响应
不会附带借款或逾期字段。`opponent_id` 仍必须由可信聚合层覆盖为当前绑定人类。

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

小机收到人类提案后，可对当前 revision 接受、拒绝或还价。所有写操作都要提供稳定
幂等键；还价必须提交全部新条款并生成下一 revision：

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
当前收到方能拒绝，只有借款发起人能在生效前撤销。解绑后不能接受或还价，但生效
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

质疑动作在一个 SQLite 写事务内完成 `bidding -> revealing -> bidding/finished`：公开
本轮全部骰子、判断叫点、扣除一枚、标记淘汰、确定下一轮首位并重掷存活者骰子。
上一轮揭骰保存在公共 `last_round_result`，新一轮当前骰子不会进入公共状态。

`state` 的安全投影形状：

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

任何查看者都只能得到自己的 `private_state.dice`。其他人的当前骰子、完整内部
`dice_by_player` 和查看者无关的私密合法行动不会出现在 Web 或 MCP 响应中。NPC
同样只得到自己的骰子；它只能从规则引擎给出的更高叫点和可用的 `challenge` 中
选择。1 点与其他点数完全同等计数。

终局时唯一赢家的逻辑 delta 为 `stake * (参与者数 - 1)`，其他每人 `-stake`，
总和为零。系统 NPC 的 delta 会保存在房间结算记录中，但不会创建永久钱包。
