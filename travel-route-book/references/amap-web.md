# 高德：查地址、营业时间、坐标，生成地图链接

中国大陆境内的地点一律用高德（谷歌在大陆网络下通常加载不出来）。坐标系是 GCJ-02。

## 方式 A：官方 Web 服务 API（有 Key 时首选）
- 用户在高德开放平台申请「Web 服务」Key，设为环境变量 `AMAP_KEY`。
- 运行 `scripts/amap_lookup.py`（见脚本头部用法）：按关键词 + 城市搜索，输出 id、名称、地址、经纬度、电话、营业时间（有则给）。可直接回填 `trip.json`。
- 稳定、合规，推荐长期使用。

## 方式 B：高德网页版（没有 Key 时）
在浏览器工具里操作，只读，不改账号任何东西。
1. 需要登录时打开 amap.com，点头像 → 登录，**让用户自己登录**（扫码或短信）。Agent 不输入手机号、验证码，不替用户勾选条款。浏览器面板隐藏时先请用户调出来。
2. 搜索 URL：`https://www.amap.com/ssr/search?query_type=TQUERY&query=<URL编码的「城市 + 店名/路名」>&city=<adcode>`。**city 参数常被忽略、按 IP 定位**，query 里必须带城市名；有路名更准。
3. 更稳的取 id 方式：`https://ditu.amap.com/service/poiInfo?query_type=TQUERY&pagesize=3&city=<adcode>&keywords=<关键词>`，返回 poi_list（id、name、address、经纬度）。
4. 详情页 `https://www.amap.com/ssr/search/poi_detail?id=<id>&source=search_result`：读评分、人均、**营业时间（含每周分段和临时闭店）**、电话、门牌、最近地铁口。用 JS 读 `document.body.innerText` 比截图快。
5. 坐标：在 amap.com 页面里 `fetch('https://amap-pc-ssr.amap.com/ssr/api/getPoiInfo?id=<id>',{credentials:'include'})`，取 `geo.longitude/latitude`。
6. 注意：第 3、5 条是网页内部接口，没有文档、可能随时变化。失效时改用方式 A 或逐个打开详情页人工读取。

## 核对规则（两种方式都适用）
- **搜不到的店要当成信号**：可能已关店，路书里写明并移出。
- **营业时间逐条对照行程时段**：每个吃喝点都检查「到店时间是否在营业时段内」。常见坑：店铺只在下午营业，正好与核心活动时段重叠；老字号有午休。冲突就换店或换时间，替代店同样核对。
- 顺手发现「两个候选其实挨着」就合并成一段。
- 交通时长不要估：用高德路线规划（公交/步行/驾车）查一次再写进时间轴。

## 坐标系
- 高德用 GCJ-02。谷歌 Places 在大陆返回的坐标也是 GCJ-02（实测同一家店差 10–20 米），可直接用于高德标点；境外坐标是 WGS-84。

## 多点地图链接（路书用）
- 不要手拼，运行 `scripts/map_links.py trip.json`，按天输出链接。
- 格式：`https://uri.amap.com/marker?markers=lng,lat,名称|…&src=routebook&callnative=1`（callnative=1 让手机直接拉起高德 App）。
- **一条链接最多显示 10 个点**，超出被截断，所以按天各一条；超过 10 个点的一天，脚本会拆成多条。
- 生成后在浏览器里打开一条，确认点数和位置正确。

## 网页版收藏的限制
- 只有**一个平铺列表、不能建收藏夹**；只能「⋮ → 备注」给每条加前缀（如「<行程名> D2-3 …」）。备注会替换显示名，被编辑的条目会跳到顶部，列表约 20 条懒加载。
- 要建收藏夹或分天路线，走 `references/amap-iphone-mirroring.md`。
