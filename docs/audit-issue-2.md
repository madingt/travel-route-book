# Issue #2 审计记录

审计对象：`main`（477e046）与 PR #1 `delegation-haiku-workers`（01da588）。日期：2026-11-10。
下面每一条都有可以重跑的命令或测试；标「未验证」的是没有条件实测的部分。

## 1. 基线（修改前实际跑出来的）
| 检查 | 结果 |
|---|---|
| PR #1 状态 | open，非草稿，1 个提交，与 `main` 可无冲突合并，没有 CI，没有评审意见 |
| PR #1 落后 `main` | 1 个提交（477e046，小红书改用中性搜索词） |
| 仓库里的自动化测试 | 只有 `test_routebook.cjs`（检查生成的 HTML），`delegate.py` 没有任何测试 |
| `build_routebook.py` + `test_routebook.cjs`（示例行程） | PASS，9 项全过 |
| `map_links.py`、`trip_to_xlsx.py`（示例行程） | 正常 |
| 新写的 29 个回归测试跑在 PR #1 上 | **20 个测试失败**（含子项共 24 处），输出见 `evidence/01-baseline-pr1-head.txt` |

数据契约盘点：`trip.json`（`references/routebook-html.md`）→ 任务卡 `work/tasks/<id>.json` → 原文 `work/raw/<id>/*` → 核对结果 `work/out/<id>.json`（格式写在 `agents/*.md`）→ `merge` 回填 `trip.json` → `build_routebook.py` 生成 HTML。修改前，卡与结果之间唯一的绑定是卡号，而卡号是位置。

## 2. 确认的缺陷与根因
| # | 现象（在 PR #1 上复现） | 根因 | 对应测试 |
|---|---|---|---|
| 1 | 把行程里某个位置的地点换成另一家，重跑 `init` 再 `merge`：新地点被填上**旧地点**的门牌、坐标、营业时间，状态还是「地图核实」 | 卡号 = `p-<天>-<第几个>`；`check` 只比对卡号；旧的 `out/`、`raw/` 留在原位 | `test_replaced_stop_does_not_inherit_old_result` |
| 2 | 在前面插入一个地点后重跑 `init`：后面所有地点的结果错位一格 | 同上 | `test_inserting_a_stop_keeps_results_on_the_right_place` |
| 3 | 同名但改了门牌（换了分店），旧结果照用 | 同上 | `test_changed_address_hint_invalidates_result` |
| 4 | 小红书卡改了口味限制，旧的打标结果仍然算通过 | 卡号只由店名决定，没有记录输入的版本 | `test_xhs_result_is_stale_when_taste_filter_changes` |
| 5 | `init` 之后从行程里删掉一个地点，`merge` 直接崩溃（`IndexError`） | 按天和下标去找地点 | `test_merge_survives_a_removed_stop` |
| 6 | 核对结果里地址、坐标、POI 编号全是编的，一条证据都没有，校验通过 | 只有填了营业时间才要求证据 | `test_found_without_any_evidence_is_rejected` |
| 7 | 引文指向一个不存在的文件，校验通过 | 文件找不到时退回「任何一个原文文件里有就行」 | `test_citation_of_a_missing_file_is_rejected` |
| 8 | 引文只有一个字（「0」「号」），校验通过 | 没有长度下限 | `test_trivial_quote_is_rejected` |
| 9 | 坐标不在任何原文里，校验通过 | 只检查是否在中国范围内 | `test_coordinates_not_in_raw_are_rejected` |
| 10 | 「提前 30 天」配一句真实但无关的引文（闭馆日那句），被合并进行程 | 只检查引文存在，不看引文和值有没有关系 | `test_real_quote_that_does_not_support_the_value_is_not_merged` |
| 11 | 字段类型写错（如 `closed_days` 写成字符串）校验通过；之后 `digest`/`merge` 崩溃，或闭馆日检查悄悄失效 | 没有类型检查 | `test_wrong_shapes_fail_validation_instead_of_crashing_later` |
| 12 | 连续 `merge --write` 两次，原始的 `trip.json` 找不回来 | 每次都用当前文件覆盖 `.bak` | `test_original_survives_repeated_merges` |
| 13 | 第二天什么都没变再 `merge` 一次，`changelog` 多一行 | 条目带当天日期，只和最后一行比较 | `evidence/02-baseline-changelog-growth.txt`、`test_merge_on_a_later_day_changes_nothing` |
| 14 | 手写的「苏州博物馆西馆」抢票条目被「苏州博物馆（本馆）」的自动条目覆盖 | 按名字子串匹配后直接替换 | `test_handwritten_ticket_for_another_venue_is_kept` |
| 15 | 两个不同名字的地点核到同一个地图点，没有任何提示 | 没有跨卡检查 | `test_two_places_resolving_to_one_poi_are_flagged` |
| 16 | `iso_date` 是周六、`weekday` 写周日，或 `iso_date` 根本不是合法日期：`conflicts` 不报，悄悄选一个用 | 解析失败静默回退 | `DateConflicts` 一组 |
| 17 | `add-xhs` 默认搜索词带「避雷」 | PR #1 早于 `main` 的 477e046，两者规则相反 | `test_default_xhs_queries_are_neutral` |
| 18 | 同一个地点出现在两天，出两张卡、采集两次 | 卡按位置出 | `test_same_place_twice_is_researched_once` |

没有复现、因此没有改的：引文编造（逐字比对本来就能拦住，`test_fabricated_quote_is_rejected` 修改前就通过）；截断的 JSON（本来就判失败）；dry-run 不写文件（本来就对）。

## 3. 修复后
```
$ python3 -m unittest discover -s tests
Ran 60 tests ... OK            （evidence/03-after-fixes.txt）
```
- 示例行程生成的 HTML 与修改前**逐字节相同**（sha256 `7f807be6…4941e`），`test_routebook.cjs` 9 项仍然 PASS；`map_links.py` 输出相同。
- `pyflakes` 对 `scripts/`、`tests/`、`bench/` 无告警。

## 4. 已验证 / 未验证
**已验证（有测试或实际运行）**：上表全部；任务状态流转、租约超时、失败两次转人工、批准只对当次结果有效、登录墙转人工、`state.json` 丢失或损坏后重建；时区对放号时间的影响；固定样例上三种分工方式的实际运行（`bench/RESULTS.md`）。

**未验证（这次没有条件实测）**：
- 真实网页的采集：高德、小红书、官方预约页都没有访问，`page-collector` 的说明只改了卡号示例和 `_blocked.txt` 的写法。
- `capture-amap`（需要 `AMAP_KEY`）：代码路径没动，但没有实际调用过高德接口。
- GitHub Actions：工作流文件已加，是否能在仓库里跑起来要等第一次触发。
- 值与引文的对照（时间、天数、星期、票价、门牌）是按常见中文写法做的，真实页面里的其他写法可能被转成人工复核（宁可多问，不会放过）。

## 5. 遗留风险
- 逐字引文不能证明网页内容是真的、是官方的、说的是这家店；选哪个候选、小红书立场和营销判断仍是模型判断。`references/delegation.md` 里写明了这一点。
- 旧版本（PR #1 格式）的 `work/` 目录不能直接接着用：旧卡会被标成需要重新 `init`，旧结果不会被采用。文件不会被删。
- 过午夜的行程时间（例如 00:30 到酒吧）对照营业时间时可能误报，这次没有处理。
- 两个并行的 `delegate.py` 同时写 `state.json` 没有加锁；流程要求由规划者一个人调用脚本。
