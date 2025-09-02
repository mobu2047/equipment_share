# Equipment Share API 文档 (MVP)

> 说明：所有接口均已在 `backend/api.py` 的 `api_router` 下聚合注册。服务默认地址 `http://127.0.0.1:8000`。

- 鉴权：JWT 短期 `access_token`（放 Header），长期 `refresh_token/session_id`（HttpOnly Cookie）
- 角色：TENANT/PROVIDER/ADMIN
- 设备数据：RAG + FAISS（JSON 存储）

---

## 鉴权 Auth (/api/v1/auth)

### 1) 发送验证码
POST /api/v1/auth/sms/send

Body (json):
`{ "phone":"13800000000", "scene":"login" }`

Response (dev):
`{ "dev": true }`

### 2) 验证并登录
POST /api/v1/auth/verify

Body:
`{ "phone":"13800000000", "code":"000000" }`

Response:
`{ "access_token": "..." }`

说明：成功后会设置 Cookie: `refresh_token`, `session_id`。

Curl:
```
curl -X POST http://127.0.0.1:8000/api/v1/auth/verify \
  -H "Content-Type: application/json" \
  -c cookie.txt \
  -d '{"phone":"000000","code":"000000"}'
```

### 3) 刷新 Access Token（基于 Cookie）
POST /api/v1/auth/refresh

Headers: 带上 `-b cookie.txt`

Response:
`{ "access_token": "..." }`

Curl:
```
curl -X POST http://127.0.0.1:8000/api/v1/auth/refresh -b cookie.txt
```

### 4) 登出
POST /api/v1/auth/logout

效果：吊销当前刷新会话，并清空 Cookie。

### 5) 获取当前用户
GET /api/v1/auth/me

Headers:
`Authorization: Bearer <access_token>`

Response:
`{ "id": 1, "phone": "...", "display_name": null, "roles": ["TENANT"] }`

---

## 租赁 Lease (/api/v1/lease)

Headers（除列表/详情外均需登录）：
`Authorization: Bearer <access_token>`

### 1) 创建租赁请求
POST /api/v1/lease/requests

Body:
```
{
  "device_id": "rag_item_id?",
  "device_name": "快照?",
  "requested_start_at": "2025-09-02T10:00:00",
  "requested_end_at": "2025-09-02T12:00:00",
  "remark": "说明可空"
}
```

### 2) 列表
GET /api/v1/lease/requests?status=pending&page=1&size=20

返回根据用户角色自动过滤后的列表。

### 3) 详情
GET /api/v1/lease/requests/{id}

### 4) 确认（ADMIN/PROVIDER）
POST /api/v1/lease/requests/{id}/confirm

Body:
```
{
  "device_id": "some",
  "confirmed_start_at": "2025-09-02T10:00:00",
  "confirmed_end_at": "2025-09-02T11:00:00",
  "provider_user_id": 2
}
```

### 5) 拒绝（ADMIN/PROVIDER）
POST /api/v1/lease/requests/{id}/reject

Body: `{ "reason": "not available" }`

### 6) 取消（TENANT 自己或 ADMIN；仅 pending）
POST /api/v1/lease/requests/{id}/cancel

Body: `{ "reason": "user cancel" }`

### 7) 归档（ADMIN；仅 confirmed）
POST /api/v1/lease/requests/{id}/archive

### 8) 删除（ADMIN；轻量软删）
DELETE /api/v1/lease/requests/{id}

备注：pending/rejected -> 置为 cancelled；其余状态不删除。

Curl 示例（创建 -> 列表）：
```
ACCESS=... # 替换为登录返回的 access_token
curl -X POST http://127.0.0.1:8000/api/v1/lease/requests \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"device_id":"d1","requested_start_at":"2025-09-02T10:00:00"}'

curl "http://127.0.0.1:8000/api/v1/lease/requests?page=1&size=20" \
  -H "Authorization: Bearer $ACCESS"
```

---

## 论坛 Forum (/api/v1/forum)

Headers（发帖/回帖/上传/删除需登录）：
`Authorization: Bearer <access_token>`

### 1) 发帖
POST /api/v1/forum/threads

Body:
```
{
  "title": "标题",
  "content": "正文",
  "category": "general",
  "lease_order_id": 1,
  "attachments": ["/static/uploads/forum/xxxx/abc.jpg"]
}
```

返回：`{ "id": 123 }`

### 2) 列表（公开）
GET /api/v1/forum/threads?category=general&lease_order_id=1&page=1&size=20

### 3) 详情（公开）
GET /api/v1/forum/threads/{thread_id}

返回示例：
```
{
  "id": 1,
  "title": "...",
  "author_user_id": 3,
  "category": "general",
  "lease_order_id": 1,
  "attachments": ["/static/..."],
  "created_at": "...",
  "posts": [
    {
      "id": 10,
      "author_user_id": 3,
      "content": "回复内容",
      "attachments": ["/static/..."],
      "parent_post_id": null,
      "created_at": "..."
    }
  ]
}
```

### 4) 回帖
POST /api/v1/forum/threads/{thread_id}/posts

Body:
```
{
  "content": "回复内容",
  "parent_post_id": null,
  "attachments": ["/static/.../pic.png"]
}
```

### 5) 上传图片
POST /api/v1/forum/uploads

Form-Data:
- file: <binary>

限制：类型（jpeg/png/webp/gif）、大小 ≤ 配置 `forum_upload_max_mb`（默认 10MB）。

响应：`{ "url": "/static/uploads/forum/<随机>/<文件名>" }`

### 6) 删除主题（作者或 ADMIN）
DELETE /api/v1/forum/threads/{thread_id}

### 7) 删除回复（作者或 ADMIN）
DELETE /api/v1/forum/posts/{post_id}

Curl（发帖 -> 查看 -> 回帖）
```
ACCESS=...
# 发帖（无图）
curl -X POST http://127.0.0.1:8000/api/v1/forum/threads \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"title":"t","content":"c","category":"general"}'

# 查看
curl http://127.0.0.1:8000/api/v1/forum/threads/1

# 回帖
curl -X POST http://127.0.0.1:8000/api/v1/forum/threads/1/posts \
  -H "Authorization: Bearer $ACCESS" -H "Content-Type: application/json" \
  -d '{"content":"hi"}'
```

---

## RAG (/api/rag)

- POST `/upsert` (multipart) 新增单条（含图片）
- POST `/upsert_full` (json) 新增/更新
- GET `/item/{id}` 详情
- PUT `/item/{id}` 更新
- GET `/items` 列表（分页/过滤）
- GET `/search` 结构化搜索（向量）
- POST `/ask` RAG 问答
- POST `/reindex` 重建索引
- GET `/list` 简表
- DELETE `/delete/{id}` 删除
- POST `/check_duplicate` 按关键字段查重
- POST `/import_excel_async` 上传 Excel 并导入
- POST `/clear_all` 清空数据与索引（可清理导入图片）
- POST `/search_by_meta` 名称+metadata 高级搜索

Curl（示例）
```
# upsert_full
curl -X POST http://127.0.0.1:8000/api/rag/upsert_full \
  -H "Content-Type: application/json" \
  -d '{"name":"SEM","description":"电镜"}'

# ask
curl -X POST "http://127.0.0.1:8000/api/rag/ask?query=我需要显微镜&top_k=3"
```

---

## 设备推荐 (/api)
- POST `/recommend` -> RecommendResponse（Top-3）

Curl：
```
curl -X POST http://127.0.0.1:8000/api/recommend \
  -H "Content-Type: application/json" \
  -d '{"experiment":"扫描电镜观察","user_location":{"lat":28.2,"lng":112.9}}'
```

---

## Chat (/api)
- POST `/chat` -> ChatResponse

Curl：
```
curl -X POST http://127.0.0.1:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"prompt":"你好"}'
```

---

## 认证与调用说明
- 登录获取 `access_token`，前端/脚本请求时放置 `Authorization: Bearer <access_token>`
- 401 时先调用 `/api/v1/auth/refresh`（需携带 Cookie），再重试原请求
- ADMIN 默认手机号在配置 `admin_default_phone`（默认 `000000`，登录即授予），也可通过管理接口授予角色（后续可扩展）
