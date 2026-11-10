# travel-route-book · 旅游攻略路书 Skill

一个给 Claude 用的旅行规划 Skill：不让 AI「一句话生成流水账」，而是**人定方向与取舍，Agent 干体力活**——查一手信息、筛真实口碑、核地址和营业时间、把地点落进地图 App，最后交付一份可离线、可迭代的 HTML 路书。

> A Claude Skill for planning trips the careful way: first-hand facts, real (non-sponsored) reviews, verified addresses and opening hours, a day-by-day route pushed into the Amap (高德) phone app via iPhone Mirroring, and an offline-ready HTML route book that you iterate on.

## 它能做什么

- **定骨架**：按到达时间、核心事件（演出/开放日/会议）、住宿分段排出每天的动线，处理行李、到达日强度、工作日/周末错峰。
- **一手信息核对 + 抢票日历**：预约渠道、放号时间、闭馆日、最晚入场，核对不到就标「待核实」。
- **小红书筛选**：搜索词套路、评论区读法、**营销/广告识别清单**（命中 2 条按营销处理）和真实信号。
- **地图核实与标注**：中国大陆用高德、境外用谷歌；用高德网页查门牌/营业时间/坐标，并**逐条对照行程时段**找出冲突；生成按天的多点地图链接（每条 ≤10 点）。
- **iPhone 镜像 + 高德 App**：利用手机上已登录的高德，建一条私密「路线」，按天分组（D1…Dn）、按顺序排好，出门直接点着导航——网页版做不到收藏夹和分组。
- **反向审查**：以导游视角质询体力、折返、闭馆、营业时间、返程缓冲、雨天方案。
- **临近复核**：出发前两天自动查天气并调整路书。
- **分工省钱（可选）**：强模型只做规划和取舍；一个便宜模型（如 Haiku）的采集员串行抓网页原文，多个核对员并行把原文填成固定格式，每个字段都附逐字引文；脚本负责校验引文、合并进 `trip.json`，并对照营业时间、闭馆日和最晚入场。规划者只读一页摘要。
- **输出**：所有结果都从一份 `trip.json` 生成——单文件 HTML 路书（选项卡、每日时间轴、地图页、地址簿、附录）、每日地图链接、可选的地点清单表格。改行程只改 `trip.json`，重跑脚本即可。

## 目录结构

```
travel-route-book/
├── SKILL.md                      主流程（约 95 行），按需指向下面的文件
├── references/
│   ├── xhs-screening.md          小红书：搜索词、评论区读法、营销识别清单
│   ├── amap-web.md               高德：官方 API / 网页版查门牌、营业时间、坐标；地图链接
│   ├── amap-iphone-mirroring.md  iPhone 镜像 + 高德 App：建分天路线
│   ├── google-maps.md            境外：谷歌地图
│   ├── routebook-html.md         trip.json 结构、生成与测试流程
│   └── delegation.md             分工：强模型规划，便宜模型采集与核对
├── agents/                       便宜模型的 worker 定义（Claude Code 子 agent 格式）
│   ├── page-collector.md         采集员：按清单串行抓网页原文
│   ├── poi-checker.md            核对员：门牌、营业时间（结构化）、坐标
│   ├── ticket-checker.md         核对员：官方预约规则
│   └── xhs-grader.md             核对员：小红书逐篇打标、抽致命差评
├── assets/
│   ├── routebook-template.html   路书模板（导航脚本、无脚本兜底、深浅色都已处理）
│   └── trip.example.json         示例行程（仅演示格式）
└── scripts/
    ├── build_routebook.py        trip.json → HTML 路书
    ├── test_routebook.cjs        发布前检查（Playwright）
    ├── map_links.py              每日多点地图链接（高德 ≤10 点 / 谷歌 ≤9 途经点，自动拆分）
    ├── amap_lookup.py            高德官方 Web 服务 API 查地点（需 AMAP_KEY）
    ├── delegate.py               分工流水线：出卡、校验结果、任务状态与断点续做、合并、冲突检查
    ├── trip_to_xlsx.py           trip.json → 地点表 + 每日统计（公式）
    └── trip_lib.py               公共函数
```

## 快速试用

```bash
cd travel-route-book
python3 scripts/build_routebook.py assets/trip.example.json /tmp/routebook.html
node scripts/test_routebook.cjs /tmp/routebook.html        # 需要 playwright
python3 scripts/map_links.py assets/trip.example.json
python3 scripts/delegate.py conflicts assets/trip.example.json  # 营业时间/闭馆日对照
python3 scripts/delegate.py init assets/trip.example.json /tmp/work   # 看看会生成哪些任务卡
python3 scripts/trip_to_xlsx.py assets/trip.example.json /tmp/places.xlsx   # 需要 openpyxl
```

## 分工流水线一览
```bash
D=travel-route-book/scripts/delegate.py
python3 $D init trip.json work/          # 出任务卡（改了 trip.json 就重跑，随时可以）
python3 $D pending work/                 # 现在该做什么：采集 / 派给谁 / 哪些要人处理
python3 $D claim work/ <卡号…>           # 派出去之前登记，避免重复派
python3 $D check work/                   # 校验核对员交回的结果
python3 $D status work/                  # 每张卡的状态；断了从这里接着做
python3 $D digest work/                  # 一张卡一行的摘要，规划者只读这个
python3 $D merge trip.json work/ --write # 合并通过校验的结果（重复执行不会改任何东西）
python3 $D conflicts trip.json           # 营业时间、闭馆日、日期、时区对照
```
卡号按内容生成：调整行程顺序不会让结果作废，换了地点也不会把旧结果套到新地点上。细节见 `travel-route-book/references/delegation.md`。

## 测试
不需要联网，不需要任何账号，只用 Python 自带的库：

```bash
python3 -m unittest discover -s tests -v   # 59 个回归测试，约 15 秒
python3 bench/replay.py                    # 用录下来的真实 agent 输出重新打分
```

- `tests/`：在临时目录里跑真实的 `delegate.py` 命令。覆盖换地点后旧结果不被复用、编造或引错的引文被拒、重复合并不改文件、失败重试与断点恢复、日期与时区矛盾等。
- `bench/`：固定样例上「一个模型全做」与「多个便宜模型并行」的实测对比，结论和局限写在 `bench/RESULTS.md`。
- `docs/audit-issue-2.md`：这套测试最初抓到的缺陷、根因和修复前后的输出。

## 安装

把 `travel-route-book/` 整个文件夹（含 references、assets、scripts）放进你的 Skills 目录：

- **Claude Code**：`~/.claude/skills/travel-route-book/`（个人）或项目内 `.claude/skills/travel-route-book/`。
- **Claude 应用**：把 `travel-route-book` 文件夹打包成 zip，在 Skills 设置里上传。

然后直接说「帮我做一个 X 天的 Y 城市攻略」即可触发。

**可选：注册便宜模型的 worker（Claude Code）**。Skill 本身不能自带子 agent，想按名字调用的话，把 worker 定义拷进 agents 目录：

```bash
cp travel-route-book/agents/*.md ~/.claude/agents/
```

不拷也能用：规划者会读 `agents/*.md`，在派发时把模型指定为 `haiku`。没有子 agent 工具的环境会退回单模型，流程不变。

## 依赖与前提（按需）

- 联网搜索（查一手信息、小红书笔记）。
- Python 3（生成路书、地图链接）；`openpyxl`（导出表格）；Node + `playwright`（发布前检查）。
- 高德官方 Web 服务 Key（可选，设为 `AMAP_KEY`）；没有就用浏览器打开高德网页查（需要时由**你自己**登录）。
- 「iPhone 镜像」步骤需要：macOS 上的 iPhone 镜像、Claude 桌面应用开启 Computer use、手机上已登录的高德地图。开始前请在高德设置里**关闭「截屏后触发分享」**。

## 安全边界

Skill 明确要求 Agent **不**替你登录、输入验证码、勾选协议、抢票或下单；不打开你的相册；路线默认私密；不删除你已有的收藏。

## 方法来源

方法论来自少数派文章：胡九思《别再把攻略全甩给 AI》（[sspai.com/post/114945](https://sspai.com/post/114945)），并加入了真实行程规划中踩过的坑。

## 许可

[MIT](LICENSE)
