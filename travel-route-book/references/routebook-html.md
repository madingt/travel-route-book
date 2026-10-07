# 输出：trip.json → HTML 路书 / 地图链接 / 表格

所有输出都从**一份 `trip.json`** 生成。改行程 = 改 `trip.json` 再重跑脚本，不要手改生成的 HTML。

## 工作流
1. 复制 `assets/trip.example.json` 到工作目录，按真实行程填写（结构见下）。
2. `python3 scripts/build_routebook.py trip.json routebook.html`
3. `node scripts/test_routebook.cjs routebook.html`（必须 PASS 才发布；playwright 装在全局时前面加 `NODE_PATH=$(npm root -g)`）
4. 发布：有 Artifact 工具就发布到**同一个 URL**迭代；同时把离线 .html 发给用户（景区信号差时用）。
5. 需要时：`python3 scripts/map_links.py trip.json`（每天的地图链接）、`python3 scripts/trip_to_xlsx.py trip.json 地点清单.xlsx`（地点表 + 每日统计）。

## trip.json 结构
```jsonc
{
  "title": "城市 N 日 · 主题",          // 必填
  "subtitle": "日期 · 交通 · 住哪",
  "map": "amap",                        // 大陆 "amap"，境外 "google"
  "city": "苏州", "adcode": "320500",  // 高德搜索链接用
  "route_name": "苏州旅行",              // 手机高德里的路线名（可选）
  "store_key": "routebook-xxx-v1",      // 勾选框存储键；日期大改后换一个
  "notice": "",                          // 非空时总览顶部醒目显示（天气调整等）
  "assumptions": ["…"],
  "tickets": [{"when": "…", "platform": "…", "what": "…"}],
  "days": [{
    "id": "d1", "label": "D1", "date": "11/07 周六", "title": "…", "stay": "…", "summary": "…",
    "stops": [{
      "time": "09:30", "name": "…", "type": "景点|餐饮|咖啡|交通|住宿|活动",
      "note": "为什么去/怎么安排", "address": "门牌", "hours": "营业时间（核对过的）",
      "transit": "从上一站怎么来、多久", "phone": "",
      "lng": 120.1, "lat": 30.2,          // 大陆 GCJ-02；境外 WGS-84；没有就省略
      "amap_id": "B0…", "place_id": "…",   // 有就填，地址簿链接更准
      "status": "地图核实|只到片区|未定位|待核实|已移出"
    }],
    "rain_plan": "…", "backups": ["…"], "tips": ["…"]
  }],
  "appendix": {"review": [], "dropped": [], "todo": [], "sources": [{"title": "", "url": "", "note": ""}], "changelog": []}
}
```
脚本会检查：缺 id、重复 id、只填了 lng 或 lat 之一等，问题打印到 stderr。

## 生成的页面包含
- 顶部标题 + 吸顶标签栏：总览 / 抢票 / D1…Dn / 地图 / 地址 / 附录（空的部分自动省略）。
- 总览：notice、行程假设、每天一张卡（点开进当天）、手机高德路线入口。
- 抢票：带勾选框的清单（勾选存在本机浏览器）。
- 每天：日期与住处、摘要、「在高德/谷歌看今天的地点」按钮、时间轴（时间、地点、类型和状态标签、备注、门牌·营业·交通）、雨天方案、随缘备选、注意、上一天/下一天。
- 地图：每天的多点链接 + 编号列表。
- 地址簿：天 / 地点 / 门牌·营业·电话 / 地图链接 / 状态。
- 附录：反向审查、舍弃清单、待核实、改动记录、信息来源。

## 模板里已经处理好的坑（改模板时别弄丢）
- **站内跳转不能依赖 `<a href="#xx">`**：Artifact 外层会拦截 # 链接。脚本启动时把站内 # 链接改成按钮（移除 href、加 data-go、role=button、tabindex=0），用捕获阶段的 click/keydown 切换分页。
- CSS 有 `[hidden]{display:none!important}`，否则离线文件里所有分页叠在一起。
- **无脚本兜底**：分页默认全部显示，由脚本隐藏非当前页；手机文件预览等不跑脚本的环境就是一页长文。
- 勾选框状态存 localStorage，读写都 try/catch。
- 外部地图链接 `target="_blank"`，不转成按钮。
- 深浅色两套配色，390px 宽无横向溢出，打印时显示全部分页。
`test_routebook.cjs` 会逐条验证这些（包括模拟外层拦截 # 链接、禁用 JS）。

## 迭代
- 用户每补充一个事实（到达时间、机场、住哪、事件时间），立刻改 `trip.json` 骨架 → 重跑 → 联动检查抢票日历、地址簿、地图链接、手机高德路线、反向审查。
- 改动写进 `appendix.changelog`；重要调整写进 `notice`。
- 每轮回复：一句话说改了什么 + 列关键调整 + 还需要用户做的事。不复述整份行程。
