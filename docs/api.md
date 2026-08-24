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
| 3001~3099 | 业务冲突 | 3001 座位已被预约 / 3002 重复投递 |
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
| 二手 | items(列表/发布/改状态/ai-describe) / wishes(创建/匹配) / orders(创建) |
| 兼职 | jobs(列表/详情/投递/可信度) / applications/me |
| 论坛 | topics(列表/创建/详情/点赞/举报/hot/feed) / comments |
| 地图 | pois / nearby / navigate / building/{id} |
| 生活 | merchants / menu / orders / notices / notice-read / notice-feed |
| 管理 | metrics / knowledge/ingest / forum/audit / train/corpus |

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
{ "instruction": "明天下午3点帮我预约图书馆二楼靠窗的座位，同时提醒我下午4点的选修课" }
```
响应 `data`：
```json
{
  "task_id": 7, "status": 0,
  "plan": [
    {"tool":"reserve_seat","desc":"预约座位"},
    {"tool":"add_reminder","desc":"设置课程提醒"}
  ]
}
```
> 后端：意图解析→Agent 编排→入 `agent_task`，异步执行（Celery）。

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

### POST /secondhand/items — 发布闲置
请求：
```json
{ "title":"九成新高数教材", "description":"微积分上册，无笔记", "category":"教材",
  "price":25.00, "condition_level":9, "images":["https://..."] }
```
响应：`{ "item_id": 3 }`

### POST /secondhand/items/ai-describe — AI 辅助发布（图像→描述/定价）
请求 `{ "image_url":"https://...", "user_note":"旧教材" }`
响应 `data`：`{ "category":"教材","title":"高数教材（微积分上册）","suggested_price":25.00,"description":"..." }`

### PUT /secondhand/items/{id}/status — 改状态
请求 `{ "status": "1" }`（0在售 1已售 2下架）

### POST /secondhand/wishes — 发布求购
请求 `{ "content":"求购高数教材","category":"教材","budget":30 }` → `{ "wish_id": 2 }`

### GET /secondhand/wishes/{id}/match — AI 供需匹配
响应 `data.items[]`：`{ "id":3,"title":"高数教材","price":25,"seller_name":"..." }`

### POST /secondhand/orders — 创建订单
请求 `{ "item_id":3, "seller_id":1, "amount":25 }` → `{ "order_id": 8 }`
> 建议事务：下单 + 物品标记已售。

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
请求 `{ "title":"求高数资料","content":"...","category":"学习" }` → `{ "topic_id": 5 }`
> 新帖 `audit_status=0`，通过 AI 审核后对外可见。

### GET /topics/{id} — 详情（含评论）
响应 `data`：
```json
{ "id":5, "title":"...", "content":"...", "author_name":"...", "liked":false,
  "comments":[{"id":1,"author_name":"...","content":"...","created_at":"..."}] }
```

### POST /topics/{id}/like — 点赞/取消
响应：`{ "topic_id":5, "liked": true, "like_count": 13 }`

### POST /topics/{id}/comments — 评论
请求 `{ "content":"同求" }` → `{ "comment_id": 3 }`

### POST /topics/{id}/report — 举报
请求 `{ "reason":"广告" }` → `{ "report_id": 1 }`

### GET /topics/hot — 热点
响应 `data.items[]`：`{ "id":1,"title":"...","is_hot":1 }`（对应 `ForumDAO.hot_topics`）

### GET /topics/feed — 个性化推荐（AI）
查询参数：`?page=&size=`，按用户标签/浏览历史排序（后端实现推荐逻辑）。

---

## 9. 地图

### GET /map/pois — POI 列表
查询参数：`?category=教学楼`
响应 `data.items[]`：`{ "id":1,"name":"中心图书馆","category":"图书馆","latitude":43.88,"longitude":125.32 }`

### GET /map/nearby — 周边服务（定位联动）
查询参数：`?lat=43.88&lng=125.32&radius=500`
响应 `data.items[]`：POI + `distance`（米）。

### POST /map/navigate — 路线规划
请求 `{ "from": {"lat":..,"lng":..}, "to_poi_id": 1 }`
响应 `data`：`{ "distance":800,"duration":10,"path":[{lat,lng}...] }`

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

### GET /life/notice-feed — AI 精准通知推送
查询参数：`?page=&size=`，按用户年级/标签过滤排序（后端实现）。

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

### GET /admin/train/corpus — 训练语料
查询参数：`?source_type=forum&is_cleaned=0&page=&size=`

---

## 12. 附：实现注意事项（前后端）

1. **SSE 解析**：前端用 `wx.request` 无法流式，改用 `wx.request` 长连接 + 后端 `StreamingResponse`，或小程序 `EventSource` 适配（微信需 `enableChunked`）。
2. **token 失效**：接口返回 `2001/2002` 时前端统一跳登录。
3. **图片上传**：预留 `POST /upload/image`（multipart）→ `image_asset` 表。
4. **日期时区**：后端统一用服务器本地时间（`Asia/Shanghai`）。
5. **接口与 DAO 对应**：每个接口标了对应 C++ DAO，实现时直接调 `jt_db.XXXDAO()`。

## 13. 变更记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-08-24 | 初始契约：11 模块，约 60 个接口 |
| v1.1 | 2026-08-24 | RAG：ingest 响应加 `status`；新增 `POST /admin/knowledge/index` 重建索引 |
