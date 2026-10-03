# 卡卡颂接入与规则核验记录

`game_type=carcassonne`，`category=tabletop`，2–5 人、推荐 4 人。实现版本 `classic72-farmers-v1`：经典 72 块（含起始 D 一块）、每人 7 名随从，保留农夫；不含河流、修道院长或其他扩展。服务端、网页、MCP、本地 NPC 均已实现。

**2026-10-02 已串行整合到主工作树，未部署。** 根 MCP 的真实模块导入、临时账号路径 Token/Bearer、绑定身份、普通/邀请链路和异步 prepare/finalize 已通过；Chromium 360/430/1280px × 2/5 人实际操作已通过。出版商图例复核的证据来源及仍未读取的 v3 主 PDF 见下文。完整日志和截图见根目录 [两款整合验收记录](../../../docs/DUEL_NEW_GAMES_ACCEPTANCE.md)。

## 来源、版本与核验边界

核验日期：2026-10-02。代码、中文说明、SVG/CSS 均自行编写，没有引入第三方游戏代码或品牌插画。下列资料是规则参考，不是开源代码许可证，因此本次没有新增第三方依赖或修改 THIRD_PARTY_NOTICES。

- [Hans im Glück 产品页](https://www.hans-im-glueck.de/en/game/carcassonne/)：已读，确认基础游戏 2–5 人。
- [2024/11 v3 英文基础规则](https://www.hans-im-glueck.de/wp-content/uploads/2024/11/Carcassonne-v3_Rulesheet_EN_web.pdf)：指定来源；工具拒绝约 11.4 MB 的文件，未成功读取完整正文或图例。
- [2024/11 v3 英文补充](https://www.hans-im-glueck.de/wp-content/uploads/2024/11/Carcassonne-v3_Supplement_EN_web.pdf)：已读文本，确认农夫留至终局、道路/城市分田、每个相邻已完成城市 3 分、多数与并列。主助手已目视第 1 页农夫图例，结果由本轮用户消息转述；本轮整合者未声称亲自查看该原图。
- [出版商商店提供的 Big Box 2010 英文规则](https://cundco.de/media/84/a4/ac/1773930056/CC_BigBox_2010_Rule.pdf?ts=1773930056)：已读文本；基础章节第 2–5 页核对道路/城市/修道院、未完成计分、弃掉不可放板块并重抽；PDF 第 17 页（索引 16，印刷 B1）的 A–X 数量及拓扑由主助手目视复核，本轮依据用户转述与代码、fixture 对照。只采用其中经典基础部分。没有把扩展牌混入牌堆。
- [Dized 无合法落点说明](https://rules.dized.com/game/xkm9dRfpQqisrXG-hwoGzQ/2VfdR6erQqixUhwwHiKI1g/no-legal-tile-placement)：辅助核对弃牌重抽。
- [Russ Williams 板块清单](https://russcon.org/RussCon/carcassonne/tiles2.html)：仅作边口总量交叉检查（城市 79、道路 94、田地 115），不是内部拓扑的最终权威依据。

图例证据分层保留：隔离开发者读过上述规则文本；主助手已目视 Big Box B1 和 v3 Supplement 第 1 页；本轮整合者根据用户传入的复核记录核查代码与断言。复核覆盖 A 绕院相连田地、F/G 贯穿单城分田、H/I 分离双城同田、D/J/K 道路分田、L/W/X 各支路独立及 S/T 入城道路两侧田地。没有把所有同类区域自动相连。

补充中的农夫结果为：红蓝并列接触已完成 A/B/D 各得 9，未完成 C 不计；黑方多数接触 4 座已完成城得 12；黄方角落接触 A/B 得 6。新增测试用实际连通板块构造相同计分条件，同时核查库存回收；它不是出版商图形的逐格重建。**约 11.4 MB 的 v3 基础 PDF 仍未完整读取，不能声称所有 v3 原图逐项验完。** 本轮经典基础版本及 72 块集合不变。

## 牌组与拓扑

数量向量 A–X 为：`2,4,1,4,5,2,1,3,2,3,3,3,2,3,2,3,1,3,2,1,8,9,4,1`。总数 72；D 的 4 块中一块固定起始，洗牌堆 71；共 6 座修道院、10 枚盾徽。边口总量为城市 79、道路 94、田地 115（合计 288）。代码独立测试断言该数量向量、边口总量和每一边口恰好覆盖一次。

坐标 x 向东、y 向南，允许负数。城市/道路口 `0/1/2/3=N/E/S/W`。田地半边口顺时针为 `0=北左,1=北右,2=东上,3=东下,4=南右,5=南左,6=西下,7=西上`；相邻边的两个田地半口反向配对。`c/r/f/m` 分别代表城市/道路/田地/修道院；同一 ID 内连通，不同 ID 不默认连通。旋转是顺时针四分之一圈 0–3，区域 ID 不变。

下表来自本实现的显式定义，数量与拓扑已对照主助手传入的 B1 视觉复核记录；本轮未独立重复打开该原图。`f[半口]→城市` 表示该片田地在本块接触的城市；`*` 为盾徽。

| 类型 | 数量 | 北东南西 | 区域与相邻城市 |
|---|---:|---|---|
| A | 2 | FFRF | r0[2]；f0[0,1,2,3,4,5,6,7]→无；m0 |
| B | 4 | FFFF | f0[0,1,2,3,4,5,6,7]→无；m0 |
| C | 1 | CCCC | c0[0,1,2,3]* |
| D | 4 | CRFR | c0[0]；r0[1,3]；f0[2,7]→c0；f1[3,4,5,6]→无 |
| E | 5 | CFFF | c0[0]；f0[2,3,4,5,6,7]→c0 |
| F | 2 | FCFC | c0[1,3]*；f0[0,1]→c0；f1[4,5]→c0 |
| G | 1 | FCFC | c0[1,3]；f0[0,1]→c0；f1[4,5]→c0 |
| H | 3 | CFCF | c0[0]；c1[2]；f0[2,3,6,7]→c0,c1 |
| I | 2 | CCFF | c0[0]；c1[1]；f0[4,5,6,7]→c0,c1 |
| J | 3 | CRRF | c0[0]；r0[1,2]；f0[2,5,6,7]→c0；f1[3,4]→无 |
| K | 3 | CFRR | c0[0]；r0[2,3]；f0[2,3,4,7]→c0；f1[5,6]→无 |
| L | 3 | CRRR | c0[0]；r0[1]；r1[2]；r2[3]；f0[2,7]→c0；f1[3,4]→无；f2[5,6]→无 |
| M | 2 | CCFF | c0[0,1]*；f0[4,5,6,7]→c0 |
| N | 3 | CCFF | c0[0,1]；f0[4,5,6,7]→c0 |
| O | 2 | CCRR | c0[0,1]*；r0[2,3]；f0[4,7]→c0；f1[5,6]→无 |
| P | 3 | CCRR | c0[0,1]；r0[2,3]；f0[4,7]→c0；f1[5,6]→无 |
| Q | 1 | CCFC | c0[0,1,3]*；f0[4,5]→c0 |
| R | 3 | CCFC | c0[0,1,3]；f0[4,5]→c0 |
| S | 2 | CCRC | c0[0,1,3]*；r0[2]；f0[4]→c0；f1[5]→c0 |
| T | 1 | CCRC | c0[0,1,3]；r0[2]；f0[4]→c0；f1[5]→c0 |
| U | 8 | RFRF | r0[0,2]；f0[1,2,3,4]→无；f1[0,5,6,7]→无 |
| V | 9 | FFRR | r0[2,3]；f0[0,1,2,3,4,7]→无；f1[5,6]→无 |
| W | 4 | FRRR | r0[1]；r1[2]；r2[3]；f0[0,1,2,7]→无；f1[3,4]→无；f2[5,6]→无 |
| X | 1 | RRRR | r0[0]；r1[1]；r2[2]；r3[3]；f0[1,2]→无；f1[3,4]→无；f2[5,6]→无；f3[0,7]→无 |

## 实现与公共接入

独立实现位于 `app/games/carcassonne.py`、`carcassonne_tiles.py` 和 `app/static/games/carcassonne.{js,css}`。公共代码只有插件注册、强制 revision、既有玩家组件的两人适配、图标/Token 粗估以及 guide 增量；没有修改其他游戏规则或专属渲染器，没有另造玩家卡、聊天、弹窗、第二入口。游戏列表和 tabletop 分类由既有 catalog 自动生成。

服务端初始化时用 `SystemRandom` 洗牌，完整牌序仅保存于内部 state；当前公开板块另存。每次行动先校验，再在副本上拼接、可选放随从、统一计分、轮换和抽牌，由既有 SQLite 事务保存；必须附当前 revision。拒绝无效动作、过期/重复提交、非本人行动，不会先放半块或提前消耗牌。不会把客户端指定的牌型或牌序当作输入。

连通图按 `(x,y,region_id)` 建立，跨真实边口合并，保留每块的分离城段、路口道路、路两侧田地。可放随从区域检查整个连通区域，包含新块的两个局部区域在外部重新接通的情况。计分按不同板块去重，盾徽独立累计；农田按相邻已完成城市的连通分量去重。完成区域内所有随从回收，只有多数/并列多数得完整分。农夫只在终局计分回收；最后一块的完成计分与终局未完成计分不会重复。

无合法落点时永久公开弃掉该块并继续抽牌，不跳过当前玩家；牌堆耗尽才完整终局。弃牌从 72 总量中扣除，所以正常终局地图可能少于 72 块。

动作：

```json
{"action":"place","x":0,"y":-1,"rotation":2,"meeple":"c0"}
```

`meeple:null` 为不放，其余为当前板块区域 ID。`private_state.placements` 每项 `[x,y,rotation,[可放区域ID]]`，没有可放区域也允许不放。`current_tile.regions` 给出当前牌拓扑；首次公共状态的 `board + topology` 给出已出现牌型。`carcassonne_delta` 仅给新增块、计分/回收、新当前牌、数量和结果；不发 SVG、全套未出现牌库或全部历史。当前行动者的紧凑 `decision_context` 还带当前地图、分数及随从数，便于离席等非拼接事件后同步。牌堆顺序不进入公开、私有或事件投影。

NPC 使用本地确定性策略，从合法位置中最多评估 32 个紧凑候选，依据公开地图的完成收益和随从库存选择；不调用模型、不读取隐藏牌序，不承诺最优。超时接管、认领、普通/邀请房身份沿用框架。

平台约定（不是出版商规则）：开局及邀请排座沿用双弈；离席/认输者退出争胜并取回其随从，其余玩家继续；只剩一人直接获胜，仅 NPC 留场由既有生命周期结束。唯一赢家从每个败者收一份底注，并列第一全桌零结算。NPC 不建立玩家钱包。普通房沿用再来一局；邀请房沿用既有行为，需另开邀请，`rematch` 返回 409，本次没有改造公共邀请生命周期。

## 网页与 Token

地图由原创 SVG/CSS 绘制；已铺块、虚线合法落点和带“预览”标签的待提交块区分。点击落点，选区域或“不放随从”，一次确认；预览不写服务器。旋转、找落点、随从选择、确认、平移/缩放/回到最新工具都在同一操作区。地图最低缩放后每块仍为 48px，可拖动、方向键平移，也可用“找落点”逐个定位，避免后期自动缩成小点。田地随从横放并有座位数字。得分和随从只进入既有玩家组件。

代码复用现有字体、13px 正文/11px 辅助字、2px 边框和按钮体系；浏览器实测手机操作按钮至少 44px、最低缩放板块为 48px，360/430/1280px 无页面横向溢出。已查看两人/五人预览和 61 块后期地图截图，五人复用现有横向座位栏，没有重复得分卡。图案是原创 SVG，不是出版商插图。

Token 提示保留既有位置：约 500–3500 token/轮。实际 16 组 MCP fixture（2/3/4/5 人，约回合 0/15/35/60；部分采样为 16/36 回合）中，使用 `cl100k_base` 对一次状态与行动回复的紧凑 JSON 计数，范围 567–2979；原始采样已归档到根目录 `artifacts/duel-games-integration-20261002/samples/carcassonne-token-counts.json` 和 `carcassonne-token-fixtures.json`。排除首次 bootstrap、guide、聊天和模型思考；地图形状与落点数量会改变长度，范围不是上限，也不是账单。

## 验证与复跑

所有数据来自临时 SQLite，不访问生产账号、存档或凭据；临时进程/目录由各脚本 finally 清理。使用现成依赖，不安装。下列命令从仓库根执行，并与其他游戏通过同一 flock 串行：

```bash
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=vendor/duel /opt/cedartoy/vendor/duel/.venv/bin/python -m unittest -v tests.test_carcassonne tests.test_carcassonne_integration
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python python3 scripts/persistence_check.py --duel-carcassonne
flock /tmp/cedartoy-duel-new-games-heavy.lock env PYTHONDONTWRITEBYTECODE=1 NODE_PATH=/opt/cedartoy/node_modules DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python node scripts/check_duel_carcassonne_ui.js
```

真实浏览器脚本 `scripts/check_duel_carcassonne_browser.js` 使用本地资源拦截和真实 Python 框架传输，无需启动服务。已完成 360/430/1280px × 2/5 人：旋转、负坐标落点、随从/不放、城市得分回收、刷新、61 块地图、平移/缩放下限、键盘和冲突重试；430px 另用实际 touch 事件操作和平移。五人终局也已检查。按宽度用 `DUEL_BROWSER_WIDTHS` 拆分，人数用 `DUEL_BROWSER_PLAYERS`，截图用 `DUEL_BROWSER_SHOTS` 控制；准确命令见根目录整合验收记录。

本轮后端包含原 38+1 项专项及新增 2 项图例条件断言，覆盖数量/拓扑约束、2/3/4/5 人、邻边/旋转/负坐标、城市/道路/农田合并分隔、多数与并列、盾徽/修道院、农田城市去重、库存、弃牌与终局、并发原子提交、越权/revision、跨进程恢复、完整 NPC 局、普通/邀请 MCP/Web 路由闭环和筹码仅结算一次。首次共享测试发现新增游戏未登记终局隐私分类，现已补齐并复测通过；没有改为公开牌序。

仍未验证：v3 基础 PDF 全部正文和原图、实体手机/其他浏览器、真实生产登录和部署链路、收费 NPC 发言模型。本轮全部数据为临时库，未删农田、未缩短牌组、未修改基础计分。没有 commit/push、重启或上线。
