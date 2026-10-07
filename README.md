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
- **输出**：所有结果都从一份 `trip.json` 生成——单文件 HTML 路书（选项卡、每日时间轴、地图页、地址簿、附录）、每日地图链接、可选的地点清单表格。改行程只改 `trip.json`，重跑脚本即可。

## 目录结构

```
travel-route-book/
├── SKILL.md                      主流程（约 80 行），按需指向下面的文件
├── references/
│   ├── xhs-screening.md          小红书：搜索词、评论区读法、营销识别清单
│   ├── amap-web.md               高德：官方 API / 网页版查门牌、营业时间、坐标；地图链接
│   ├── amap-iphone-mirroring.md  iPhone 镜像 + 高德 App：建分天路线
│   ├── google-maps.md            境外：谷歌地图
│   └── routebook-html.md         trip.json 结构、生成与测试流程
├── assets/
│   ├── routebook-template.html   路书模板（导航脚本、无脚本兜底、深浅色都已处理）
│   └── trip.example.json         示例行程（仅演示格式）
└── scripts/
    ├── build_routebook.py        trip.json → HTML 路书
    ├── test_routebook.cjs        发布前检查（Playwright）
    ├── map_links.py              每日多点地图链接（高德 ≤10 点 / 谷歌 ≤9 途经点，自动拆分）
    ├── amap_lookup.py            高德官方 Web 服务 API 查地点（需 AMAP_KEY）
    ├── trip_to_xlsx.py           trip.json → 地点表 + 每日统计（公式）
    └── trip_lib.py               公共函数
```

## 快速试用

```bash
cd travel-route-book
python3 scripts/build_routebook.py assets/trip.example.json /tmp/routebook.html
node scripts/test_routebook.cjs /tmp/routebook.html        # 需要 playwright
python3 scripts/map_links.py assets/trip.example.json
python3 scripts/trip_to_xlsx.py assets/trip.example.json /tmp/places.xlsx   # 需要 openpyxl
```

## 安装

把 `travel-route-book/` 整个文件夹（含 references、assets、scripts）放进你的 Skills 目录：

- **Claude Code**：`~/.claude/skills/travel-route-book/`（个人）或项目内 `.claude/skills/travel-route-book/`。
- **Claude 应用**：把 `travel-route-book` 文件夹打包成 zip，在 Skills 设置里上传。

然后直接说「帮我做一个 X 天的 Y 城市攻略」即可触发。

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
