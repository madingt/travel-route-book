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
- **输出**：单文件 HTML 路书（选项卡、每日时间轴、地图页、地址簿、附录），以及可选的地点清单表格。

## 安装

把 `travel-route-book/` 文件夹放进你的 Skills 目录：

- **Claude Code**：`~/.claude/skills/travel-route-book/SKILL.md`（个人）或项目内 `.claude/skills/travel-route-book/SKILL.md`。
- **Claude 应用**：在 Skills 设置里上传 `travel-route-book` 文件夹（或打包成 zip 上传）。

然后直接说「帮我做一个 X 天的 Y 城市攻略」即可触发。

## 依赖与前提（按需）

- 联网搜索（查一手信息、小红书笔记）。
- 浏览器工具：用来打开高德网页查地址与营业时间（需要时由**你自己**登录）。
- 「iPhone 镜像」步骤需要：macOS 上的 iPhone 镜像、Claude 桌面应用开启 Computer use、手机上已登录的高德地图。开始前请在高德设置里**关闭「截屏后触发分享」**。

## 安全边界

Skill 明确要求 Agent **不**替你登录、输入验证码、勾选协议、抢票或下单；不打开你的相册；路线默认私密；不删除你已有的收藏。

## 方法来源

方法论来自少数派文章：胡九思《别再把攻略全甩给 AI》（[sspai.com/post/114945](https://sspai.com/post/114945)），并加入了真实行程规划中踩过的坑。

## 许可

[MIT](LICENSE)
