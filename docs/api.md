# 校捷通 · 接口契约（API Contract）

> 版本：v1.0 ｜ 状态：基线
> 用途：前后端对接的唯一契约。前端按本文档实现请求，后端按本文档实现路由（对应 C++ DAO）。
> 变更需在本文档登记，并同步更新 `db/cpp_driver` 对应 DAO。

---

## 0. 通用约定

### Base URL
```
https://api.xjt.example.com/api/v1     # 生产
http://127.0.0.1:8000/api/v1           # 本地开发（uvicorn）
```

### 认证（JWT Bearer）
- 微信登录换取 token 后，除 `auth/*` 与 `health` 外所有接口需携带：
  `Authorization: Bearer <token>`
- token 有效期 2h；刷新用 `refresh_token`（7d）。

### 统一响应体
```json
{ "code": 0, "message": "ok", "data": { } }
```
- `code=0` 成功；`message` 为给用户看的提示；`data` 按接口定义。

### 错误码分段
| 区间 | 含义 | 示例 |
|---|---|---|
| 0 | 成功 | — |
| 1001~1099 | 参数错误 | 1001 参数缺失 / 1002 格式错误 |
| 2001~2099 | 认证/权限 | 2001 未登录 / 2002 token 过期 / 2003 无权限 |
| 3001~3099 | 业务冲突 | 3001 座位已被预约 / 3002 重复投递 / **3003 内容未通过审核** |
| 5001~5099 | 服务端/DB | 5001 数据库错误 / 5002 模型服务不可用 |

### 分页约定
- 请求：`?page=1&size=20`（page≥1，size 1~100，默认 20）
- 响应 `data`：
```json
{ "items": [...], "total": 42, "page": 1, "size": 20 }
```

### 流式对话（SSE）
- `POST /chat/send` 返回 `text/event-stream`，事件：
```
event: chunk    data: {"delta":"你好"}
event: sources  data: [{"title":"图书馆借阅规则","source_url":"..."}]
event: done     data: {}
event: error    data: {"code":5002,"message":"模型不可用"}
```

### 时间格式
- 统一 `YYYY-MM-DD HH:mm:ss`（MySQL DATETIME）；日期 `YYYY-MM-DD`；时间 `HH:mm`。

---

## 1. 接口总览

| 模块 | 接口 |
|---|---|
| 认证 | wechat-login / refresh / logout |
| 用户 | me(查/改) / tags(查/改) |
| AI 助手 | chat/send(SSE) / chat/quick / conversations / messages / feedback |
| Agent | tasks(创建/列表/详情/取消) / reminders(列表/创建/完成) |
| 图书馆 | free-rooms / rooms/{id}/seats / reservations(创建/我的/取消) / occupancy |
| 二手 | items(列表/发布/我的发布/详情/改状态/ai-describe/ai-price) / wishes(创建/匹配) / orders(创建) |
| 兼职 | jobs(列表/详情/投递/可信度) / applications/me |
| 论坛 | topics(列表/创建/我的/详情/点赞/举报/hot/feed) / comments |
| 地图 | pois / nearby / navigate / building/{id} |
| 生活 | merchants / menu / orders / notices / notice-read / notice-feed |
| 管理 | metrics / knowledge/ingest / forum/audit / train/corpus |
| 收藏 | favorites(切换/我的收藏，target_type: topic·item) |

---

## 2. 认证与用户

### POST /auth/wechat-login — 微信登录
请求：
```json
{ "code": "wx.login 的 code" }
```
响应 `data`：
```json
{
  "token": "eyJhbGci...",
  "refresh_token": "eyJhbGci...",
  "user": {
    "id": 1, "openid": "oXJT_TEST_0001", "nickname": "测试用户A",
    "avatar": "", "role": 0, "grade": "2024级", "major": "计算机科学与技术",
    "campus": "前卫南区", "is_new": false
  }
}
```
> 后端：code→openid→查 `user`（无则自动创建）→签 JWT。对应 `UserDAO.find_by_openid/create`。

### POST /auth/refresh
请求 `{ "refresh_token": "..." }` → 响应 `{ "token": "...", "refresh_token": "..." }`

### POST /auth/logout — 登出（可无 token）

### GET /user/me — 我的信息
响应 `data`：同 login 的 `user` 结构。

### PUT /user/me — 更新资料
请求：
```json
{ "nickname": "新昵称", "avatar": "https://...", "major": "软件工程", "grade": "2024级", "campus": "前卫南区" }
```
响应 `data`：更新后的 `user`。

### GET /user/tags ｜ PUT /user/tags
- GET 响应：`{ "tags": ["学习","求职"] }`
- PUT 请求：`{ "tags": ["学习","二手"] }`（整体覆盖）

---

## 3. AI 助手

### POST /chat/send — 发送消息（SSE 流式）
请求：
```json
{ "conversation_id": 12, "content": "图书馆几点关门？", "quick": "" }
```
- `conversation_id` 为空则新建会话；`quick` 传快捷指令关键词走 RAG/指令模板。
- 响应为 SSE 流（见 §0），`done` 事件附带 `{ "conversation_id": 12, "message_id": 88, "sources": [...] }`。

### POST /chat/quick — 快捷指令（非流式）
请求 `{ "keyword": "查空教室" }` → 响应 `data`：`{ "conversation_id": 13, "answer": "今天第3节空闲教室：101、201...", "action": {"type":"library","params":{}} }`

### GET /chat/conversations — 会话列表
响应 `data`：`{ "items": [{"id":12,"title":"图书馆几点关门？","updated_at":"..."}] }`

### GET /chat/conversations/{id}/messages — 历史消息
响应 `data`：`{ "items": [{"id":1,"role":"user","content":"...","created_at":"..."}] }`

### DELETE /chat/conversations/{id} — 删除会话

### POST /chat/feedback — 回答反馈（数据闭环）
请求 `{ "target_type":"answer","target_id":88,"rating":4,"content":"很好" }`

---

## 4. Agent

### POST /agent/tasks — 创建任务（自然语言）
请求：
```json
{ "instruction": "提醒我明天下午4点交作业" }
```
响应 `data`（status=2 表示执行完成，`plan[].result` 为真实落库结果）：
```json
{
  "task_id": 7, "status": 2,
  "plan": [
    {"tool":"add_reminder","desc":"添加提醒",
     "result":{"reminder_id":3,"content":"交作业","remind_at":"2026-09-12 16:00:00"}}
  ],
  "result": {"source":"model","results":[{"tool":"add_reminder","ok":true,"result":{...}}]}
}
```
> **编排三级链路（v1.7）**：
> ① 模型 Function Call 主路径（`result.source=model`）；
> ② 模型不可用/未返回工具时，**规则执行器兜底**（`services/rule_executor.py`，`source=rule`，同样真实写库）；
> ③ 两者都未识别 → `status=3` + `error_msg`（**不再出现"未执行工具却报成功"的假成功**）。
> 规则兜底支持的指令示例：「10 分钟后提醒我交作业」「帮我预约 1 号座位明天上午 9 点到 11 点」「查一下空教室」「出一本高数教材，25 元」。

### GET /agent/tasks — 任务列表
查询参数 `?status=&page=&size=`；`data.items[]`：
```json
{ "id":7, "task_type":"reserve", "title":"预约图书馆座位", "status":2,
  "result": {"reservation_id": 5}, "error_msg":"", "created_at":"..." }
```
- status：0待执行 1执行中 2成功 3失败 4已取消

### GET /agent/tasks/{id} — 任务详情（含状态轮询用）

### POST /agent/tasks/{id}/cancel — 取消任务
响应：`{ "task_id":7, "status":4 }`

### GET /agent/reminders — 我的提醒
`data.items[]`：`{ "id":3, "content":"下午4点选修课", "remind_at":"2026-08-24 16:00", "is_done":0 }`

### POST /agent/reminders — 创建提醒
请求 `{ "content":"交作业", "remind_at":"2026-08-25 09:00" }` → `{ "reminder_id":4 }`

### PUT /agent/reminders/{id}/done — 标记完成

---

## 5. 图书馆

### GET /library/free-rooms — 空教室
查询参数：`?campus=&floor=&period=`（period 缺省=当前节次）
响应 `data.items[]`：
```json
{ "id":3, "building_name":"第二教学楼", "floor":1, "room_name":"101 教室", "capacity":80, "has_power":0 }
```

### GET /library/rooms/{roomId}/seats — 座位列表
查询参数：`?date=2026-08-24`
响应 `data.items[]`：
```json
{ "id":1, "seat_no":"A01", "has_power":1, "is_window":1, "reserved":0 }
```

### POST /library/reservations — 预约座位
请求：
```json
{ "seat_id": 1, "date": "2026-08-24", "begin_time": "09:00", "end_time": "11:00" }
```
响应：`{ "reservation_id": 6 }`
> 冲突时 `code=3001`。对应 `LibraryDAO.reserve`（事务内校验占用）。

### GET /library/reservations/me — 我的预约
`data.items[]`：
```json
{ "id":6, "building_name":"中心图书馆", "room_name":"二楼社科阅览室", "seat_no":"A01",
  "reserve_date":"2026-08-24","begin_time":"09:00","end_time":"11:00","status":0 }
```

### POST /library/reservations/{id}/cancel — 取消（仅本人）
响应：`{ "reservation_id": 6, "status": 2 }`

### GET /library/rooms/{roomId}/occupancy — 拥挤度/AI 预测
查询参数：`?days=7`
响应 `data`：
```json
{ "history": [{"record_date":"2026-08-18","period":9,"occupancy_rate":62.5}],
  "prediction": 58.2, "room_id": 1 }
```
> `prediction` 来自 `LibraryDAO.predict_occupancy`（当前为均值占位）。

---

## 6. 二手

### GET /secondhand/items — 物品列表
查询参数：`?category=教材&q=高数&page=1&size=20`
响应 `data.items[]`：
```json
{ "id":1, "title":"高数教材", "category":"教材", "price":25.00,
  "condition_level":9, "images":["https://..."], "trust_score":80,
  "seller_name":"测试用户A", "created_at":"2026-08-24 10:00" }
```

### GET /secondhand/items/{id} — 物品详情（v1.8 新增）
响应 `data`：物品完整信息（含 `images` 图片列表、`seller_name`、`status`、`audit_status`、`view_count`）；浏览量 +1。
> B9 修复：发布时 `images` / `condition_level` 此前未落库，现已补写，详情可正常返回图片。

### POST /secondhand/items — 发布闲置
请求：
```json
{ "title":"九成新高数教材", "description":"微积分上册，无笔记", "category":"教材",
  "price":25.00, "condition_level":9, "images":["https://..."] }
```
响应：`{ "item_id": 3, "audit_status": 1, "source": "model" }`
> v1.8：`images` 与 `condition_level` 已随发布落库（此前丢失，见上）；发布后可用 `GET /secondhand/items/{id}` 查看图片。
> v1.5 起发布同样过内容审核：敏感词 → 3003 且 `audit_status=2`；可疑 → 待审；正常 → 立即上架。

### POST /secondhand/items/ai-describe — AI 辅助发布（图像→描述/定价）
请求（B9 扩展，旧字段兼容）：
```json
{ "title":"高数教材", "category":"教材", "condition_level":9,
  "user_note":"微积分上册，无笔记", "image_url":"" }
```
响应 `data`（定价带库内同类样本依据）：
```json
{ "title":"九成新高数教材", "description":"微积分上册，无笔记，成色好…",
  "selling_points":["成色9/10，保存良好","教材分类，同类需求稳定"],
  "suggested_price":22.5, "price_min":18.0, "price_max":27.0,
  "reason":"库内同类 3 件均价 25.0 元，按成色 9/10 折算",
  "category":"教材", "condition_level":9, "sample_count":3, "avg_price":25.0,
  "source":"model" }
```
> `source`：`model`（模型生成）/ `stat`（库内同类统计兜底）/ `fallback`（无样本，分类通用区间）。
> 模型不可用或输出异常时自动降级为模板文案 + 统计定价，不阻塞发布；
> 模型建议价超出统计区间 [0.5×min, 1.5×max] 时回退统计值（价格护栏）。

### POST /secondhand/items/ai-price — 纯定价建议（v1.8 新增）
请求 `{ "category":"教材", "condition_level":9 }`
响应 `data`：
```json
{ "suggested_price":22.5, "price_min":18.0, "price_max":27.0,
  "sample_count":3, "avg_price":25.0, "source":"stat",
  "reason":"库内同类 3 件均价 25.0 元，按成色 9/10 折算" }
```

### PUT /secondhand/items/{id}/status — 改状态
请求 `{ "status": "1" }`（0在售 1已售 2下架）

### GET /secondhand/items/mine — 我的发布（v1.2 新增）
查询参数：`?page=&size=`
`data.items[]`（多含 `status` / `audit_status` 供前端管理展示）：
```json
{ "id":3,"title":"九成新高数教材","category":"教材","price":25.00,"images":["..."],
  "status":0,"audit_status":1,"view_count":10,"created_at":"2026-08-24 10:00" }
```

### POST /secondhand/wishes — 发布求购
请求 `{ "content":"求购高数教材","category":"教材","budget":30 }` → `{ "wish_id": 2 }`

### GET /secondhand/wishes/{id}/match — AI 供需匹配
响应 `data.items[]`：`{ "id":3,"title":"高数教材","price":25,"seller_name":"..." }`

### POST /secondhand/orders — 创建订单
请求 `{ "item_id":3, "remark":"周末自取" }` → `{ "order_id": 8, "amount": 25.0 }`

> **v1.4 契约变更（P0 安全加固）**：请求体不再接受 `seller_id` / `amount`。
> 卖家与成交价一律由服务端从 `secondhand_item` 反查，避免客户端伪造价格或
> 向任意卖家下单（SEC-05）。下单使用原子抢占
> `UPDATE secondhand_item SET status=1 WHERE id=? AND status=0`，
> 抢占失败（已售/已下架）返回业务码 `3001`，从根上防超卖（TXN-02）；
> 购买自己发布的物品同样返回 `3001`。

---

## 7. 兼职实习

### GET /jobs — 岗位列表
查询参数：`?type=实习&q=前端&page=1&size=20`
响应 `data.items[]`：
```json
{ "id":1, "title":"校园大使", "job_type":"兼职", "salary":50, "pay_unit":"元/天",
  "work_time":"周末", "location":"校内", "risk_level":0, "trust_score":85,
  "company_name":"某某科技", "credit_score":90 }
```

### GET /jobs/{id} — 岗位详情

### POST /jobs/{id}/apply — 投递
请求 `{ "resume": "自我介绍..." }` → `{ "application_id": 4 }`
> 重复投递 `code=3002`。

### GET /jobs/applications/me — 我的投递
`data.items[]`：`{ "id":4,"job_title":"校园大使","company_name":"某某科技","salary":50,"status":0,"created_at":"..." }`

### GET /jobs/{id}/trust — 可信度评分
响应 `data`：`{ "job_id":1, "trust_score":85, "risk_level":0, "company_credit":90 }`

---

## 8. 论坛

### GET /topics — 帖子列表（仅已审核）
查询参数：`?category=学习&page=1&size=20`
响应 `data.items[]`：
```json
{ "id":1, "title":"期末复习互助", "category":"学习", "like_count":12, "comment_count":3,
  "view_count":100, "ai_summary":"期末复习资料共享...", "is_hot":0,
  "author_name":"测试用户A", "created_at":"2026-08-24 09:00" }
```

### POST /topics — 发帖
请求 `{ "title":"求高数资料","content":"...","category":"学习" }`
响应 `data`：`{ "topic_id": 5, "audit_status": 1, "ai_summary": "求高数复习资料，共享笔记", "source": "model" }`
> **自动审核（v1.5：词库 `audit_word` + 模型二次判定）**：
> - 敏感词命中 → 拒绝：入库 `audit_status=2` 并返回**错误码 3003**；
> - 可疑词/联系方式/外链 → `audit_status=0` 转人工待审（管理端处理）；
> - 正常内容 → `audit_status=1` **立即可见**并附 `ai_summary` 摘要；
> - 模型不可用 → 自动降级为规则判定，**不阻塞发帖**。
> 轮询可见性：`GET /topics/{id}/audit-status`。

### GET /topics/mine — 我的帖子（v1.2 新增）
查询参数：`?page=&size=`；返回自己发的全部帖子（含审核状态，便于展示"审核中/被拒"）。
`data.items[]` 结构同列表接口 + `audit_status`（0待审 1通过 2拒绝）。

### 收藏（通用，v1.2 新增；支持帖子 topic / 二手物品 item）
- `POST /favorites` — 收藏/取消收藏（切换）：请求 `{ "target_type":"topic", "target_id":5 }`
  响应：`{ "target_type":"topic", "target_id":5, "favorited":true }`（再次调用即取消）
- `GET /favorites?target_type=topic&page=&size=` — 我的收藏列表（分页）
  - `target_type=topic`：`items[]` = 帖子摘要 + `favorited_at`（收藏时间）
  - `target_type=item`：`items[]` = 物品摘要（含 images/price/status）
  - 收藏列表自动剔除已删除/已下架对象
- 帖子详情 `GET /topics/{id}` 响应新增 `favorited` 字段（供收藏按钮高亮）

### GET /topics/{id} — 详情（含评论）
响应 `data`：
```json
{ "id":5, "title":"...", "content":"...", "author_name":"...", "liked":false,
  "comments":[{"id":1,"author_name":"...","content":"...","created_at":"..."}] }
```

### GET /topics/{id}/audit-status — 审核状态轮询（v1.5）
响应 `data`：`{ "topic_id": 5, "audit_status": 1, "passed": true, "ai_summary": "..." }`
> 发帖后前端轮询该接口确认可见性（audit_status：0 待审 / 1 通过 / 2 拒绝）。

### POST /topics/{id}/like — 点赞/取消
响应：`{ "topic_id":5, "liked": true, "like_count": 13 }`

### POST /topics/{id}/comments — 评论
请求 `{ "content":"同求" }` → `{ "comment_id": 3, "audit_status": 1 }`
> 评论同样经内容审核（v1.5）：命中敏感词 → 返回 3003 且不入库。

### POST /topics/{id}/report — 举报
请求 `{ "reason":"广告" }` → `{ "report_id": 1 }`

### GET /topics/hot — 热点
响应 `data.items[]`：`{ "id":1,"title":"...","is_hot":1 }`（对应 `ForumDAO.hot_topics`）

### GET /topics/feed — 个性化推荐（B11 落地）
查询参数：`?page=&size=`
响应 `data.items[]` 在帖子字段基础上新增推荐信息：
```json
{ "id":12, "title":"考研数学经验分享", "category":"学习",
  "score":2.6, "reason":"因为你关注了考研", "matched_tags":"考研" }
```
> 打分 = 兴趣标签命中 +1.5/个（上限 3）｜行为偏好（点赞/收藏分类命中）+0.8｜热度（赞/评/阅归一化）上限 +2.0｜3 天内时效加成 0.8→0。
> 冷启动（无标签、无行为）自动回落为热度排序；每条均带 `reason` 推荐理由。

---

## 9. 地图

### GET /map/pois — POI 列表
查询参数：`?category=教学楼`
响应 `data.items[]`：`{ "id":1,"name":"中心图书馆","category":"图书馆","latitude":43.88,"longitude":125.32 }`

### GET /map/nearby — 周边服务（定位联动）
查询参数：`?lat=43.88&lng=125.32&radius=500`
响应 `data.items[]`：POI + `distance`（米）。

### POST /map/navigate — 步行路线规划（B13 路网升级）
请求 `{ "to_poi_id": 1, "from_lat": 43.88, "from_lng": 125.32 }`
（起点坐标可省略；省略时取**距目标最近的 POI** 作为校园地标锚点）
响应 `data`：
```json
{ "distance": 760, "straight_distance": 610, "duration": 9,
  "path": [{"lat":43.8801,"lng":125.3202}, {"lat":43.8805,"lng":125.3210}],
  "algorithm": "astar-grid", "start_source": "user_location",
  "target": {"id":1, "name":"中心图书馆"} }
```
> v1.11：由「两点直线」升级为**网格 A* 路网寻路**（30m 网格，建筑按 45m 缓冲作障碍，
> 八方向搜索 + 共线压缩）；`straight_distance` 用于对比绕行增量，`algorithm` 标明
> `astar-grid`（正常）或 `straight-fallback`（障碍封死时回退直线）。

### GET /map/building/{id} — 建筑详情
响应 `data`：`{ "id":1,"name":"中心图书馆","floors":5,"hours":"08:00-22:00",
  "floor_plan":[{"floor":2,"name":"二楼社科阅览室","seats":120,"occupancy":45}],
  "services":["library","study"] }`

---

## 10. 生活服务

### GET /life/merchants — 商家列表
查询参数：`?category=食堂&page=&size=`
响应 `data.items[]`：`{ "id":1,"name":"湖畔餐厅","category":"食堂","delivery_fee":0,"min_order":0,"avg_score":4.5,"business_hours":"07:00-21:00" }`

### GET /life/merchants/{id}/menu — 商家菜单
`data.items[]`：`{ "id":1,"name":"红烧肉套餐","price":15.00,"sales_count":120 }`

### POST /life/orders — 下单
请求：
```json
{ "merchant_id":1, "items":[{"id":1,"num":2}], "address":"三公寓", "contact":"测试用户A", "contact_phone":"13800000000", "remark":"少辣" }
```
响应：`{ "order_id": 9, "pay_amount": 30.00 }`

### GET /life/orders/{id} — 订单详情/配送进度
`data`：`{ "id":9,"status":2,"items":[...],"total_amount":30.00,"delivery_fee":0 }`
- status：0待支付 1已支付 2配送中 3已完成 4已取消

### GET /life/notices — 通知列表
查询参数：`?category=选课&target_grade=2024级&page=&size=`
响应 `data.items[]`：`{ "id":1,"title":"2026年秋季学期选课通知","source":"教务处","category":"选课","publish_time":"..." }`

### POST /life/notices/{id}/read — 标记已读（精准推送回执）
> v1.9 起同步更新投递记录（notice_delivery）的已读状态。

### GET /life/notice-feed — AI 精准通知推送（v1.9 升级）
查询参数：`?page=&size=`
响应 `data.items[]` 在通知字段基础上新增推荐信息：
```json
{ "id":2, "title":"2026年秋季学期选课通知", "category":"选课",
  "score":2.3, "reason":"你关注了选课", "matched_tags":"选课,教务", "is_read":0 }
```
> 打分维度（可解释）：兴趣标签命中 +1.5/个（上限 3）｜行为偏好（点赞/收藏分类命中）+0.6｜年级匹配 +0.8｜校区匹配 +0.5｜7 天内时效衰减 0.5→0。
> 拉取时懒生成投递记录（notice_delivery）并回执曝光。

### GET /life/notices/unread-count — 未读数（v1.9 新增）
响应 `data`：`{ "count": 3 }`（与未读列表口径一致，批量已读后归零）

### GET /life/notices/unread — 未读列表（v1.9 新增）
查询参数：`?page=&size=`；`data.items[]` 同 notice-feed，仅含 `is_read=0`，按得分倒序。

### POST /life/notices/read-batch — 批量已读（v1.9 新增）
请求 `{ "notice_ids": [1,2,3] }` → 响应 `{ "updated": 3 }`
> 更新投递表并同步旧回执表（notice_read），保证未读口径一致。

---

## 11. 管理端（管理员角色）

### GET /admin/metrics — 系统指标
响应 `data`：
```json
{ "users": 128, "topics": 45, "db": { "pool": {"idle":2,"active":0}, "latency_ms": 3 } }
```

### POST /admin/knowledge/ingest — 知识入库（RAG）
请求 `{ "title":"图书馆借阅规则","category":"图书馆","content":"...","source_url":"..." }`
响应：`{ "doc_id":1, "chunks":12, "status":"ok" }`（写库后自动分块+向量化；embedding 未就绪时 `status="embed_failed"`，Ollama 就绪后重跑建索引）

### POST /admin/knowledge/index — 重建知识库索引
查询参数：`?force=true`（全量重建，默认 false 只处理待向量化文档）
响应：`{ "total":12, "ok":12, "failed":0, "details":[{"doc_id":1,"chunks":12,"status":"ok"}] }`

### GET /admin/forum/audit — 待审核帖子
`data.items[]`：`{ "id":5,"title":"...","content":"...","author_id":1 }`（对应 `ForumDAO.pending_audit`）

### POST /admin/forum/audit/{id} — 审核
请求 `{ "pass": true, "summary": "期末复习互助" }`
> pass=true 设 `audit_status=1` + `ai_summary`；false 设 2。

### POST /admin/forum/audit/batch — 批量审核（v1.5）
请求 `{ "topic_ids": [5,6,7], "pass": true, "summary": "" }`（summary 留空则保留原摘要）
响应 `data`：`{ "processed": 3, "audit_status": 1 }`

### GET /admin/train/corpus — 训练语料
查询参数：`?source_type=forum&is_cleaned=0&page=&size=`

---

## 12. 附：实现注意事项（前后端）

1. **SSE 解析**：前端用 `wx.request` 无法流式，改用 `wx.request` 长连接 + 后端 `StreamingResponse`，或小程序 `EventSource` 适配（微信需 `enableChunked`）。
2. **token 失效**：接口返回 `2001/2002` 时前端统一跳登录。
3. **图片上传（B14）**：`POST /upload/image`（multipart；魔数白名单 + 5MB 上限）→ 返回**带签名的访问 URL**（`?e=过期时间戳&s=HMAC 签名`，有效期默认 7 天）。`/static/uploads/*` 无签名或签名过期返回 **403**（可用 `XJT_UPLOAD_SIGNED_URL_ENABLED=false` 关闭校验）。存储经 `services/storage.py` 抽象，`XJT_STORAGE_BACKEND=local|s3|oss`（对象存储接口已预留）。
4. **日期时区**：后端统一用服务器本地时间（`Asia/Shanghai`）。
5. **接口与 DAO 对应**：每个接口标了对应 C++ DAO，实现时直接调 `jt_db.XXXDAO()`。
6. **健康探针（v1.6）**：`GET /health`（基础，前端存活探测）；`GET /health/detail`（可观测：Ollama 可达性/模型清单/向量库/知识库规模/检索模式与降级原因）；`GET /health/selfcheck`（一键自检 embed + 检索 + 生成）。
7. **审核词库**：`audit_word` 表（`db/sql/11_audit.sql`），服务端 60s TTL 缓存 —— 增删词条无需重启，最多 1 分钟生效。

### 12.1 新接口联调用例（B2 / B5，PowerShell 实测通过）

前置：本地后端已启动（`cd backend` + `uvicorn app.main:app --port 8000`），先登录拿 token：

```powershell
$base = "http://127.0.0.1:8000/api/v1"
$login = Invoke-RestMethod -Method Post -Uri "$base/auth/wechat-login" -ContentType "application/json" -Body '{"code":"test1"}'
$H = @{ Authorization = "Bearer $($login.data.token)" }
```

**1) 我的帖子 / 我的发布 / 我的收藏（三个列表接口）**

```powershell
(Invoke-RestMethod -Uri "$base/topics/mine?page=1&size=10" -Headers $H).data | ConvertTo-Json -Depth 5
(Invoke-RestMethod -Uri "$base/secondhand/items/mine?page=1&size=10" -Headers $H).data | ConvertTo-Json -Depth 5
(Invoke-RestMethod -Uri "$base/favorites?target_type=topic&page=1&size=10" -Headers $H).data | ConvertTo-Json -Depth 5
```

**2) 收藏切换（同一请求重复调用即取消收藏）**

```powershell
$body = '{"target_type":"topic","target_id":1}'
Invoke-RestMethod -Method Post -Uri "$base/favorites" -Headers $H -ContentType "application/json" -Body $body
# → data: { "target_type":"topic", "target_id":1, "favorited":true }
```

**3) Agent 一句话任务（Function Call 真执行）**

```powershell
$body = '{"instruction":"提醒我明天下午4点交作业"}'
Invoke-RestMethod -Method Post -Uri "$base/agent/tasks" -Headers $H -ContentType "application/json" -Body $body | ConvertTo-Json -Depth 6
```

实测响应（status=2 成功；模型不可用时降级为 `note` 占位，接口不报错）：

```json
{ "code": 0, "message": "ok", "data": {
  "task_id": 3, "status": 2,
  "plan": [ { "tool": "add_reminder", "desc": "添加提醒",
              "result": { "reminder_id": 1, "content": "交作业", "remind_at": "2026-09-10 16:00:00" } } ],
  "result": { "results": [ { "tool": "add_reminder", "ok": true, "desc": "添加提醒",
              "result": { "reminder_id": 1, "content": "交作业", "remind_at": "2026-09-10 16:00:00" } } ] }
} }
```

预约座位同理：`{"instruction":"帮我预约1号座位明天上午9点到11点"}` → `plan[0].result.reservation_id`；时段冲突时 `status=3` 且 `error` 给出冲突原因。

**4) 回归脚本**：`pwsh backend/tests/verify_b2.ps1`（我的帖子 / 收藏切换 / 我的发布 全链路断言，输出 `B2 PASS`）

---

## 13. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-24 | 初始契约：11 模块，约 60 个接口 |
| v1.1 | 2026-08-24 | RAG：ingest 响应加 `status`；新增 `POST /admin/knowledge/index` 重建索引 |
| v1.2 | 2026-09-08 | B2 补齐缺口接口：`GET /topics/mine`（我的帖子）、`GET /secondhand/items/mine`（我的发布）、通用收藏 `POST/GET /favorites`（topic/item）；帖子详情新增 `favorited` |
| v1.3 | 2026-09-09 | B5 Agent Function Call：`POST /agent/tasks` 由模型解析意图并真实执行工具（add_reminder/reserve_seat/query_free_room/post_secondhand），响应新增 `result`（执行明细）；模型不可用时降级关键词占位 |
| v1.4 | 2026-09-10 | **P0 安全加固（成员3）**：`POST /secondhand/orders` 请求体改为 `{item_id, remark}`，卖家/金额由服务端反查、响应新增 `amount`（SEC-05），下单原子抢占防超卖（TXN-02，冲突返回 `3001`）；聊天会话读写新增归属校验（SEC-03/04，越权返回 `1001`）；账号禁用即时生效（SEC-06，返回 `2003`）；生产环境强制校验 `XJT_JWT_SECRET` 与微信配置（SEC-01/02） |
| v1.5 | 2026-09-10 | B6 内容审核闭环（成员2）：词库 `audit_word` + 审核留痕 `audit_log`（C7 表设计，`db/sql/11_audit.sql`）；`services/audit.py` 三级判定（pass/review/block，规则 + 模型二次判定）；发帖·评论·二手发布接入审核；拒绝错误码 **3003**；新增 `GET /topics/{id}/audit-status` 轮询与 `POST /admin/forum/audit/batch` 批量审核 |
| v1.6 | 2026-09-10 | B8 可观测性：新增 `GET /health/detail`（Ollama·模型·向量库·知识库·检索模式与降级原因）与 `GET /health/selfcheck`（embed/检索/生成一键自检）；`/health` 保持兼容不变 |
| v1.8 | 2026-09-11 | B9 二手 AI：新增 `GET /secondhand/items/{id}`（含 images）与 `POST /secondhand/items/ai-price` 纯定价接口；`ai-describe` 升级为「模型文案 + 库内同类均价定价」（响应含 sample_count/avg_price/reason，模型不可用时统计兜底 + 价格护栏）；修复发布丢失 `images`/`condition_level` 的落库缺陷 |
| v1.9 | 2026-09-12 | B10 通知精准推荐与未读：`notice-feed` 升级为兴趣标签 + 行为偏好 + 年级/校区 + 时效衰减打分（逐条带 `score/reason/matched_tags`）；新增 `GET /life/notices/unread-count`、`GET /life/notices/unread`、`POST /life/notices/read-batch`；投递记录 `notice_delivery` 懒生成 + 曝光回执 |
| v1.10 | 2026-09-12 | B11 推荐数据消费：`GET /topics/feed` 由纯时间序升级为混合打分（兴趣标签 + 行为偏好 + 热度 + 时效），逐条返回 `score/reason/matched_tags`；冷启动回落热度榜（`services/recommend.py`） |
| v1.11 | 2026-09-12 | B13 路网导航：`POST /map/navigate` 由两点直线升级为网格 A* 路网寻路（`services/route.py`，30m 网格 + 建筑 45m 缓冲障碍 + 共线压缩）；响应新增 `straight_distance`（绕行对比）/`algorithm`/`start_source`（起点来源），起点缺省改为距目标最近的 POI |
| v1.12 | 2026-09-12 | B14 上传加固与存储抽象：新增 `core/url_sign.py`（HMAC 签名 + 过期）与 `services/storage.py`（本地 / S3 / OSS 可切换）；`POST /upload/image` 返回签名访问 URL，`/static/uploads/*` 校验签名（无签名/过期返回 403）；配合既有魔数白名单与安全响应头构成完整上传安全基线 |
| v1.7 | 2026-09-11 | B7 Agent 三级链路：模型 Function Call → **规则执行器**（`services/rule_executor.py`，模型不可用时真写库）→ `status=3` 明确失败；移除"未执行工具却报成功"的假成功路径；相对时间换算改为基准日期注入（修复"明天"日期偏移） |
