# 云端素材库（A4）管理后台接口

## 1. 通用约定

接口前缀：`/api/v1`。需携带 `Authorization: Bearer <access_token>`。

| 接口类别 | 权限码 |
| --- | --- |
| 读（`GET`） | `platform.material.read`（`platform.material.write` 亦可） |
| 写（`POST` / `PATCH` / `DELETE`） | `platform.material.write` |

- 文件落盘：`{upload_dir}/admin/<uuid>.webp` 与原图缩略图 `{upload_dir}/admin/<uuid>-thumb.webp`。
- 数据库只存**相对路径**（`/storage/uploads/admin/...`）；所有返回的 `url` / `thumbnail_url` 均为按当前管理员
  现签的临时地址（`kind=admin`，有效期取 `MEDIA_URL_TTL_SECONDS`），过期后需重新调用列表接口获取。
- 审计：`material_category.create/update/delete`、`material.create/update/delete`。
- 分页壳：`{ items, page, page_size, total, has_more }`。

---

## 2. 素材分类

### 2.1 `GET /api/v1/admin/materials/categories`

- 用途：分页查询素材分类，附带每个分类的有效素材数。
- 权限：`platform.material.read`。成功状态：`200 OK`。

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | 1–10000 | 页码 |
| `page_size` | query | int | 否 | 100 | 1–200 | 每页条数 |
| `keyword` | query | string | 否 | 无 | ≤64 | 分类名模糊匹配 |
| `parent_id` | query | int | 否 | 无 | ≥1 | 只看某父分类下的子分类 |

返回字段：

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `id` | int | 是 | — | 分类 ID |
| `parent_id` | int/null | 是 | 一级分类 | 父分类 |
| `name` | string | 是 | — | 分类名 |
| `sort` | int | 是 | — | 排序，越小越靠前 |
| `enabled` | bool | 是 | — | 是否启用 |
| `material_count` | int | 是 | — | 分类下有效素材数（软删除不计） |
| `created_at` | datetime/null | 是 | — | 创建时间 |

请求示例：

```http
GET /api/v1/admin/materials/categories?page=1&page_size=100
Authorization: Bearer <access_token>
```

返回示例：

```json
{"items": [{"id": 1, "parent_id": null, "name": "活动物料", "sort": 0,
            "enabled": true, "material_count": 12, "created_at": "2026-10-20T10:00:00"}],
 "page": 1, "page_size": 100, "total": 1, "has_more": false}
```

空数据：`{"items": [], "page": 1, "page_size": 100, "total": 0, "has_more": false}`

### 2.2 `POST /api/v1/admin/materials/categories`

- 权限：`platform.material.write`。成功状态：`201 Created`。

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `name` | body | string | 是 | — | 1–64 字 | 分类名 |
| `parent_id` | body | int/null | 否 | null | ≥1 | 父分类 |
| `sort` | body | int | 否 | 0 | 0–100000 | 排序 |
| `enabled` | body | bool | 否 | true | — | 是否启用 |

```http
POST /api/v1/admin/materials/categories
Content-Type: application/json

{"name": "活动物料", "sort": 0, "enabled": true}
```

非法示例：`{"name": ""}` → `422`。

返回：`MaterialCategoryItem`（同 2.1 行结构）。

### 2.3 `PATCH /api/v1/admin/materials/categories/{category_id}`

- 权限：`platform.material.write`。成功状态：`200 OK`。
- 全部字段可选，但**至少提供一个**（否则 422：`至少提供一个需要修改的字段`）。
- `parent_id` 不能等于自身（422：`父分类不能是自身`）。
- 返回：`MaterialCategoryItem`。

非法示例：`{}` → `422`。

### 2.4 `DELETE /api/v1/admin/materials/categories/{category_id}`

- 权限：`platform.material.write`。成功状态：`204 No Content`（无返回体）。
- 约束：分类下仍有有效素材 → `409 分类下还有素材，不能删除`；存在下级分类 → `409 存在下级分类，不能删除`。

---

## 3. 素材

### 3.0 将私有素材复制为长期配置图片

素材列表中的 url 是短时签名地址，不能直接写入长期平台配置。选择素材时调用本接口，将已有 WebP 图片复制到公开的 common/ 图片目录；原素材不变，公共副本供平台配置长期引用。

- **基本信息**：POST /api/v1/admin/materials/{material_id}/copy-to-common；需登录且具备 platform.material.write；无请求体；成功返回 201 Created。
- **请求参数**：material_id（path，正整数，必填，≥1，素材 ID；示例 42，非法示例 0）。Header：Authorization: Bearer access_token。
- **请求示例**：POST /api/v1/admin/materials/42/copy-to-common，携带 Authorization Header；无请求体。
- **返回参数**：material_id（integer，必返，源素材 ID）；url（string，必返，长期公共图片路径，例如 /storage/uploads/common/<uuid>.webp）。
- **返回示例**：{"material_id":42,"url":"/storage/uploads/common/9b12d60dcae444488ebac4dfd1bc88f5.webp"}。
- **使用方法与业务规则**：先从素材列表选择未删除素材，再调用本接口；复制操作需要素材写权限，源素材仍保留，复制产生的公共副本不会随素材软删除而删除。仅允许复制素材库 admin/ 目录下的 WebP 文件；接口记录 material.copy_to_common 审计事件。该接口不修改配置；调用方仍需通过对应平台配置接口保存返回的 url。
- **错误**：401 未登录；403 缺少写权限；404 素材或文件不存在；422 ID 或素材路径非法；500 复制或审计失败。调用方应提示错误并保持原配置不变。
- **兼容性**：新增端点，不改变现有素材列表/上传/编辑/删除响应。

### 3.1 `GET /api/v1/admin/materials`

- 用途：分页查询素材（软删除不计）。
- 权限：`platform.material.read`。成功状态：`200 OK`。

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `page` | query | int | 否 | 1 | 1–10000 | 页码 |
| `page_size` | query | int | 否 | 20 | 1–100 | 每页条数 |
| `keyword` | query | string | 否 | 无 | ≤64 | 素材名模糊匹配 |
| `category_id` | query | int | 否 | 无 | ≥1 | 按分类筛选 |

返回字段：

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `id` | int | 是 | — | 素材 ID |
| `category_id` | int | 是 | — | 所属分类 |
| `category_name` | string/null | 是 | 分类已删 | 分类名（冗余展示） |
| `name` | string | 是 | — | 素材名 |
| `url` | string | 是 | — | 原图**现签**地址 |
| `thumbnail_url` | string | 是 | — | 缩略图**现签**地址 |
| `mime_type` | string | 是 | — | 固定 `image/webp` |
| `size` | int | 是 | — | 原图字节数 |
| `width` / `height` | int/null | 是 | 当前未解析 | 图片宽高 |
| `uploaded_by` | int/null | 是 | — | 上传者管理员 ID |
| `created_at` / `updated_at` | datetime/null | 是 | — | 时间戳 |

请求示例：

```http
GET /api/v1/admin/materials?page=1&page_size=20&category_id=1&keyword=海报
Authorization: Bearer <access_token>
```

返回示例：

```json
{"items": [{"id": 9, "category_id": 1, "category_name": "活动物料", "name": "双十一海报",
            "url": "/storage/uploads/admin/9f1c.webp?expires=1768&kind=admin&user=3&revision=0&signature=ab",
            "thumbnail_url": "/storage/uploads/admin/9f1c-thumb.webp?expires=1768&kind=admin&user=3&revision=0&signature=cd",
            "mime_type": "image/webp", "size": 48213, "width": null, "height": null,
            "uploaded_by": 3, "created_at": "2026-10-20T10:30:00", "updated_at": "2026-10-20T10:30:00"}],
 "page": 1, "page_size": 20, "total": 1, "has_more": false}
```

### 3.2 `POST /api/v1/admin/materials`

- 用途：上传图片素材（multipart）。
- 权限：`platform.material.write`。成功状态：`201 Created`。Content-Type：`multipart/form-data`。

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `category_id` | query | int | 是 | — | ≥1 | 目标分类 |
| `file` | form | file | 是 | — | JPG/PNG，≤5MB，像素 ≤2500 万 | 图片文件 |
| `name` | query | string | 否 | 自动生成 | ≤128 | 素材名；不传按 `素材-<uuid 前缀>` 生成 |

```http
POST /api/v1/admin/materials?category_id=1&name=双十一海报
Authorization: Bearer <access_token>
Content-Type: multipart/form-data; boundary=...

--...
Content-Disposition: form-data; name="file"; filename="poster.png"
Content-Type: image/png

<binary>
--...--
```

非法示例（上传 PDF，返回 415）：`--... filename="a.pdf" Content-Type: application/pdf ...`

返回字段：

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `id` | int | 是 | 素材 ID |
| `name` | string | 是 | 素材名 |
| `url` | string | 是 | 原图现签地址 |
| `thumbnail_url` | string | 是 | 缩略图现签地址 |
| `mime_type` | string | 是 | 固定 `image/webp` |
| `size` | int | 是 | 原图字节数 |

返回示例：

```json
{"id": 10, "name": "双十一海报",
 "url": "/storage/uploads/admin/2a7b.webp?expires=1768&kind=admin&user=3&revision=0&signature=ef",
 "thumbnail_url": "/storage/uploads/admin/2a7b-thumb.webp?expires=1768&kind=admin&user=3&revision=0&signature=12",
 "mime_type": "image/webp", "size": 51200}
```

业务规则：

- 上传顺序为「读限长 → 转码 WebP + 缩略图 → 落盘 → 登记行 → 审计」，任一步失败都不会留下数据库半行。
- 非 JPG/PNG → `415 仅支持JPG、JPEG或PNG图片`；超过 5MB → `413 文件大小不能超过5MB`。
- 像素超过 2500 万 → `413 图片像素不能超过2500万`。

### 3.3 `PATCH /api/v1/admin/materials/{material_id}`

- 用途：改名 / 移动分类。权限：`platform.material.write`。成功状态：`200 OK`。

| 参数 | 位置 | 类型 | 必填 | 默认值 | 校验 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `material_id` | path | int | 是 | — | ≥1 | 素材 ID |
| `name` | body | string | 否 | 无 | 1–128 | 新名称 |
| `category_id` | body | int | 否 | 无 | ≥1，目标分类必须启用 | 新分类 |

```http
PATCH /api/v1/admin/materials/10
Content-Type: application/json

{ "name": "双十一主视觉", "category_id": 2 }
```

非法示例：`{}` → `422`；`{"category_id": 999}` → `422 目标分类不存在或已停用`。

返回：`MaterialItem`（同 3.1 行结构，`url` 为现签地址）。

### 3.4 `DELETE /api/v1/admin/materials/{material_id}`

- 权限：`platform.material.write`。成功状态：`204 No Content`（无返回体）。
- 软删除（写 `deleted_at`），磁盘文件保留。

---

## 4. 错误码汇总

| 状态码 | 触发条件 | 前端处理 |
| --- | --- | --- |
| 401 | 未登录 | 跳转登录 |
| 403 | 缺 `platform.material.*` | 隐藏入口 |
| 404 | 素材/分类不存在 | 刷新列表 |
| 409 | 分类下仍有素材 / 有下级分类 | 先清空再删 |
| 413 | 文件超 5MB / 像素过大 | 压缩后重传 |
| 415 | 非 JPG/PNG | 转换格式 |
| 422 | 参数或表单校验失败 | 修正表单 |

## 5. 变更记录

| 日期 | 变更 |
| --- | --- |
| 2026-10-20 | 新增素材库分类与素材 CRUD 接口（A4） |
| 2026-11-07 | TASK-PLATFORM-MATERIAL-2026-11-07-01：新增短时签名素材复制为长期配置图片的接口 |

## 6. 文档完成自检清单

- [x] 请求参数表含位置/类型/必填/默认值/校验/含义
- [x] 有完整请求示例与至少一个非法示例
- [x] 返回参数表覆盖全部字段并说明空值
- [x] 分页接口给出分页字段含义与空数据示例
- [x] 业务规则覆盖前置条件、上传顺序、文件限制
- [x] 错误码表完整
