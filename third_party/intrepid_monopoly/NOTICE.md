# 大富翁经典地产数据与规则对照

## 实际采用并分发的部分

- 上游：https://github.com/intrepidcoder/monopoly
- 固定 commit：`3537fc393930f1712e8b4d6fbe2e80b25419ceed`（2021-02-23）。
- 原作者版权：Copyright 2013-2020 Daniel Moyer。
- 许可证：MIT；完整、原始许可正文保留在本目录 [LICENSE](LICENSE)。主项目 LICENSE 未替换。
- 原文件：[classicedition.js](https://github.com/intrepidcoder/monopoly/blob/3537fc393930f1712e8b4d6fbe2e80b25419ceed/classicedition.js)。
- 本地文件 `estate_data.json` 从该文件的28个可购地块提取：每行是 `[位置, 价格, 原分组编号, 空地租金, 一屋租金, 二屋租金, 三屋租金, 四屋租金, 旅馆租金]`。车站/设施仅前三项；原分组1为车站、2为设施、3至10为同色街区。
- 本地修改：转成无执行逻辑的JSON；删除原英文地产名称、颜色/HTML和图像引用。中文地名、配色、SVG/CSS界面是本地实现。`app/games/monopoly.py` 使用该数字表，并按该文件的经典事件效果编写独立的结构化中文事件定义，未复制事件英文文案或浏览器回调。

## 阅读对照与本地规则选择

同 commit 的 `monopoly.js` 用于核对掷骰、拍卖、交易、建房库存、抵押/赎回、监狱与破产处理。没有将其 JS 引擎、AI、jQuery、网页、样式、图片、字体或品牌美术打包；本地 Python 阶段机、事务/身份边界、NPC策略和DOM棋盘独立实现。

赎回按抵押本金110%向上取整。与部分经典版本不同：经营限本人决策窗口；每个正常回合最多发起3笔交易；抵押地产转让/继承不立即收费，后续赎回时收10%；破产建筑统一半价变现、出狱卡回牌堆；出狱卡不参与交易。这些是本地明示规则，不宣称原样移植上游完整规则引擎。

## 未采用的阅读来源

- https://github.com/hencter/monopoly-3d-ai
- 阅读固定 commit：`fec44211e930ba42ad98d9172e0830aa55a170a6`。
- 核验 `LICENSE` 为 MIT（Copyright (c) 2026 hencter），阅读 `src/core/engine.js` 的回合、监狱及支付组织方式。
- 未发现本次需要搬用的局部；未复制代码/资源、未增加依赖。尤其不采用其3D、行业、股票、贷款系统、12000回合上限或到期资产排名。该项目不是本地分发依赖，不将其作者列为代码贡献者。
