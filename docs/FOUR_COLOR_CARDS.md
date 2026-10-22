# 四色牌：三模式单手对局

2026-10-22 第三轮续作。在既有十胡核心上补齐九支、群胡的独立状态机、计分、NPC、Web 与 MCP 增量。
本地 `GAMES` 已注册三个独立类型；大厅合为一个“四色牌”选项，普通／邀请表单均在创建前选规则。
**没有部署、重启、commit、push；线上服务尚未更新。** 没有修改主仓无关脏改、vendor origin 或 gitlink。

## 采用版本与差别

| 项目 | 台湾十胡 | 九支十仔 | 群胡 |
| --- | --- | --- | --- |
| game_type | four_color_tenhu | four_color_jiuzhi | four_color_qunhu |
| 基准 | BGA 中文帮助修订1851 | 台湾五对十张＋2015实玩文的公开翻牌版 | 2021忠孝新村采访，日报群胡段补听牌／计分 |
| 人数／发牌 | 2–4人，庄21闲20 | 4人，庄10闲9，先留五张 | 3–5人，庄16闲15 |
| 胜牌 | 全部完整分组，组合至少10胡 | 同色同字五对，共10张 | 五组三张＋任一单张，至少一目，且事先听牌 |
| 进牌 | 公开翻牌／弃牌吃碰杠后弃 | 初始对扣置，强制吃对后弃 | 未听可取上家弃牌或公开摸换；听后固定 |
| 优先 | 胡＞碰杠＞吃 | 胡＞吃对；弃牌只下家 | 公开牌听者可胡；弃牌只下家可胡／取 |
| 计分 | 超10胡＋花胡＋1；相公反向11 | 颜色、无将兵、自摸、抽五张、开交计头 | 自摸3；非自摸着花2／无花1 |
| 特殊 | 将帅禁弃，明暗胡不同 | 断张优先弃、最少颜色弃 | 有目才听，公开展示，听后手牌锁定 |

三种都使用112张实体牌，不互借组合表和计分。所有房间只打一手、只有游戏内虚拟零和分数；非0 stake 由服务端拒绝。
游戏类型创建后持久化且不可变，状态另存 `rules_version`。动作不接受规则字段；Web／MCP必须携带当前revision。

### 公开资料与明确取舍

- [BGA 中文帮助](https://zh.doc.boardgamearena.com/index.php?title=Gamehelpfourcolorcards&variant=zh-hans)，修订1851，2022-08-09：十胡组合表、优先级、点花、门槛。
- [BGA 英文帮助](https://en.doc.boardgamearena.com/Gamehelpfourcolorcards)：只补副露不可改组、无法弃牌即结束。将帅杠冲突采用中文暗6／明8；不混用国瑜厂商版本。
- [怪e紅傑克，2010-08-14](https://j9981168.pixnet.net/blog/posts/9205344374)：九支“五对、同字同色、共十张”。
- [世界遊戯博物館，2015-08-19](https://sekaiyugi.com/2015/08/19/sisepai/)：作者实玩台湾线上平台后的公开翻牌、初始扣对、强制吃、断张、最少色弃、抽五张及计头。**不采用日文“十组对子”误句，也不混用台湾短文可直接摸换的读法。**
- [浯島城事，陈昱青，2021-02-01](https://kinmen-story.tw/article/1025)：忠孝新村女子桌游团采访，群胡仅采用这个地方版本，不代表全金门统一规则。
- [金门日报，陈丽玉，2013-08-24](https://www.kmdn.gov.tw/1117/1271/1274/228160)：仅“群胡”段补听牌／3、2、1计分。**不把前文十胡移入群胡，不采用“三色以上兵卒”扩展四张组。**

已实际阅读公开资料，未复用第三方玩法源码。以下约定是公开资料未完全说明处的产品裁判，不是台湾统一规则。

## 全部双弈平台约定

同源玩家正文在 `app/games/four_color_cards/rules.py` 的 `TENHU/JIUZHI/QUNHU`。
网页规则抽屉、MCP首包／恢复和插件guide共用正文，显著区分“资料依据与版本”和“双弈平台约定”。
正文涵盖发牌、轮转、全部动作、胡牌门槛、优先级、特殊规则、计分、流局、其他两种差别及来源。

### 共同

1. 单房单手，庄／先手按房间设置；不接真实赌博或钱包下注。
2. 离场或认输取消整手，所有人0分，其他人的暗牌不公开。
3. 规则创建后固定；服务端核验动作资格、优先级和revision。旧revision返回409，不重复改状态／结算。

### 十胡（`bga-zh-1851-tenhu-platform1`）

1. 不采用英文BGA三手赛程、后续换庄及余牌置顶洗牌。
2. 空堆点花引用首弃实体；若首弃已吃进胡家组合，实体留在该组合，不与自身重复匹配，其余同码实体照计，最多3花胡。
   尚无直接验证这个特殊局面的BGA牌例；已明确标为平台约定，不再抛“规则待确认”令牌局死锁。

保留既有独立十胡裁判：末张进组算明，余牌选最大胡数完整精确分组；花胡不补10胡门槛。
完整但不足10胡的叫胡判相公每家赔11，杂牌未组完的错胡拒绝且不改状态。
最后公开牌及后续进牌弃牌完成响应后才能判空堆，流局0分。

### 九支（`taiwan-five-pairs-sekai2015-duel1`）

1. 庄起手已五对可以直接胡，不拆初始对子，不算自摸、不抽五张。
2. 断张只查活动手牌，不拆扣置对子；不能再成对。多张依出现先后，同次依实体顺序排队，每次先弃最早一张，优先于最少颜色规则。
3. 最少颜色只数活动手牌；并列颜色可任选其中任一牌。扣置对／明对均不能拆换。
4. 开交胡（同字类4＋4＋2且末张补四同牌）加5，与开交对子不重复加；否则每组四同牌加1。颜色、无将兵、自摸和抽五张照常相加。
5. 所有积分仅在胡牌结算一次，不实施未明确的非终局收分。自摸或吃别人公开翻牌，各家付同样头数；吃上家弃牌仅弃牌者付一份。
6. 预留五张不补回牌库；自摸才翻。每张与胜牌任一张同码即中，不乘胜牌实体数量；第五张中加2、其余各1。
7. 最后公开牌及后续吃对弃牌完成响应；无人进牌且无牌可翻时流局，所有人0分。

响应采用所选公开翻牌版本：胡先于吃对，同级翻牌者先再顺座次，弃牌只有下家；有资格必须吃，无“过”按钮。

### 群胡（`kinmen-zhongxiao2021-duel1`）

1. 开局不能直接胡，必须先弃牌并听牌；15张已经有目且存在合法进张才可听。
2. 听后整副15张固定，不能换听、撤听或取牌换手；展示是公开引用，不重复移出实体。展示优先一张将帅，否则同色车马炮，否则一张兵卒；听牌不锁具体分组。
3. 同一公开翻牌多人胡，翻牌者先，再顺逆时针；第一位接受者胡，可过。弃牌只下家可胡／取。已听者未胡的翻牌原样打出。
4. 非自摸抽最多两张剩余牌库顶牌，不预留不补。任一花牌与胜牌任一张同色同字即着花；两张中仍只2点，零张可抽按未中1点。自摸固定3点、不抽花；都由其他每家付款。
5. 最后一张翻牌仍完成胡、换、弃响应；无可翻牌且弃牌无人取则0分流局。连续112次弃牌都未再翻牌亦0分流局，防止循环换牌。

独立牌型表：同色将士象、同色车马炮、三异色兵卒是目；其他三张组须同字三异色，将帅允许颜色重复。
不是十胡的同色普通碰／杠／对子，也不套麻将组表。

## 实现与保密

- `tenhu.py` 保留上一轮主流程；`jiuzhi.py` 与 `qunhu.py` 各有独立选项、响应窗口、轮转、胡／流局和计分。
- `common.py` 只共享发牌实体、生命周期、投影与动作包装；`*_rules.py` 各自判定。
- `public_state` 只含公开组、张数、当前公开牌、断张／听牌展示和终局赢家胜牌。九支扣置对内容只给本人；群胡待牌列表只给本人；其他人的暗手和未来牌序不发出。
- 本地NPC只根据本人手牌、已公开牌和合法动作选牌。自动化把他家手牌及牌库替换成不可读取值，确认策略输出不变；不调用付费模型。
- 每个动作深复制后变更；112实体保持唯一归属。听牌展示、终局胜牌和花牌显示只是引用，不重复实体。
- 主站代理本来会覆盖自报身份；新增四色牌HTTP动作对认证头与body身份的局部比对。其它游戏接口行为不变。

## MCP：首包一次，后续增量

三个类型均接既有 `mcp_minimal` v2事务上下文；其他游戏格式不变。
**根 play/schema/list/guide 源码未改，常驻字符增量0。** 四色牌规则仅随首次bootstrap或显式恢复发送，重复读取不再发规则、全局快照或历史。

调用沿用根工具薄壳，例如：

```json
{"game":"duel","action":"invite","params":{"game_type":"four_color_jiuzhi","target_player_count":4,"stake":0}}
{"game":"duel","action":"move","params":{"room_id":"ABCDEFGH","revision":7,"move":{"action":"act","action_id":"draw"}}}
{"game":"duel","action":"state","params":{"room_id":"ABCDEFGH","full_state":true}}
```

不要固定抄示例revision／action_id：只能原样选当前 `legal_actions` 的ID，外层revision取最新 `r`。九支强制吃时没有pass；群胡 `listen:<实体ID>` 表示打出并听牌。

- 首包：规则、协议说明、座位、公开状态、本人手牌／扣置对／待牌、合法动作。
- 后续：`from_revision` 必须等于本地保留r；`events=[actor,action,patch]` 顺序应用。普通字段替换，`discards+`追加，`melds+`按玩家追加。
- `private.+/-` 增删本人实体；`private.legal_actions` 完整替换（空数组表示等待）；`closed_pairs/waiting_codes` 若出现亦完整替换。
- 未知上下文自动首包；丢包或缺revision显式 `full_state:true` 得到自足快照与规则。数据库游标跨进程保留，不靠内存会话猜首包。
- 旧revision重放返回409，不是重复返回成功结果；需恢复后再决定动作。非参与者不能读取或恢复。
- 用真实响应的 `json.dumps(ensure_ascii=False,separators=(',',':'))` 测量**字符数，不是token数**。最终测量及测试结果见下一节；终局、多动作合并的包可能更大。

## UI与验证

沿用Duel正文13px、标题14px、辅助11px、既有颜色／边框／按钮；移动端动作44px。
三张规则卡全部可选、等宽全宽单列，没有2＋1。创建后房间标题和牌桌显示具体版本，不能再切规则。
九支可见自己扣置对、公开断张／明对；群胡可见听牌展示及自己的待牌；服务端合法动作驱动控件，结算逐席显示积分。

浏览器运行真实页面＋真实裁判＋临时SQLite，拦截全部网络，不使用生产房间。
360／430／1280分别检查三模式创建、选牌、503重试、刷新恢复、邀请胡牌、空堆、规则抽屉；群胡另点选听牌。
截图位于 `/tmp/cedartoy-four-color-ui/`：`{tenhu,jiuzhi,qunhu}-{rules,invite,opening,selected,reconnected,hu-offer,settlement,rule-text}-{360,430,1280}.png`，另有 `qunhu-listening-*`。
`measurements.json` 保存卡片几何、按钮高度、console检查。人工检查手机、宽手机、桌面截图；未声称做过实体手机或屏幕阅读器测试。

### 第三轮测试记录

- 四色牌＋共享MCP联测73项通过，104.035秒：`/tmp/cedartoy-four-color-check/three-modes-verified.log`。包括十胡24个固定种子整手、九支／群胡各12个随机发牌整手，逐步检查112实体、私牌隔离和零和；普通、邀请与系统NPC都合法到终局。
- 随后补充九支最后一张多人同听按翻牌者优先，以及普通／邀请非0注拒绝，2项通过（其中1项为新增测试）：`final-boundaries.log`。最终相关测试总数74项。之后首包移除重复且偏向Web格式的move_format说明，仅保留准确的MCP协议Guide；三模式整局增量重建专项2项重新通过（17.552秒），见 `wire-final.log`，以下字符测量采用该最终结果。
- 根schema/Gemini相关2项、路径Token／Bearer与身份覆盖相关4项均通过：`root-schema-third.log`、`root-auth-third.log`；根侧源码零修改。
- 浏览器三模式×三宽度通过记录：`browser-three-final.log`；追加邀请创建POST选择校验后最终记录为 `browser-acceptance.log`。手机／桌面截图已实际查看。
- Python/JS局部语法、tracked diff及新增文件空白检查均纳入最终检查；主仓无关脏改保持原样。

日志均位于 `/tmp/cedartoy-four-color-check/`。第三轮留档字符测量（本轮更新见第四轮）：

| 模式 | 四席首包范围 | 后续范围 | 后续平均 |
| --- | ---: | ---: | ---: |
| 十胡 | 3829–4063 | 66–591 | 256.0 |
| 九支 | 3958–3993 | 182–1162 | 234.7 |
| 群胡 | 3884–4118 | 154–1578 | 277.8 |

十胡显式恢复3471字符。九支／群胡恢复测试核对全状态与规则，并未另单独统计恢复字符。以上是本次样本，不是长度上限或token估算。

复跑：

```bash
cd /opt/cedartoy/vendor/duel
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest tests.test_four_color_tenhu tests.test_four_color_shapes tests.test_four_color_variants tests.test_four_game_incremental -v
PLAYWRIGHT_BROWSERS_PATH=/tmp/cedartoy-four-color-browsers PLAYWRIGHT_MODULE=/tmp/monopoly-pending-tools/node_modules/playwright node tests/check_four_color_browser.js
git diff --check
```

专项涵盖赢／错胡、冲突响应、明暗胡与点花、初始对子、断张、开交计头、听牌固定、花牌0／1／2张、空堆／循环流局、实体守恒、零和积分、NPC私牌隔离、随机整手、普通／邀请／人类／系统NPC、MCP逐席重建、并发重复提交、跨进程恢复及HTTP身份。
共享MCP专项只复跑受分派改动影响的四游戏；根侧仅鉴权代理与schema/Gemini相关测试，不跑全仓。

## 文件范围与交付边界

所有本任务源码修改限于 `vendor/duel`：

- `app/games/four_color_cards/`：三裁判、规则正文、实体、MCP；`app/games/__init__.py` 注册三个类型。
- `app/framework.py`：三模式强制revision；`app/main.py`：仅四色牌HTTP身份比对；`app/mcp_minimal.py`：仅此家族增量分支。
- `app/static/app.js` 与 `app/static/games/four_color_tenhu.{js,css}`：一个家族入口、三卡选择、共享牌面组件及独立动作。
- `tests/test_four_color_{tenhu,shapes,variants}.py`、`tests/four_color_ui_fixture.py`、`tests/check_four_color_browser.js`；本文。

交付的是明确版本＋平台约定的三种完整单手玩法；不是BGA三手赛事，也不是台湾／金门所有家规合集。
剩余操作是主助手的独立验收与部署；这里未使用真实账号或生产房间做上线冒烟。

## 第四轮：细长纸牌与MCP首包核对（2026-10-22）

仅修改展示和四色牌专属payload说明；三种裁判、计分、规则正文以及共享MCP分派均与本轮开工副本逐文件SHA-256一致。
没有外部运行依赖，没有生图、部署、重启、commit或push。

### 牌面与操作

已实际查看[台湾教育部实物图](https://sutian.moe.edu.tw/media/too/kukautian/23609_f.jpg)。
CSS以窄纸条、四色纸底、重复印字、细框和轻微纸纹表达实物特征，去掉麻将式厚底／圆角。
手牌纸面34×88（高宽2.59），公开组／弃牌26×68（2.62）；点击外框独立为44×100，牌纸变窄不缩小命中区域。

- 字形固定红／黄 `帥仕相俥傌炮兵`，绿／白 `將士象車馬包卒`。后端展示label、Web牌面和MCP映射一致；112实体ID及逻辑字类不变。
- 使用系统繁体字体栈与 `lang=zh-Hant`，不下载字体。双色墨字在红／黄／绿／白底的计算对比度分别5.39／6.05／5.10／11.31。
- 大于10张用两行横向滚动，否则单行；选牌后保留滚动位置。可用键盘聚焦滚动区、空格选牌，也可触控滑动／点击。
- 选中态有紫色轮廓、上提和勾选，保持纸底色；不能弃的牌禁用并标短横，辅助名称说明原因。等待／提交中禁用动作不降低文字透明度。
- 手机手牌右侧避开现有规则浮签；没有移动其它UI。公开组紧凑排布，展开弃牌区最高220px，超出局部滚动。

### 首包去重与固定样本测量

仅删FORMAT重复的完整恢复句，纠正两套牌面映射；`rules_version`只保留在 `board_state.rules_version`，移除快照顶层的重复副本。
首包仍无 `move_format`，协议说明只在 `protocol_guide`；显式恢复快照保留自足的 `move_format`。
完整 `rules_text` 未删任何字；合法action_id、r/revision、from_revision、private.+/-、规则及状态resync解释均保留。
四种既有增量玩法和根schema/list/guide未改。

下面采用固定随机种子9、普通四席房、相同身份／操作，以紧凑JSON的Unicode字符数做前后成对比较。
与第三轮不同种子／邀请样本的范围不混用；**字符数不是token数**。

| 模式 | 本轮修改前首包（四席范围） | 修改后首包 | 每包减少 | 同步后首个动作增量（前后相同） |
| --- | ---: | ---: | ---: | ---: |
| 十胡 | 3829–4063 | 3752–3986 | 77 | 250–302 |
| 九支 | 3835–3859 | 3752–3776 | 83 | 207–251 |
| 群胡 | 3760–3994 | 3684–3918 | 76 | 250–294 |

同一样本显式恢复：十胡3074→2997，九支3039→2956，群胡3005→2929字符。节省约2%，没有为了压缩而删必需规则。
原始响应和测量脚本在 `/tmp/cedartoy-four-color-visual/{wire-before.json,wire-after.json,measure_wire.py}`。
后续整局增量平均字符仍为十胡256.0、九支234.7、群胡277.8。

### 验证及文件范围

本轮7项相关测试通过（36.295秒），见 `/tmp/cedartoy-four-color-visual/tests.log`：
牌字／ID／Web／MCP一致性、完整规则仅首包一次、恢复、直接提交合法ID、三模式整局逐席重建、跨进程游标和HTTP权限。
没有重跑全量或不相关四游戏测试，共享分派未修改。

Playwright以原有临时SQLite fixture运行三规则×360／430／1280，检查真实手势横向滚动、44px命中区域、选中位置保持、键盘选择、禁止动作、失败重试、胡牌／结算、弃牌局部滚动。
前次 `/tmp/cedartoy-four-color-visual/final-ui/` 与 `browser-final.log` 是容量错误中断留档，不作为九场通过证据。本次 `acceptance-ui/` 与 `browser-resumed.log` 的九场均已通过，逐场保存结果。
包括 `*-opening-*`、`*-selected-*`、`*-hu-offer-*`、`*-settlement-*`、`*-discards-*`；保留原单列规则卡截图。
已实际查看三种规则各360／430／1280的选牌或开局截图，另查看群胡听牌、三规则结算、展开弃牌与规则卡截图：字形、细长比例与选中／禁用标记清晰，滚动仅限手牌／弃牌容器，三张规则卡同宽单列。

中断收尾只修正浏览器测试的结算弹窗流程（胡牌和流局后等待弹窗、点击完成、确认关闭，再查看弃牌），未修改共享弹窗；并补逐场日志与测量落盘。
看图发现九支空手牌仍占100px行高，局部CSS以 `:empty` 收起空行，增加结算空容器断言；修后九支三个尺寸的结算图均已实际查看。
最终复验产物另存 `acceptance-final/` 与 `browser-acceptance-final.log`，前八场通过后进程收到SIGTERM（退出143，无断言失败），保留全部证据。测试增加可选 `FOUR_COLOR_CASE=qunhu:1280` 单场过滤，补跑产物为 `acceptance-qunhu-1280/` 与 `browser-qunhu-1280.log`。
补跑群胡1280已通过（退出0），听牌截图已实际查看；修后九场全部有通过证据（八场＋单场补跑），无浏览器脚本错误。主仓及Duel的 `git diff --check`、未跟踪改动文件空白检查、浏览器测试语法检查通过，见 `resumed-checks.log`。
收尾另复跑 `tests.test_four_color_presentation` 两项通过（1.874秒），日志 `resumed-python.log`；确认三模式MCP牌字映射、首包完整规则且不重复move_format、后续增量与显式恢复未倒退。

本轮变更文件仅：

- `app/static/games/four_color_tenhu.css`、`four_color_tenhu.js`：纸面与手牌交互。
- `app/games/four_color_cards/cards.py`：仅两套显示字形常量；`mcp.py`：专属映射／去重。
- `tests/check_four_color_browser.js`、新 `tests/test_four_color_presentation.py`、`tests/test_four_color_tenhu.py`的一条显示字形断言。
- 本文。

剩余验证边界：未用实体手机或屏幕阅读器实测；不同系统的中文字体笔画有差异，尺寸和命中区域不依赖字体下载。
若临时MCP客户端曾读取快照顶层rules_version，应改读board_state.rules_version。主助手仍需独立验收，线上未部署。

## 上线后展示修正：Token 提示、席位牌数与公共桌面（2026-10-22）

本节对应用户确认已上线后的展示修正；前面“未部署”描述的是各轮开发交付时状态。本次仍只修改和测试，不部署／重启／commit／push。

- 大厅三规则切换沿用既有 `change` 链更新 Token 提示，分别为十胡约100–850、九支约100–750、群胡约100–700 token/轮；详细口径放原提示的title，不新增控件。
- 牌数来自公开 `hand_counts`，在现有上方对手／下方本人席位卡展示；九支扣置数量、群胡已听与各家结算分数也在席位卡。原有行动／离场状态保留，十胡两人桌采用同样席位布局。
- 删除空的逐人姓名＋手牌列表。公共桌面集中牌库、当前公开翻牌／弃牌、实际公开组／断张／听牌展示和可展开弃牌；没有公开牌时只显示简短空态，不用私牌或假牌装饰。手机右侧避开规则浮签。
- 主仓首页仅双弈 `metric` 改为00032，`desc`／`logs` 两处改32款。

### Token 采样口径

使用现有tiktoken 0.12.0的 `cl100k_base`（没有添加项目依赖），临时SQLite＋httpx ASGITransport调用真实 `/mcp/play`。
每模式四席、种子9／37，现有NPC策略各打完两局；预先消费每席bootstrap，统计正常轮次一次state增量＋对应move回复的紧凑JSON响应（`ensure_ascii=False,separators=(',',':')`）。排除bootstrap、guide、完整恢复、请求和模型思考，不是字符数换算。

| 模式 | 样本轮数 | 最小 | 中位数 | P95 | 最大 | UI估计范围 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 十胡 | 140 | 100 | 251 | 829 | 1741 | 100–850 |
| 九支 | 73 | 105 | 226 | 735 | 822 | 100–750 |
| 群胡 | 83 | 105 | 200 | 657 | 1228 | 100–700 |

UI为样本最小值到P95向外取整，**不是全范围或计费上限**。多人累计公开事件、合法选择及结算可超出；其他人数／模型tokenizer／客户端包装未采样。
复现脚本 `sample.py`／`count.py`、原始 `responses.jsonl.gz`、统计 `token-counts.json` 保存在 `/tmp/cedartoy-four-color-table-review/`。
所有规则、动作、服务端与MCP增量代码未改。

### 本次验证与边界

- 原临时SQLite浏览器fixture：三模式×360／430／1280，九组全部完成并输出passed；新增断言覆盖Token规则切换、每席牌数／结算分数、仅公开牌ID、无逐人重复牌数、空桌高度；兼跑选牌／触摸滑动、公开牌、听牌、胡牌／流局、断线重试、邀请房与十胡两人桌。每组无页面脚本错误。
- 已逐图查看九组布局，并查看两人十胡、群胡听牌、九支／十胡结算；手机公共桌面避让规则浮签。首页360／430／1280渲染断言通过，手机与桌面图片实际查看，00032及32款一致。
- 四项Python专项通过：`tests.test_four_color_presentation`两项，加共享UI的 `test_game_styles_preserve_shared_chat_scrolling_and_autocomplete`、`test_desktop_chat_rail_is_multiplayer_only_at_every_width`；没有运行全量或改游戏裁判。
- JS语法、主仓与Duel的 `git diff --check` 通过。
- 全部产物保留在 `/tmp/cedartoy-four-color-table-review/`：最终截图 `final/<模式>-<宽度>/`；逐组 `final-*.log`；汇总 `final-measurements.json`；首页 `homepage-*.png`／`homepage.log`；Python `python-tests.log`。部分浏览器进程在打印完整passed、写出测量后工具返回退出143；缺失的群胡430／1280已单独补跑完成全部断言，不能将上述结果等同于所有浏览器进程均正常退出。
- 未验证实体手机／屏幕阅读器、所有其他游戏整局，也未测其他人数或模型tokenizer的Token分布。本次没有操作生产房间，没有新增依赖，保留主仓无关修改。
