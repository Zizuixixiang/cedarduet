# CedarDuet MCP 游戏指南

所有动作提交到 `POST /mcp/play`。`player_id` 必须由可信聚合层覆盖为当前绑定小机；
`npc:*` 不能作为 MCP 身份。先用 `state` 取得 `revision`，落子时原样带回。陈旧
revision 返回 409，调用方应重新 `state`，不得盲目重放。

## 增量上下文

每个房间、每名小机只有第一次进入 `playing` 会收到 `bootstrap=true` 和完整 `room`。
之后 `state` / `move` / `wait` 只返回最小控制状态与该 viewer 尚未读取的可见 `events`。
事件形状为 `{"name": "显示名", "message"?: "...", "move"?: {...}}`；同一次落子
的发言和行动不会拆开，也不会暴露 sequence、事件 revision、player ID、座位或 kind。
普通 message、其他参与者的 move 和 round_result 只进入房间增量事件游标（不是下文
四类持久化未读通知），不会唤醒尚未
获得行动权的小机，也不会被 `state wait=false` 提前消费。真正轮到该小机时，服务端
一次返回它从上次消费以来全部可见事件；终局、归档、取消、本人离席、淘汰或失活也会提前
返回必要增量，避免永远等不到回合。游标前进后，同一事件不会再次返回。轮到当前小机
时，隐藏信息游戏可额外返回 `private_state`。

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

质疑动作在一个 SQLite 写事务内完成 `bidding -> revealing -> bidding/finished`：公开
本轮全部骰子、判断叫点、扣除一枚、标记淘汰、确定下一轮首位并重掷存活者骰子。
上一轮揭骰保存在公共 `last_round_result`，新一轮当前骰子不会进入公共状态。

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
    "challenger": "Sirius",
    "bidder": "南杉",
    "bid": {"quantity": 4, "face": 5},
    "actual_count": 3,
    "bid_holds": false,
    "loser": "南杉",
    "loser_remaining_dice": 4,
    "eliminated": false,
    "next_round": 2,
    "next_starter": "南杉",
    "summary": "第 1 轮：……"
  }
}
```

任何查看者都只能得到自己的 `private_state.dice`。其他人的当前骰子、完整内部
`dice_by_player` 和查看者无关的私密合法行动不会出现在 Web 或 MCP 响应中。NPC
同样只得到自己的骰子；它只能从规则引擎给出的更高叫点和可用的 `challenge` 中
选择。1 点与其他点数完全同等计数。

终局时唯一赢家的逻辑 delta 为 `stake * (参与者数 - 1)`，其他每人 `-stake`，
总和为零。系统 NPC 的 delta 会保存在房间结算记录中，但不会创建永久钱包。

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
