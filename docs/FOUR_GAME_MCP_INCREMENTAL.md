# 四款 MCP v2：有状态行动增量

仅 `monopoly/rummikub/bomb_plane/carcassonne` 使用 `app/mcp_minimal.py`。
飞行棋为设计参考，飞行棋、UNO、五子棋等其他游戏仍走原协议。
Web 投影、NPC 策略、动作校验与原始 `room_messages` 不变。

## 响应与兼容

普通成功回复只有 `r`（权威房间 revision），以及确实有内容的 `events`、`private`。
请求继续传原来的 `revision`，值取 `r`；不要求客户端支持新的请求字段。
没有 `wait` 且无终局/离席状态表示可以行动；等待时 `wait` 给行动者 ID。
炸飞机并发布阵额外给 `setup:open|locked`；锁定者 wait 指向尚未锁定的另一人。
`status` 只在终局、离席或网关需要内部续等的 `still_waiting` 心跳保留。
活动受限者仍有 `participant_status`，终局仍有结果和结算。

首次响应 `protocol:2, bootstrap:true, room` 提供规则、编码、静态资料与完整安全局面。
`protocol_guide` 说明本页的编码。没有 v2 上下文的旧房、第一轮已 bootstrap 的小机，
自动收到明确 `resync` 提示及一次完整 v2 bootstrap，必须替换旧上下文。

`state(full_state=true)` 在已有 v2 上下文时返回 `r, full_state:true, snapshot`。
full_state 可独立恢复：即使模型已丢失 bootstrap，snapshot 也包含完整安全棋面、
viewer_player_id、参与者 ID/name/seat/token/handle/status 映射、紧凑 rules、action_formats
及 protocol_guide。静态且决策必需的信息不能仅凭“首次发过”省略。定义只在
`app/mcp_recovery.py` 的 full_state 路径添加；不改变 bootstrap、普通回复和游标行为。
规则按主题和数值整理，不机械复制原始长 rules_text。

- 大富翁：完整 40 格定义、价格/租金表/建筑/抵押/赎回参数及全部公开动态状态；
  当前 auction/trade/debt 和已抽出的 last_card_events；本人 jail_cards；
  当前 viewer 的 legal_actions/trade_options 及全部动作参数。
- 拉密：完整 melds/meld_kinds/joker_roles、公开牌数/开局/阻塞/行动权/result；本人完整 hand；
  tile_encoding、106 张实体牌编码、组/顺子/边界/joker/30 分开局/完整桌面提交规则。
- 炸飞机：10×10 坐标、北向相对机头的 10 个偏移及 N/E/S/W 旋转、重叠与机头唯一规则、
  全部布阵/攻击格式、shots 编码和反馈语义；本人 planes；终局（含认输）双方 revealed_planes。
- 卡卡颂：完整 board（含当前随从）/scores/supply/flow/turn/result/current_tile/deck_count/discarded；
  A–X topology、坐标/端口/region 含义、计分和回收规则。placements 全集继续按需查询；
  它只能替代合法落点列表，不能替代地图拓扑。非行动者同样能通过 full_state 理解整张地图。

不会暴露他人手牌、进行中的对手飞机或隐藏牌堆顺序。只从
project_mcp_snapshot_for_viewer 的安全输出恢复，不接触持久化原始 state。
旧插件 delta_format 被正确的 v2 protocol_guide 替代；同义字段和派生字段仍沿用已有
安全投影。完整私有状态保持 hand/planes/jail_cards 的原形状；不新增建议动作查询系统。
未知/v1 上下文即使显式 full_state，仍先返回原有 bootstrap；本次没有更改 bootstrap 内容。

**这四款 v2 的 full_state 原子记录快照覆盖的行动边界**；之后不再
重放已包含的行动，所以普通事件不需要逐条 revision。full_state 不附带旧 events，
但事件游标停在首条未交付文字之前：聊天、动作附言及非棋局文字通知仍在下一次普通
响应中按序恰好交付一次，即使此时正在等待他人行动。重复 full_state 不会吞掉文字；
并发读取使用同一事务串行消费，原始消息和可见范围不修改。
旧游戏的 full_state 游标语义保持不变。读落点查询不推进事件游标。

正常 `state` 仅在该查看者需要响应时消费，等待不提前消费；move 的权威结果立即消费。
每次消费在同一 SQLite 写事务中读取局面/消息、计算差分、推进游标、保存私有基线，
保证该响应的 r、事件和手牌来自一致时点。所有可见对手行动完整有序返回，没有截断。
自己的权威行动也在 events 内，仅提交动作本身可确定的拉密手牌移除不重复。
同查看者并发读取只能由一次读取消费同一批事件。网络丢包或上下文丢失用 full_state
恢复；这不是传输层的确认重试协议，不能重放已成功提交的 move。

新增表 `mcp_minimal_contexts` 保存每房间/查看者协议版本、公共差分基线、本人私有基线，
房间删除时级联清理。初始化建表幂等，无历史消息重写，也不改账号、游戏存档或钱包。

## 事件格式

事件通常为短数组。聊天为 `{actor,message}`，保留原文字；actor 是稳定玩家 ID，
不会用显示名区分同名玩家。按数组顺序应用，不把整批当成最后一个对手的行动。
缺省键不变，null 清除。生命周期 resign/leave 在相应行动后附一次 `public_state`
修复当前公开局面；按序应用事件后再应用修复视图，避免每轮重复大账本/地图。

### 大富翁

`[actor, action, delta?]`。每个行动（含 resign/leave）将 action_seq 加 1；从 bootstrap/full_state
提供的 action_seq 开始。r 与 action_seq 是不同的计数，不得互换。

- `p` 行：`[player_id,cash,position,bankrupt,jailed,jail_turns]`，按玩家 ID 替换。
- `t` 行：`[tile_id,owner,level,mortgaged]`，按地块 ID 替换。
- `next` 替换 turn_player_id，其他字段按键替换，例如 phase、dice、auction/trade/debt。
- 卡牌事件保留随机结果、效果与文本，省去重复的 event_id/action_seq；普通自然语言 note
  可从骰子、位置、现金、地产、卡牌效果推导，不再重复。
- 首个差分以真实 bootstrap 状态为基线，不再补发所有未变化地产。
- 静态地块 ID、名称、价格、租金、建造成本在完整上下文中；动态现金与产权靠事件累积。
- `private.jail_cards` 仅数量改变时发送。可交易对象、资产操作、现金限额、拍卖出价范围
  可从 phase、账本和规则推导，不再重复整套 legal_actions/trade_options。
- bootstrap/full_state 的 `action_formats` 列出所有动作字段；每个 move 带 action_seq。

### 拉密

- `[actor,"draw"]`：该玩家手牌数 +1，牌堆数 -1，清空 blocked；不会公开其摸到的牌。
- `[actor,"pass"]`：把该玩家加入 blocked；牌堆为空才可 pass。
- `[actor,"meld",{table_patch,joker_roles?}]`：table_patch 为
  `{size,set:[[index,tile_ids,kind],...]}`。调整桌面数组长度，再覆盖指定组合。
  newly tabled IDs 数量从该玩家 hand_count 扣除，opened=true，清空 blocked。
  joker_roles 改变时给权威解释（包括不唯一的颜色集合）；为空对象表示清除。
- 每次动作后转至下个 active 玩家，终局以 status/result 为准。
- 私有 `+` 新增实体牌，`-` 移除实体牌；完整 hand 仅 bootstrap/full_state。
- 自己成功提交 meld 后，先从已知手牌移除自己提交桌面中的 IDs；服务器只对额外差异
  返回 `-`。其他途径（例如网页/代下）导致的手牌移除仍通过 `-` 完整交付。
- 动作仍提交完整最终 `melds` 与可选 `kinds`；服务器玩法/万能牌校验不变。
  动作定义在 bootstrap/full_state 给出，不再主动计算输出 suggested_move/table suggestion。

### 炸飞机

攻击为 `[actor,cell,result]`，result 为 miss/hit/head。对方攻击自己的结果同样发送。
正常轮流攻击，三个 head 获胜；权威 winner/result 在终局返回。
布局只在完整上下文或修改后通过 `private.planes` 给本人；锁定动作若布局没变则不重复。
setup 标志只在布阵阶段出现；setup 消失且未终局表示进入攻击阶段。
完整历史 shots 只在 bootstrap/full_state，按攻击者及结果分组；Web 终局展示不受影响。

### 卡卡颂

`[actor,tile_id,x,y,rotation,meeple_region_or_null,next_tile_or_null,effects?]`。
拓扑 A–X 与坐标/旋转编码在 bootstrap/full_state，事件只给板块 ID。

- 向已知 board 添加板块，meeple 非 null 时归 actor。
- effects.scoring 保留全部计分与 returned `[x,y,owner]`，后者移除对应随从。
- effects.scores/supply 按玩家合并；effects.discarded 为本次弃牌，累积到已知弃牌数组。
- 抽到 next tile 时 deck_count 减 1 再减本次 discarded 数量；终局效果可显式覆盖 deck_count。
- 每次动作后转至下个 active 玩家；不重复 board/decision_context。

普通路径不发送合法落点全集。保留现有 state + move 对象，不增加顶层请求字段：

```json
{"action":"state","room_id":"ABCDEFGH","move":{"query":"placements","x":1,"y":0,"rotation":2,"meeple":null}}
```

返回 `{revision,valid,placements:[[x,y,r,[available_region_ids]],...]}`。
valid 判定指定坐标、可选旋转及可选随从；不填旋转时检查任一合法旋转。
最多返回 8 个按 Manhattan 距离及坐标稳定排序的附近候选。`meeple:null` 始终表示不放。
全集显式使用 `state(move={query:placements,all:true})`。
查询仅供当前行动者；参数严格检查，无状态修改、游标消费、随机抽牌。
查询 revision 仅用于检查是否过期，不替代最近消费响应的 r；过期时先同步再决策。
不允许将查询与 wait/full_state/message 合并，防止查询意外消费或发送聊天。
根平台需要配套应用交付中的 `root-guide-query.patch`，让 state 的 move 字段透传。

## 通知与网关

原 `attach_mcp_unread` 只在未读总数 >0 时返回，但同一未读会每轮重复。
v2 用 `mcp_notification_delivery` 按 AI 保存已提示的通知 ID 高水位：有新的未读通知才
再次带 unread/unread_hint，**不写 notifications.read_at**。同数量的新通知也能被发现。
rooms、chips 及其他游戏仍可照常读取/消费通知；根平台非 duel 工具的提醒及强制公告不变。

空的 still_waiting 心跳保留以兼容现有网关与 local MCP 内部续等。含棋局事件、私有差分、
bootstrap 或新通知的回复会去掉内部自动重试标志，防止网关吞掉已交付的内容。
根 MCP 本来就对 duel play 跳过额外的重复对局提醒，不需要禁用真正通知功能。

## 测量

`scripts/sample_duel_incremental_tokens.py`：临时 SQLite、真实 ASGI `/mcp/play`、固定种子
11/37、既有 NPC 决策策略，无局面修改。第一轮 after 原始采样作为本轮 before。
请求逐条核对、完整快照核心局面核对由 `scripts/compare_duel_incremental_tokens.py` 完成。
`DUEL_TOKEN_REPORT_ROOT=artifacts/token-opt-v2` 选择本轮报告。
`scripts/audit_duel_minimal_tokens.py` 输出每字段边际 token、配对 JSON、六人最大事件批次。

完整数字、原始压缩 JSONL、验证记录与仅第二轮的最小补丁见 `artifacts/token-opt-v2/`。

第三轮仅 full_state 的独立补丁、配对实测与恢复测试见 `artifacts/third-round/full-state/`；
基线为第二轮结束时的工作树，不重复第一、二轮修改。

审计后的完整性小修补独立产物见 `artifacts/four-game-fullstate-completeness/`：
基线为第三轮结束时的工作树，保留全部旧补丁与采样；丢弃 bootstrap 的恢复测试、
普通/启动响应逐字段不变证明、中后期 token 统计及独立补丁均在该目录。

合并态沿用当前生产大富翁挂起报价规则：报价不打断提案方或其他玩家；接收方在之后的
正常回合开始时先响应，任一时刻仅一个挂起报价，条件失效自动取消。历史 phase=trade
存档仍兼容即时响应。full_state 的 trade、当前 legal_actions 与恢复 rules 描述该规则。
