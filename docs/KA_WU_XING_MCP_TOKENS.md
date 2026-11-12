# 卡五星 MCP 字节与 Token 验收

## 2026-11-12 单手版复测（当前口径）

连续多局已取消；下方旧报告完整保留为历史证据，旧多局样本不参与当前分位数。MCP v2 接入、手牌 `+/-`、`revealed_added`、`kong_added`、持久游标及按需完整恢复全部保留。新普通事件不含 `round_state`；新终局不再复制逐局历史与累计分，仍包含权威本局结算及亮牌。

复现：在 CedarToy 根运行 `python3 scripts/sample_ka_wu_xing_mcp.py`。仍用真实根 MCP → 隔离 ASGI、临时账号/会话/Duel SQLite，种子 7/11/37，默认和七项多选各三场单手，stake=0。全选参数为四个布尔开关 true、horse_count=1、piao=3、joker=red；不传 rounds。共 85 个自然动作，其中 79 对普通 state+move、6 对终局。定向夹具另计。恢复采样在第一步后仍进行中的房间执行（旧固定8步在单手下可能已结束）。

计数沿用下方方法：tiktoken 0.12.0 / cl100k_base，原样压缩 JSON，state 与 move 分别编码后相加，nearest-rank 分位数；字节不当作 token。ASGI 为 Duel 正文，content 含根 slot，root 为真实完整 JSON-RPC。仅响应；不计请求、Guide、聊天、模型思考或网络头。

### 普通轮次

| 房规 | 层 | 部分 | n | token min/p50/p95/max | UTF-8 字节 min/p50/p95/max |
|---|---|---|---:|---:|---:|
| default | asgi | state | 54 | 5/273/794/902 | 7/876/2479/2569 |
| default | asgi | move | 54 | 95/103/450/504 | 291/313/1260/1468 |
| default | asgi | state+move | 54 | 100/412/978/1036 | 298/1341/2867/2882 |
| default | content | state | 54 | 9/277/798/906 | 16/885/2488/2578 |
| default | content | move | 54 | 99/107/454/508 | 300/322/1269/1477 |
| default | content | state+move | 54 | 108/420/986/1044 | 316/1359/2885/2900 |
| default | root | state | 54 | 41/416/1169/1319 | 112/1252/3448/3569 |
| default | root | move | 54 | 166/175/675/754 | 492/518/1781/2068 |
| default | root | state+move | 54 | 207/645/1458/1546 | 604/1942/4054/4140 |
| options | asgi | state | 25 | 5/203/785/878 | 7/660/2520/2550 |
| options | asgi | move | 25 | 89/115/476/504 | 265/375/1366/1468 |
| options | asgi | state+move | 25 | 94/514/900/974 | 272/1578/2835/2902 |
| options | content | state | 25 | 9/207/789/882 | 16/669/2529/2559 |
| options | content | move | 25 | 93/119/480/508 | 274/384/1375/1477 |
| options | content | state+move | 25 | 102/522/908/982 | 290/1596/2853/2920 |
| options | root | state | 25 | 41/319/1158/1288 | 112/962/3405/3556 |
| options | root | move | 25 | 158/193/711/754 | 460/595/1956/2068 |
| options | root | state+move | 25 | 199/798/1351/1454 | 572/2291/4007/4040 |

标签维持 **约100–1000 token/轮**：默认 p50/p95=412/978，多选=514/900；观察到普通最大1036，范围不是上限。多选样本从旧四局123对变为单手25对，不能把中位数变化解释为协议退化。title 删除跨局说明，继续排除 bootstrap/full_state/终局与根包装。

### 快照、终局与恢复（单列）

bootstrap、full_state、terminal_repeat、reconnect 为单次响应；terminal 为 state+move。

| 场景 | n | ASGI token min/p50/p95/max | 根包装 token min/p50/p95/max | ASGI 字节 min/p50/p95/max |
|---|---:|---:|---:|---:|
| default/bootstrap | 9 | 2817/2835/3043/3043 | 3297/3321/3622/3622 | 8830/8879/9556/9556 |
| options/bootstrap | 9 | 3134/3150/3357/3357 | 3613/3635/3935/3935 | 9504/9553/10219/10219 |
| recovery/full_state_midgame | 1 | 3011/3011/3011/3011 | 3530/3530/3530/3530 | 8878/8878/8878/8878 |
| default/full_state_terminal | 9 | 6481/6870/7919/7919 | 8625/9298/10801/10801 | 17721/18853/21316/21316 |
| options/full_state_terminal | 9 | 6804/7151/7389/7389 | 8949/9529/9885/9885 | 18457/19491/19945/19945 |
| default/terminal | 3 | 2514/2668/2849/2849 | 3656/3876/4128/4128 | 6684/7054/7404/7404 |
| options/terminal | 3 | 2505/2875/2921/2921 | 3633/4164/4229/4229 | 6724/7500/7645/7645 |
| default/terminal_repeat | 3 | 1161/1238/1349/1349 | 1696/1806/1961/1961 | 3021/3206/3449/3449 |
| options/terminal_repeat | 3 | 1109/1362/1363/1363 | 1616/1979/1983/1983 | 2897/3497/3519/3519 |
| recovery/reconnect | 1 | 5/5/5/5 | 41/41/41/41 | 7/7/7/7 |

### 定向夹具（不计入普通分布）

| 场景 | n | ASGI token min/p50/p95/max | 根包装 token min/p50/p95/max |
|---|---:|---:|---:|
| forced/added_gang | 1 | 583/583/583/583 | 881/881/881/881 |
| forced/concealed_gang | 1 | 762/762/762/762 | 1122/1122/1122/1122 |
| forced/double_hu | 3 | 126/163/3252/3252 | 242/295/4714/4714 |
| forced/joker | 1 | 2789/2789/2789/2789 | 4058/4058/4058/4058 |
| forced/liang | 1 | 469/469/469/469 | 737/737/737/737 |
| forced/ming_gang | 2 | 128/128/714/714 | 244/244/1073/1073 |
| forced/peng | 2 | 128/128/533/533 | 244/244/821/821 |
| forced/rob_kong | 2 | 78/78/2775/2775 | 174/174/4028/4028 |
| forced/self_draw | 1 | 2795/2795/2795/2795 | 4064/4064/4064/4064 |

终局重复查询依然返回必要结算，并非空增量。完整恢复仍比普通轮次大；历史已结束多局保留只读逐局回顾，但不提供续局动作。前端七项房规和标签通过 DOM 回归；本轮未做真实浏览器渲染验证。

`--legacy-baseline` 现在用于当前单手引擎关闭 v2 路由的对比，不能复现已删除的四局推进。下方旧轨迹与数据保留为当时记录；当前脚本的结果以本节为准。

---

<details>
<summary>历史：取消多局前的 MCP v2 采样与修补记录（原文保留）</summary>

# 卡五星 MCP 字节与 Token 验收

日期：2026-11-12。只使用临时账号/会话/Duel SQLite、隔离三位 AI、真实根 `_handle_root_mcp` → `play(game=duel)` → ASGITransport `/mcp/play`；未访问生产账号、房间、Token 或日志。

## 复现与计数口径

在 CedarToy 根目录运行（无需安装运行依赖）：

```bash
python3 scripts/sample_ka_wu_xing_mcp.py
python3 scripts/sample_ka_wu_xing_mcp.py --legacy-baseline
PYTHONPATH=vendor/duel/.venv/lib/python3.10/site-packages:vendor/duel python3 -m unittest tests_toy.test_duel_ka_wu_xing_incremental tests_toy.test_duel_ka_wu_xing
```

脚本在 Duel 现有 venv 执行 ASGI/根服务，追加既有系统 site-packages 取得 requests；系统 Python 的 tiktoken 0.12.0 只负责分析。所有数据库使用 TemporaryDirectory 并自动清理；脚本默认只向 stdout 输出统计，不保留 JSON 大附件。

- `cl100k_base.encode(json.dumps(response, ensure_ascii=False, separators=(",", ":")))` 的长度是真实 token 数；UTF-8 编码长度是字节数，二者分别统计。分位数用 nearest rank。
- `asgi` 为真实 Duel 响应 JSON；`content` 为根工具 content[0].text 解析后的正文（含平台 slot）；`root` 为完整真实 JSON-RPC 返回，内部 text 字符串的原有换行/空格/转义全部保留。root 不是凭空套一个近似包装。
- 一轮 = 当前行动者的一次 state + 随后的 move 回复，两个响应分别编码后相加。只计响应，不计请求、guide、聊天、模型思考、HTTP/SSE 头。临时环境关闭无关公告/旧存档迁移与模型供应商；未测生产公告注入及网络网关开销。
- 固定牌墙与邀请座次种子 7/11/37；默认单局 3 场，多选房规 4 局×3 场，均 stake=0。多选为 double_hu/gang_discard_double/liang_liability/consecutive_kong_double=true、horse_count=1、piao=3、joker=red、rounds=4。天然轨迹共 219 次动作，177 对普通 state+move。
- 按响应实际字段分类：只要一对响应中有 round_state、terminal_hands 或终局状态，即移出普通样本；跨局接收者也单列。罕见场景用另建的 84 张唯一实体牌夹具，执行动作仍走真实 MCP；单独计量，绝不混成自然频率。
- 房间 ID/时间戳不规范化，因此快照/终局的少量 token 可能随复现变化。`--legacy-baseline` 仅在临时进程关闭卡五星 v2 路由和新增 event hook，恢复修补前真实旧投影；两次输出的六组 trajectory_sha256 已逐组相等，确认牌局、动作、规则结果不变。

## 普通轮次 Token

| 房规 | 层 | 每轮部分 | n | min/p50/p95/max |
|---|---|---|---:|---:|
| default | asgi | state | 54 | 5/273/794/902 |
| default | asgi | move | 54 | 95/103/450/504 |
| default | asgi | state+move | 54 | 100/412/978/1036 |
| default | content | state | 54 | 9/277/798/906 |
| default | content | move | 54 | 99/107/454/508 |
| default | content | state+move | 54 | 108/420/986/1044 |
| default | root | state | 54 | 41/416/1169/1319 |
| default | root | move | 54 | 166/175/675/754 |
| default | root | state+move | 54 | 207/645/1458/1546 |
| options | asgi | state | 123 | 5/151/692/994 |
| options | asgi | move | 123 | 89/97/472/534 |
| options | asgi | state+move | 123 | 94/436/910/1139 |
| options | content | state | 123 | 9/155/696/998 |
| options | content | move | 123 | 93/101/476/538 |
| options | content | state+move | 123 | 102/444/918/1147 |
| options | root | state | 123 | 41/242/1027/1458 |
| options | root | move | 123 | 158/167/706/795 |
| options | root | state+move | 123 | 199/683/1362/1698 |

## 普通轮次 UTF-8 字节

| 房规 | 层 | 部分 | n | min/p50/p95/max |
|---|---|---|---:|---:|
| default | asgi | state | 54 | 7/876/2479/2569 |
| default | asgi | move | 54 | 291/313/1260/1468 |
| default | asgi | state+move | 54 | 298/1341/2867/2882 |
| default | root | state | 54 | 112/1252/3448/3569 |
| default | root | move | 54 | 492/518/1781/2068 |
| default | root | state+move | 54 | 604/1942/4054/4140 |
| options | asgi | state | 123 | 7/509/1985/2767 |
| options | asgi | move | 123 | 265/287/1326/1518 |
| options | asgi | state+move | 123 | 272/1314/2649/3125 |
| options | root | state | 123 | 112/754/2790/3904 |
| options | root | move | 123 | 460/486/1904/2160 |
| options | root | state+move | 123 | 572/1918/3808/4514 |

## 快照与终局（与普通样本分开）

快照/恢复/terminal_repeat 为单次响应；跨局/局末/终局为 state+move 一对。terminal_repeat 是最后行动者已收到终局后再次查询，仍返回现行终局结算/回顾；并非空增量。

| 场景 | n | ASGI token min/p50/p95/max | 根包装 token min/p50/p95/max | ASGI 字节 min/p50/p95/max |
|---|---:|---:|---:|---:|
| default/bootstrap | 9 | 2888/2905/3114/3114 | 3383/3406/3708/3708 | 9105/9154/9831/9831 |
| options/bootstrap | 9 | 3267/3282/3490/3490 | 3761/3782/4083/4083 | 9920/9969/10635/10635 |
| recovery/full_state_midgame | 1 | 6186/6186/6186/6186 | 8026/8026/8026/8026 | 16911/16911/16911/16911 |
| default/full_state_terminal | 9 | 10905/11041/12186/12186 | 14936/15203/16878/16878 | 29688/29725/32704/32704 |
| options/full_state_terminal | 9 | 23104/23303/23581/23581 | 32218/32610/32905/32905 | 60990/61591/62000/62000 |
| options/cross_round | 27 | 1081/2763/3332/3371 | 1590/3985/4814/4871 | 3106/7544/9208/9243 |
| options/round_end | 9 | 1431/1553/1939/1939 | 2111/2279/2834/2834 | 3882/4127/5083/5083 |
| default/terminal | 3 | 4151/4382/4630/4630 | 5991/6321/6662/6662 | 11169/11722/12198/12198 |
| options/terminal | 3 | 8435/8543/8585/8585 | 12084/12272/12313/12313 | 22100/22546/22596/22596 |
| default/terminal_repeat | 3 | 2360/2514/2736/2736 | 3405/3625/3935/3935 | 6176/6546/7033/7033 |
| options/terminal_repeat | 3 | 6345/6498/6516/6516 | 9093/9304/9349/9349 | 16475/16788/16931/16931 |
| recovery/reconnect | 1 | 5/5/5/5 | 41/41/41/41 | 8/8/8/8 |

## 定向夹具（不代表出现概率）

每例从隔离完整合法实体牌配置启动；碰/直杠/双响/抢杠包含其前置出牌或补杠。表中为 state+move；胡牌例含终局，不能套大厅普通标签。

| 场景 | n | ASGI token min/p50/p95/max | 根包装 token min/p50/p95/max |
|---|---:|---:|---:|
| added_gang | 1 | 583/583/583/583 | 881/881/881/881 |
| concealed_gang | 1 | 762/762/762/762 | 1122/1122/1122/1122 |
| double_hu | 3 | 126/163/5390/5390 | 242/295/7763/7763 |
| joker | 1 | 4623/4623/4623/4623 | 6672/6672/6672/6672 |
| liang | 1 | 469/469/469/469 | 737/737/737/737 |
| ming_gang | 2 | 128/128/714/714 | 244/244/1073/1073 |
| peng | 2 | 128/128/533/533 | 244/244/821/821 |
| rob_kong | 2 | 78/78/4571/4571 | 174/174/6588/6588 |
| self_draw | 1 | 4634/4634/4634/4634 | 6683/6683/6683/6683 |

## 查出的重复与最小修补

原工作树未将 ka_wu_xing 接入 mcp_minimal，背景中的“已是 v2”不成立。相同 revision 连续两次 state 会原样返回 14 张本人手牌以及 own_melds/shanten/legal_actions 等。公开行动已有差分，但第二人亮倒重发先前亮倒手牌、新杠重发历史 kong_ledger；换庄同时给旧河/副露清空差分和完整 round_state。

修补仅接入既有 v2 事务/游标/私有基线：手牌按实体 ID 增删、其他私有字段变化时替换；revealed_added 合并本次亮倒者、kong_added 追加本次杠；round_state 覆盖牌桌时省去被覆盖的清空差分。Web 原始事件、规则引擎、合法动作校验、钱包及 NPC 策略不变。协议说明只放 bootstrap/full_state 和按需 Guide，不扩大全局工具 schema。

| 房规 | 修补前 ASGI state+move min/p50/p95/max | 修补后 |
|---|---:|---:|
| default | 448/613/1350/2217 | 100/412/978/1036 |
| options | 437/672/1404/2323 | 94/436/910/1139 |

大厅填 `约100–1000 token/轮`，沿用其他棋种的压缩正文 state+move 口径、近似 min–p95。观察最大值 1139，明确不是上限；根包装分开列出。title 排除 bootstrap、手动完整同步、跨局和终局。

## 验证与边界

- 字段级断言：普通响应递归不含 full_state/rules_text/wall/hands/action_history/round_results/round_state/完整牌桌/历史 kong_ledger 或全量 revealed；private 不含完整 hand。允许本次亮倒公开手牌、合法动作、变更的本人副露、听牌距离等必要字段。
- 逐响应重放私有 +/-、legal_actions 和公开牌河、副露、亮倒、杠分/账、庄家、局数、累计分等，与持久数据库的安全权威投影比较；不是单看 JSON 变短。摸牌通过自动推进及杠补触发。
- 定向覆盖碰、明/暗/补杠、抢杠、亮倒、双响、自摸翻马、漂和红中万能；四局跨局、0底注、旧上下文一次升级、重复读取空增量、full_state 后不重放动作且未读文字恰好一次。每次 HTTP 请求用新 ASGI client，另有新解释器加载同一临时库的真实 ASGI 重连测试。
- 终局仍带权威结算和亮牌，完整恢复仍带历史回顾，数字明显更大；0 底注终局重复查询也仍给完整 game_result（默认最多2736、多局最多6516 token），本轮保留现有终局响应契约，没有删除结算关键字段来压低普通估算。这是“不是每次都只发增量”的明确边界。断线并不等于丢失上下文：同账号沿用游标；丢包或丢上下文必须 full_state，协议不是可重放 move 的确认重试协议。
- 关联回归：根 MCP 12 tests 通过；Duel 规则/房规/癞子/共享 v2 63 tests 通过；JS syntax 与卡五星 DOM 检查通过。
- UI 完全复用现有标签：12px、system-ui、粉色 #c95f72，字段间距5px、输入框44px高/3px边框均未改；同位置与麻将标签切换/提示口径有断言。已添加 360/390/430/1280 浏览器检查，但本环境无浏览器且临时 Playwright 获取因 EAI_AGAIN 失败，真实截图验收仍待有浏览器环境执行，DOM 通过不代替视觉通过。
- 本轮未 commit/push/部署/回复 Issue。验收后先补真实浏览器检查，再按 vendor 本地补丁边界归档，主仓提交对应脚本/测试；部署独立 cedartoy-duel 服务并刷新 app.js?v=0.9.73，勿仅重启主站。

</details>
