# CedarDuet third-party notices

本文件汇总 CedarDuet 随仓库分发或运行时直接使用的第三方规则核心。各项目的完整许可证文本以对应 `third_party/<name>/LICENSE` 为准；若上游目录内另含许可证，也一并保留。

| 目录 | 上游 | 固定版本 / revision | License | 使用范围 / 本地适配 |
|---|---|---|---|---|
| `chess_js` | https://github.com/jhlywa/chess.js | v1.4.0 / `ce1ff9e` | BSD-2-Clause | vendored CJS 规则引擎；CedarDuet 另做 Node bridge 与 FIDE 和棋语义适配 |
| `xiangqi_js` | https://github.com/lengyanyu258/xiangqi.js | `f9019ac2303d4b80ef0b82fd0515bfb55a80a62b` | BSD-2-Clause | 上游规则源码 + CedarDuet Node bridge |
| `onestraw_doudizhu` | https://github.com/onestraw/doudizhu | PyPI `doudizhu==0.1.5` | MIT | 保留 37 类/34,152 项牌型语义，适配为 Python 3 rank-count API；叫分和牌局流程由 CedarDuet 实现 |
| `rlcard_guandan` | https://github.com/Choysang/rlcard-guandan | v0.1.0 / `42f83aa8d84c0047473e069244e07db0c02af420` | MIT | 实际 vendored 上游 rule/environment core 与原生测试；小范围导入/运行适配详见目录 NOTICE |
| `golden_flower_evaluator` | https://github.com/luyao618/golden-flower | `35e74e929c5ed1856ade29a2b8340b19a5e8f014` | MIT | 最小三张牌 evaluator 语义适配；下注与隐私流程由 CedarDuet 实现 |
| `online_junqi` | https://github.com/samuelyuan/online-junqi | `f5ba2e8cedaa7e1dc3975349d5bbe097f2d5e13a` | MIT | 棋子碰撞、拓扑、铁路、工兵转弯、布阵与胜负核心；TypeScript rule core 同时保留 type-erased CommonJS runtime |
| `pypokerengine` | https://github.com/ishikota/PyPokerEngine | `a52a048a15da276005eca4acae96fb6eeb4dc034` | MIT | 牌/牌桌/下注/手牌评估/side-pot 核心；含在 NOTICE 中逐项披露的 HU、kicker、odd-chip、all-in 等修正 |
| `tenuki` | https://github.com/aprescott/tenuki | 0.3.1 / `aeedb4cd39d73242e49490aea359118ea5a4df23` | MIT | BoardState/Ruleset/Scorer/Region 等规则核心；固定 19×19、PSK、中国面积计分、贴 7.5 |
| `pymahjonggb` | https://github.com/ailab-pku/PyMahjongGB | 1.4.0 / `bb404f3f3480c2569e14d54043ad06e366e128df` | MIT | 原样源码编译 C++11 CPython 扩展；`MahjongFanCalculator` 判胡/番，`MahjongShanten` 算向听 |

## 规则验证参考与运行依赖的区别

只有上表这些代码会随 CedarDuet 分发或被运行时直接调用。开发过程中用于规则交叉核对、随机对局 differential test 或阅读规范、但没有复制进仓库的项目，不属于 vendored runtime dependency，也不据此宣称其作者为 CedarDuet 的代码贡献者。

## 修改披露

不要仅依赖本汇总判断“是否原样”。每个目录的 `NOTICE.md` 是该 vendor 的细粒度真源，记录了复制范围、固定 SHA/版本以及本地修改。升级第三方核心时必须同步更新对应 `NOTICE.md`、许可证文件（若上游变化）和本汇总。
