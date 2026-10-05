# 宝石商人 `gem_merchant`：两人宝石对决

制作：顾屿、相顾｜小红书：苏苏脆脆

南山君好！这是一份新游戏插件的投稿说明。我们原本在自己搭的小服务上和小机玩一个两人宝石对局，
很想能搬到双弈里玩，所以照双弈现有的插件契约重新写了一份。下面是它是什么、规则从哪来、改了哪些文件、
测了什么，以及几处拿不准、需要您来定的地方。若有任何不合适，按您的判断改或不收都完全没问题。

## 版权与出处（请先看这一段）

**规则机制与 67 张发展卡的数值来自桌游《Splendor Duel》**（中文名《璀璨宝石：对决》，Space Cowboys / Asmodee 2022 出版）。
本插件的游戏名「宝石商人」、四张称号卡的名称（糖霜女王、斗篷小偷、风车旅人、卷轴管家）以及全部卡面、宝石、图标
都是原创的 CSS 绘制，没有使用原版的名字、文字或美术素材，也没有任何图片文件。

但规则机制和卡牌数值确实与原版一致。是否适合在 toy.cedarstar.org 这个公益非商业站点上线，请您自行判断；
如果您觉得不妥，这个分支可以只留在本地自用。

参考资料：

- 官方英文规则书（12 页）：https://cdn.1j1ju.com/medias/d5/20/a3-splendor-duel-rulebook.pdf
  （1jour-1jeu 文件区 https://en.1jour-1jeu.com/cardgame/2022-splendor-duel/files ）
- 67 张发展卡数值：BoardGameGeek 文件区 plaidmac 整理的 *Splendor_Duel_Card_List-v3*（2024-05-08）
  https://boardgamegeek.com/filepage/276448/card-list-for-splendor-duel
- 交叉核对：GitHub `CGYGameDevelopment/Splendor-Duel` 的 `jewel-cards.csv` / `royal-cards.csv`（只看数据）。
  两份 67 张逐张比对，66 张一致，1 张不同：一级 1 冠百搭卡，BGG 为「白4 珍珠1」，CSV 为「白4 黑1 珍珠1」；
  对照 splendortactics.com 的卡面图确认为「白4 珍珠1」，采用 BGG（测试里锁定为 #28）。
- 4 张皇室卡（本作称号卡）：3 分无能力 / 2 分+从对手拿 1 枚 / 2 分+再来一回合 / 2 分+拿特权券。
- 宝石盘螺旋：规则书只写“从中心格沿盘上印的螺旋”，走向取自 CGY 仓库的盘面图；没有用实体盘核对过。
  宝石本身随机入袋随机铺，走向只影响“空格先补哪里”，不影响公平。

## 规则摘要（实现即按此）

- 5×5 宝石盘；白/蓝/绿/红/黑各 4 枚、珍珠 2、金 3；3 张特权券；一级 30 / 二级 24 / 三级 13 张发展卡；4 张称号卡。
- 开局：三级各翻 3/4/5 张成金字塔；宝石随机从中心沿螺旋铺满；后手先拿 1 张特权券。
- 每回合：可选行动按顺序 —— 先用特权券（每张换盘上 1 枚非金宝石，可多张），再补盘（袋中宝石沿螺旋填空，对手得 1 张特权券，
  补完本回合不能再用券）；然后必选其一：
  - 拿 1–3 枚在同一横/竖/斜线上紧挨着的非金宝石（中间不能隔空格或金）；一次 3 枚同色或拿到 2 枚珍珠，对手得 1 张特权券；
  - 保留区不足 3 张且盘上有金：拿 1 枚金，保留场上 1 张或从任一牌堆顶盲抽 1 张；
  - 购买场上或自己保留区的 1 张：成本先扣同色加成（珍珠无加成），金可顶任何颜色，花掉的宝石回袋。
  - 三样都做不了时必须先补盘；补盘后仍做不了（或袋空）才能 pass。
- 能力：再来一回合 / 从盘上拿 1 枚同色 / 从对手拿 1 枚非金宝石或珍珠 / 拿 1 张特权券 / 百搭（必须压在自己已有加成的颜色上）。
- 皇冠累计到第 3、第 6 顶各选 1 张称号卡。
- 回合结束：宝石超过 10 枚弃到 10；然后检查胜利 —— 声望 ≥ 20、皇冠 ≥ 10、或同一颜色的卡累计 ≥ 10 分（百搭算所压颜色）。

## 实现取舍

- **一回合拆成多个权威动作**：`use_privilege` / `refill` 保留行动权；必选动作后若有需要人选的结算
  （`take_bonus_gem` / `steal` / `choose_royal` / `discard`，弃宝石一次一枚），仍由本人继续提交。
  只有一个选项时服务端自动结算，减少无意义的往返。所有动作都来自 `private_state.legal_actions`，
  前端只比对“当前选择是否等于某个已发布动作”，不在浏览器复制规则。
- **付款自动**：先用同色宝石/珍珠，不够再用金（金比任何单色都灵活，所以不会更差）。
- **保留时选哪枚金**：动作里的 `gold` 指定；MCP 可省略，取阅读顺序第一枚金。
- **防死锁**：双方连续都只能 pass（实际几乎不可能），按声望判胜，相同为平局。
- **再来一回合不叠加**：同一回合同时触发卡牌与称号卡的“再来”只多走一回合（原版对此没有明确说明，我们按一次处理）。
- **NPC**：不支持系统 NPC（`supports_npcs=False`）。为了满足“所有游戏都支持超时临时代操作”的现有测试，
  插件实现了 `npc_legal_actions`（跳过可选步骤），并在 `app/takeover.py` 加了一个通用回退：
  不支持 NPC 的插件若主动发布了合法动作，就用它来做临时代操作。基类返回空列表，其他游戏行为不变。
- **筹码**：`supports_stakes=True`，沿用双人固定 ±stake，平局 0。
- **隐私**：牌堆顺序永不离开服务端；盲抽保留的卡在对局中对手只看到等级（公共投影为
  `{"hidden":true,"blind":true,"level":n}`，MCP 为 `"hidden L2"`），本人在 `private_state.reserved` 看到全貌，
  真实终局（`terminal_public_state`）公开复盘；**从场上保留的卡双方都可见**。
  动作记录、`format_action` 文案、裁判 delta 都不含盲抽卡号。隐藏信息审计表把它归入 terminal review 一类。
- **MCP（原协议）**：
  - bootstrap / full_state 的 `board_state` 用紧凑编码：`board` 为 5 个字符串（W/U/G/R/K/P/O，`.` 为空），
    卡牌形如 `"#59 L3 green+1 3pt 2crown =W5 U3 R3 P1"`，另附 `royal_table` 与 `delta_format` 说明。Web 投影保持完整对象不变。
  - 每个动作后发 `gem_merchant_delta`（`mcp_immediate_public_events=True`），只含变化字段：
    `players` 按玩家与字段合并，`purchased_add` 追加新购卡号；`board_set=[[row,col,字符]]` 更新至多两个变化格
    （`.` 清空），变化更多时发送完整 `board`；`pyramid_set=[[level,index,card|null]]` 替换金字塔格，其余整体替换。
  - 可选阶段 MCP `private_state` 给 `legal_summary` 摘要（取法数、可保留的金格/卡号/等级、可买卡号与百搭颜色），
    避免每轮发送上百条拿宝石组合；普通轮次 `take` 为取法数、`use_privilege=true` 表示可用券，规则和提交格式
    由 bootstrap/full_state 的 `move_format` 完整提供。摘要每次整体替换，未列出的动作不可用。
    结算阶段继续给完整 `legal_actions`，不截断选项。
  - 普通轮次只在 `private_state.blind_reserved` 重发自己的全部盲抽保留卡，空数组清空；
    场上保留的卡面由公开 `players` 快照和增量继承。bootstrap/full_state 的 `reserved` 仍包含全部保留卡。
  - 我把 `gem_merchant` 加进了 `app/full_state.py` 的 `LEGACY_GAMES`（及 `tests/full_state_support.py` 的同名列表），
    让 `full_state` 也带 `rules_text/move_format` 并沿用原协议的“快照覆盖的旧事件不重放”语义。
    这个集合原本叫“老 25 款”，如果您希望新游戏走别的路径（例如 MCP v2），请按您的设计调整。
  - 2026-10-05 配对复测：普通轮次 p10/p50/p90/max 从 348/517/673/854 降至 290/413/549/693，
    大厅估算更新为「约300–600 token/轮」。完整口径、bootstrap 和复现方式见下节。

## 普通轮次 token 配对测量（2026-10-05）

基线为 `bea5d745685b5124037ff5f872102fc6f7a16e36` 的游戏模块。临时 SQLite、真实 ASGI `/mcp/play`，
固定 `PYTHONHASHSEED=0`，种子 3/11/37/71 各跑均匀随机与优先购买两种策略，共 8 局自然结束，
1,034 个服务端动作。购买策略在有可买卡时以 80% 概率随机购买，否则从全部合法动作随机选择；
双方使用同一策略。人类席经框架正常落子，小机席每次行动前读 state、再提交 move，保留全部对手事件批次。
无局面注入，覆盖前中后期、特权/补盘、购买/保留和四种结算选择。

计数为 `cl100k_base`、`json.dumps(ensure_ascii=False,separators=(',',':'))`，
一次普通轮次 = 一次 state 增量回复 + 一次 move 回复的 token 之和；不是一个可含多次动作的桌游回合。
排除 bootstrap/full_state、guide、聊天、请求、模型思考，以及 2 个产生终局回复的轮次。
正常响应中的通用字段和实际出现的未读提示全部计入，没有归一化或删字段后再计数。
固定房间号 `GEM00000`–`GEM00007`，玩家为 `ai-1` / `human-1`，真实名字和 ID 长度会影响实际费用。
分位数用 nearest rank；阶段按每局全部动作序号分三等份，不是生产流量的频率估计。

| 样本 | n | before p10 / p50 / p90 / max | after p10 / p50 / p90 / max |
|---|---:|---:|---:|
| 全部普通轮次 | 515 | 348 / 517 / 673 / 854 | 290 / 413 / 549 / 693 |
| 均匀随机 | 299 | 345 / 508 / 667 / 854 | 282 / 396 / 545 / 693 |
| 优先购买 | 216 | 353 / 522 / 674 / 838 | 302 / 432 / 556 / 649 |
| 前期 | 166 | 345 / 500 / 629 / 753 | 301 / 419 / 523 / 654 |
| 中期 | 180 | 345 / 511 / 680 / 793 | 279 / 403 / 549 / 676 |
| 后期 | 169 | 352 / 539 / 713 / 854 | 286 / 415 / 568 / 693 |
| bootstrap（单独计，不纳入普通轮次） | 8 | 3428 / 3438 / 3454 / 3454 | 3678 / 3688 / 3704 / 3704 |

p50 下降 **20.1%**，p90 下降 **18.4%**，均值从 515.9 降至 415.1（**19.5%**）。
早期投稿的 4 局结果是 p10≈340 / p50≈500 / p90≈950，缺少同轨迹原始样本，不能与本次结果直接计算优化比例。
大厅的「约300–600」是常见量级估算，不是上限或账单承诺；本批峰值仍为 693。

主要开销如下（每轮平均 token；字段项为移除该字段前后的边际差值，包含其键名；子项互有包含及分词边界差异，不应再次求和）：

| 来源 | before | after |
|---|---:|---:|
| 通用外壳（移除 events/private_state 后） | 185.5 | 185.5 |
| 其中 unread / unread_hint | 101.5 | 101.5 |
| private_state 合计 | 143.6 | 58.2 |
| 其中全部 reserved → 仅 blind_reserved | 59.8 | 8.6 |
| 其中 legal_summary | 66.1 | 32.1 |
| 其中结算 legal_actions | 12.5 | 12.5 |
| 其中重复取宝石/特权说明 | 34.2 | 0 |
| 双方事件合计 | 186.7 | 171.3 |
| 其中 state 携带的对手事件（独立子数组计数，扣除空数组） | 103.7 | 95.2 |
| 其中玩家字段 delta | 68.0 | 59.2 |
| 其中宝石盘 board / board_set | 22.8 | 16.4 |
| 其中新翻卡 pyramid_set | 16.1 | 16.1 |

卡牌文本本来已包含清晰的编号、等级、颜色、分/冠、能力、成本，因此保留其可读编码；主要收益来自减少重复卡面，
而非再缩写卡牌语义。通用外壳的未读成就提醒会重复发送，本次仍原样计入并保留；进一步大幅削减需单独评估共享协议。
本次仅改游戏插件的 MCP 投影和公开 delta，不迁移 v2，不改全局 schema、规则、合法性校验、Web 投影或事件游标。
新增说明只在 bootstrap/full_state 交付；bootstrap 均值从 3,442.5 增至 3,692.5，增加 250 token，
换取普通轮次平均减少约 101 token。已有只读过旧版 bootstrap 的客户端在切换版本后应先调用一次 full_state，
恢复含新字段定义的上下文；新局默认 bootstrap 已包含全部定义。

复现（`DUEL_PYTHON` 为已有项目依赖的解释器；分析解释器另需 tiktoken，不加入运行依赖）：

```bash
mkdir -p /tmp/gem-token-baseline
git show bea5d745685b5124037ff5f872102fc6f7a16e36:app/games/gem_merchant.py > /tmp/gem-token-baseline/gem_merchant.py
PYTHONHASHSEED=0 PYTHONPATH=. "$DUEL_PYTHON" scripts/sample_gem_merchant_tokens.py --before-dir /tmp/gem-token-baseline --out /tmp/gem-before.json
PYTHONHASHSEED=0 PYTHONPATH=. "$DUEL_PYTHON" scripts/sample_gem_merchant_tokens.py --out /tmp/gem-after.json
PYTHONPATH=. python scripts/compare_gem_merchant_tokens.py /tmp/gem-before.json /tmp/gem-after.json --out /tmp/gem-counts.json --check-budgets
```

[归档报告](../artifacts/gem-merchant-token-opt/tokens.json) 包含逐阶段/策略/结算类型统计及 SHA256；
同目录 `before.json.gz` / `after.json.gz` 保存完整原始采样，比较脚本也可直接读取压缩文件。
比较器检查两边完整动作轨迹和终局状态相等，逐响应重放公开 delta、核对所有未改字段和盲抽卡面，
只有终局成就时间在等价检查时忽略（计数未改时间）。`--check-budgets` 对固定样本锁定 n=515、p50≤430、p90≤580。
`tests/test_gem_merchant_compact.py` 用仅依赖已交付响应的参考客户端，在四局 ASGI 对局中枚举全部可提交动作，
逐步核对引擎合法动作集合、保留卡恢复及公开状态，并丢弃上下文后用 full_state 恢复；另覆盖 pass、空盲抽列表和紧凑字段契约。

本次验证命令（仅相关模块）：

```bash
PYTHONHASHSEED=0 PYTHONPATH=. "$DUEL_PYTHON" -m unittest tests.test_gem_merchant tests.test_gem_merchant_compact tests.test_gem_merchant_frontend tests.test_hidden_information_audit
git diff --check
```

46 项中 45 项通过，浏览器教程几何测试因未安装 Playwright 跳过；Node DOM 与大厅文案测试通过。
包含原有 60 局随机规则/守恒测试、框架整局与 MCP 隐私验证。采样解释器使用项目 Python 3.10，分析解释器使用 tiktoken 0.12.0。

## 文件清单

新增：

- `app/games/gem_merchant.py` —— 规则引擎、卡表、合法动作、公共/私有/终局投影、MCP 紧凑编码与 delta。
- `app/static/games/gem_merchant.js` —— renderer（`usesStandardMoveConfirmation:false`、`ownsPrivateStatePresentation:true`），
  只用 DOM/CSS，没有图片和 `innerHTML`。
- `app/static/games/gem_merchant.css` —— 沿用 `--purple/--pink/--text` 等配色变量；手机竖屏优先，触控目标 ≥ 44px，
  有 `max-width: 599px` 与 `359px` 两档。
- `tests/test_gem_merchant.py` —— 规则单测、60 局随机整局守恒、校验器与 legal_actions 一致性、框架整局、认输、MCP 隐私。
- `tests/test_gem_merchant_frontend.py` —— 前端契约 + node DOM 运行时测试（用真实服务端投影做 fixture）。
- `docs/GEM_MERCHANT.md` —— 本文件。

修改：

- `app/games/__init__.py` —— 注册。
- `app/takeover.py` —— 上文所述的通用临时代操作回退（5 行）。
- `app/full_state.py` —— 加入 `LEGACY_GAMES`。
- `app/static/app.js` —— 只加了一行 token 估算文案。
- `README.md`、`docs/MCP_GUIDE.md` —— 游戏表与 MCP 说明各加一节，计数 29 → 30。
- 已有测试里写死的游戏数量/分类/隐私分桶：`test_game_categories`、`test_hidden_information_audit`、`test_identity`、
  `test_local_gateway`、`test_multiplayer_phase1`、`test_new_games`、`tests/full_state_support.py`。

## 投稿者原始测试结果

以下为随投稿保留的原始验证记录，不代表本次接入运行了全仓测试。

环境：macOS arm64，Python 3.12.15，Node 20.18.0（launcher 自建 `.venv`，PyMahjongGB 本地编译通过）。

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s tests
```

- 加入前（干净 main）：1297 个测试，1 个失败、10 个跳过。
- 加入后：1332 个测试（新增 35 个），同样只有那 1 个失败、10 个跳过。
- 这个失败是 `test_npc_framework.PersonaLoaderTests.test_avatar_mapping_is_external_and_rejects_unsafe_paths`，
  原因是 macOS 的临时目录 `/var` 是 `/private/var` 的符号链接，路径比较不相等；与本插件无关，干净 main 上同样失败。
- 宝石商人自己的测试：`.venv/bin/python -m unittest tests.test_gem_merchant tests.test_gem_merchant_frontend`，35 个全过。
  随机整局测试会检查每一步的宝石总数、67 张卡、3 张特权券守恒，回合结束时宝石不超过 10 枚，非行动方没有合法动作。

浏览器验证：按 `docs/LOCAL.md` 用 `DUEL_LOCAL_PORT=8790 ./scripts/start-local.sh --no-browser` 起本地实例，
Playwright Chromium 以 390×844（iPhone 竖屏，2x）走了一遍：大厅选桌游 → 开局 → 选三枚宝石拿取 → 小机经 `/mcp/play`
从场上保留并附言 → 查看卡牌详情 → 盲抽保留 → 小机盲抽 → 看对手保留区（场上保留的可见、盲抽的只见牌背）→
买下带“偷”能力的卡并选择 → 买到 20 分终局 → 终局复盘 → 规则抽屉。页面无 JS 报错。
（为了快速到达能力结算和终局，截图脚本直接改了本地 `data/local-duel.db` 里的局面；正常对局不需要这样做。）

## 拿不准、请您定夺的地方

1. 规则机制与卡牌数值来自已出版的商业桌游，是否适合上线请您判断（见开头）。
2. 加入 `LEGACY_GAMES` 的做法是否符合您的协议规划。
3. `app/takeover.py` 的通用回退是否可以接受；不接受的话，也可以改成在测试里把本游戏排除，或者给本游戏写一个本地策略。
4. 宝石盘螺旋走向没对过实体盘（不影响公平）。
5. 同一回合两次“再来一回合”按一次处理。
6. 没有系统 NPC。若以后想支持，可以在 `npc_legal_actions` 基础上接 provider，或写个简单的本地策略。

谢谢您做了双弈 :)
