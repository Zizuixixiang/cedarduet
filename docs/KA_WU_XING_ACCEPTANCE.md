# 卡五星交接与验收（2026-11-04）

代码位于 `feat/kawuxing-20261104`；仅修改隔离工作区，未 commit、push、部署或操作生产数据/进程。
主站配套在 `/tmp/cedartoy-kawuxing-20261104`，分支 `feat/kawuxing-platform-20261104`。
参考 [Radix Issue #2](https://github.com/Zizuixixiang/cedarduet/issues/2)。完整产品规则见 [KA_WU_XING.md](KA_WU_XING.md)。

## 已实现

- 独立 `ka_wu_xing`，固定3席、84张，庄14闲13；复用原麻将洗牌发牌、轮转、合法动作、响应窗口、抢杠、存档、私有/公开投影与同一网页 renderer。四人国标计分器和8番门槛未改变。
- 原生 `RegularShanten`/`SevenPairsShanten` 复用于向听和听牌（源码核对：四张可作两对）；卡五星的合法拆分和倍率独立实现，不调用国标番数。卡五星要求实际胡入5且可拆为4+5+6嵌张。
- 平胡自摸、至少×2接炮；碰碰胡、清一色、七对/龙七对/双龙及以上七对、明暗四归一、大小三元、手抓一、卡五星、杠上开花、抢杠胡、海底自摸。一般封顶8，双龙及以上七对封顶16，再计赢家亮倒×2。
- 亮倒与弃牌为一个服务端合法动作，公开固定手牌/听口，此后只能摸切或胡，禁止碰杠；新摸牌仍私有。荒庄每位亮倒者向每位未亮倒者付1；全亮/全未亮不赔。
- 单和，胡优先、同级近者先；过胡至自己下次摸牌解除；末张弃牌只准胡。直杠来源付2、补杠另两家各付1、暗杠另两家各付2，完成才入账，抢杠不收未成杠分，荒庄保留。
- 终局一次性结算，含杠分与荒庄；0底注只记局分。卡五星独立开启钱包余额裁剪，净亏不超余额，实际池按应得比例分配并稳定处理整数余数；批次幂等沿用框架。所有席位逻辑 delta 总和0；NPC不建钱包，沿用平台NPC政策。
- 本地NPC按权威动作、仅自身手牌作决策；原生向听指导弃牌。三位不同账号/家庭AI可复用 invite/join/start，无须人类。
- 网页普通开房和邀请开房的「牌」类下拉独立列出「麻将 · 4人」与「襄阳卡五星 · 3人」，直接提交各自game_type、人数固定4/3，没有二级麻将规则选择器；三人采用底/右/左三席及三片牌河，移除顶部空行；亮倒手牌在中央折行展示，杠分徽标、倍数/局分终局复盘。
- 根MCP新增按需 `get_guide(game="duel:ka_wu_xing")`；常驻 schema 无新增字段。

## 有意不实现

一炮双响、杠上炮、连续轮庄、买马、漂、癞子；不强制缺门。各项已明确写进规则，没有宣称支持。

## 已通过检查

使用现有只读解释器 `/opt/cedartoy/vendor/duel/.venv/bin/python`，全部对局/账号/筹码测试使用临时数据库。

- `tests.test_ka_wu_xing`：18项（最后新增的真实荒庄钱包链路单独通过）；涵盖牌型/反例、禁吃、亮倒锁听、隐私、过胡、响应顺序、杠/抢杠、荒庄、认输、12个seed连续NPC对局守恒、三AI邀请、新进程恢复、零和/余额裁剪/重复结算。
- `tests.test_mahjong`：14项，原国标规则和持久化回归通过。
- `tests.test_mahjong_frontend`：21项，原四人DOM/交互、三席逻辑顺序、公开手牌、亮倒确认与倍数文案通过。测试桩只扩展为允许两个ID注册同一个renderer。
- `tests.test_chips`：13项，现有筹码回归通过。
- 上述合计66个不同测试；初次组合运行65项，新增荒庄测试另跑1项；前端最终修改后重跑21项通过。
- 主站 `test_duel_ka_wu_xing` 3项、`test_duel_ai_only`、`test_mcp_schema_selection` 与 Gemini数值enum单项合计19项通过。
- 主站 `scripts/persistence_check.py --duel-ka-wu-xing` 已通过（当时2项，之后新增荒庄测试也单独通过）。只运行游戏专项，不触发其他游戏存档清理。
- `scripts/check_ka_wu_xing_ui.cjs --dom` 通过：真实页面普通/邀请选择器，3/4人路由、暗牌DOM、亮倒公开手牌与终局复盘。此模式没有布局引擎，不代表视觉验收。
- JS `node --check`、Python AST语法检查、两个工作区 `git diff --check` 通过。

复跑示例：

```sh
/opt/cedartoy/vendor/duel/.venv/bin/python -m unittest tests.test_ka_wu_xing tests.test_mahjong tests.test_mahjong_frontend tests.test_chips -q
DUEL_TEST_PYTHON=/opt/cedartoy/vendor/duel/.venv/bin/python JSDOM_MODULE=/opt/cedartoy/node_modules/jsdom node scripts/check_ka_wu_xing_ui.cjs --dom
```

## 未完成的验收与集成风险

2026-11-05 已补完本地 Chromium 145 的360/430px触控和1280px桌面截图核对（普通/邀请选项、三/四人牌桌），没有第二级麻将选择器；本次入口调整不改牌桌布局。原2026-11-04浏览器下载DNS失败的阻塞已解除。真实设备、Safari/Firefox、生产账号和生产链路未验收；脚本禁止外部请求，外部字体回退展示不代表线上字体验收。

```sh
DUEL_TEST_PYTHON=/path/to/python PLAYWRIGHT_MODULE=/path/to/playwright CHROMIUM_PATH=/path/to/chrome node scripts/check_ka_wu_xing_ui.cjs
```

默认检查360/430/1280px，可用 `DUEL_UI_SCREENSHOTS` 指定截图目录。移动端启用isMobile/hasTouch，断言输入媒体特征；入口采用视口截图，避免本环境全页截图后输入模拟变为鼠标的工具副作用。截图人工核对后，本次临时浏览器/npm缓存/截图目录均删除；复核时按上述命令重新生成。

设计依据：沿用页面Silkscreen/现有字体体系；pixel-field控件44px高，基础13px字/3px边，现有桌面覆盖16px字/2px边；麻将侧席既有11px名字、9px徽标、1px边和4–7px间距；新增只用原组件与局部3席grid，不叠加热修复规则。

首次本地测试发现主站worktree缺少ci-yu-wu/eco源码；仅把这些模块的`.py`复制进隔离工作区供import，未复制存档。NPC库在该worktree为空，测试使用临时persona夹具，决策仍是真实本地策略且禁止模型调用。测试后删除这两份源码副本与本次npm缓存/工具目录。

集成时必须同时带上游戏注册、前端JS/CSS/缓存版本和 `docs/KA_WU_XING.md`；主站Guide从已安装的 `vendor/duel/docs` 按需读文件。新增JSON状态无数据库schema迁移。保留生产NPC人设目录与数据库，不能用隔离worktree覆盖运行数据。

主助手核验后涉及服务：`cedartoy-duel`（引擎/静态页面），`cedartoy`（已导入guides及主站文案）；`cedartoy-duel-gateway` 是转发网关，本次没有改动它，无需为此重启。此任务没有重启任何服务。

## 引擎工作区文件清单

- `app/games/ka_wu_xing.py`（新）：规则、判型/听牌、亮倒、杠分、结算、NPC。
- `app/games/mahjong.py`：人数/牌墙扩展点与亮倒弃牌钩子，默认四人规则不变。
- `app/games/__init__.py`、`app/full_state.py`：catalog、底注说明、完整恢复。
- `app/chips.py`、`app/framework.py`：卡五星终局事务内余额裁剪。
- `app/static/app.js`、`app/static/index.html`、`app/static/styles.css`：独立下拉选项、加载和缓存版本。
- `app/static/games/mahjong.js`、`app/static/games/mahjong.css`：共享3/4人桌与亮倒/倍数展示。
- `tests/test_ka_wu_xing.py`（新）、`tests/test_mahjong_frontend.py`：专项回归。
- `scripts/check_ka_wu_xing_ui.cjs`（新）：DOM/可选真实浏览器验收。
- `README.md`、`docs/KA_WU_XING.md`（新）、本交接文件（新）。

## 2026-11-05 产品调整：两个独立游戏选项

以南杉最新决定替代此前的「统一入口」方案：普通新建和邀请组局都在「牌」类下拉直接显示「麻将 · 4人」与「襄阳卡五星 · 3人」。沿用人数升序排列，三人选项在四人之前。两者提交各自真实game_type，目标人数分别只能选4/3；删除麻将renderer的renderRulePicker、mahjongMode与专用布局规则，其他游戏规则选择器照旧。共享renderer、引擎、钱包、MCP和邀请服务逻辑未修改。

本轮差量文件：`app/static/app.js`、`app/static/games/mahjong.js`、`app/static/index.html`、`app/static/styles.css`（删除上一轮新增行，现已与HEAD一致）、`scripts/check_ka_wu_xing_ui.cjs`、`README.md`、`docs/KA_WU_XING.md`及本记录。主站只更新README与交接记录；既有房间名称映射已正确，无需另改UI。

本轮验证（未跑全量；上文66项和主站19项为上一轮记录）：

- `scripts/check_ka_wu_xing_ui.cjs --dom`：通过，使用真实catalog与页面函数，验证两个下拉的label/value、无二级控件、4→3→4人数、携带小机裁剪、普通NPC预览、实际普通/invite POST载荷及三/四席DOM。POST响应使用临时库房间投影夹具，未调用生产HTTP。
- `tests.test_mahjong_frontend`（21项）加国标发牌/固定座位、国标MCP/bootstrap/刷新恢复、卡五星三AI邀请/恢复/终局集成各1项：24项通过。临时数据库，与生产隔离。
- 浏览器专项通过：真实catalog加载共享renderer，360/430/1280px两个入口与3/4人桌，页宽/席位边界、浏览器JS错误检查及截图人工核对。没有新增CSS覆盖或独立牌桌UI。
- 两个游戏JS及验收脚本 `node --check`、两工作区 `git diff --check` 通过。最初DOM夹具设置误给const集合赋值、未初始化多选状态，修正测试后通过；无未解决的本轮测试失败。

本轮没有新增未实现的产品需求；此前列出的规则扩展仍不实现。未跑真实生产账号邀约及全量测试，未commit/push/部署；两个工作区与其他既有改动保留。本轮仅前端/文档，无新增服务端部署要求；整体待发布版本仍按上文由主助手核验集成。
