# 短视频 接口契约（M7-C）

> **模块范围**：管理后台「短视频」菜单下 6 个页面
>
> - 短视频参数配置 `/short-video-config`
> - 视频管理（刷粉/审核）`/short-video-list`
> - 红包记录 `/short-video-red-packet`
> - 评论审核 `/short-video-comment`
> - 会员主页 `/short-video-homepage`
> - 打赏管理 `/short-video-tip`
>
> **后端文件**：
> 1. 视频 / 分类 / 评论 / 打赏 / 红包 / 主页：`app/api/routes/short_video_admin.py`
> 2. 配置域 `tools_short_video` 走 `/api/v1/admin/configs`

---

## 一、通用约定

### 1.1 鉴权

- Token 类型：红娘后台独立 Token
- 401 / 403 同其它模块

### 1.2 权限点

| 路径前缀 | 读取 | 写入 |
|---|---|---|
| `/admin/short-videos/*` | `community.video.read` | `community.video.manage` |
| `/admin/short-video-categories/*` | `community.video.read` | `community.video.manage` |
| `/admin/short-video-comments/*` | `community.video.read` | `community.video.manage` |
| `/admin/short-video-tips/*` | `community.video.read` | `community.video.manage` |
| `/admin/video-red-packets/*` | `community.video.read` | `community.video.manage` |
| `/admin/short-video-homepages/*` | `community.video.read` | `community.video.manage` |
| 配置域 `tools_short_video` | `platform.config.read` | `platform.config.write` |

### 1.3 路由顺序铁律

`/brush`（POST 数据刷粉）必须声明在 `PATCH /{video_id}` 之前，否则被吞。

---

## 二、视频分类（`/api/v1/admin/short-video-categories`）

### 2.1 列表 `GET /admin/short-video-categories`

```json
[{ "id": 1, "name": "相亲日常", "sort": 100, "status": 1, "created_at": "..." }]
```

### 2.2 新增 `POST /admin/short-video-categories`
### 2.3 修改 `PATCH /admin/short-video-categories/{category_id}`
### 2.4 删除 `DELETE /admin/short-video-categories/{category_id}` → 204

---

## 三、视频管理（`/api/v1/admin/short-videos`）

### 3.1 列表 `GET /admin/short-videos`

参数：`category_id` / `keyword`（标题/简介） / `audit_status`（pending/approved/rejected） / `online` / `user_id` / `start_date` / `end_date` / `min_views` / `page` / `page_size`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | |
| `user_id`, `user_nickname`, `user_avatar` | | 创作者 |
| `category_id`, `category_name` | | |
| `title`, `description`, `cover`, `video_url` | string | |
| `duration` | int | 秒 |
| `size_kb` | int | |
| `width`, `height` | int | |
| `audit_status` | enum | pending/approved/rejected |
| `online` | bool | |
| `top`, `featured` | bool | 运营标记 |
| `view_count`, `like_count`, `comment_count`, `share_count`, `tip_total` | int | 实时计数 |
| `red_packet_id` | int | 关联红包 |
| `created_at`, `published_at` | | |
| `last_brushed_at` | | 上次刷粉时间 |

### 3.2 新增 `POST /admin/short-videos`

```json
{
  "user_id": 100, "category_id": 1,
  "title": "...", "description": "...",
  "cover": "https://...", "video_url": "https://...",
  "duration": 35, "size_kb": 4096,
  "width": 720, "height": 1280,
  "top": false, "featured": false
}
```

### 3.3 数据刷粉 `POST /admin/short-videos/brush`

```json
{
  "video_ids": [1, 2, 3],
  "view_min": 100, "view_max": 500,
  "like_min": 50, "like_max": 200,
  "share_min": 10, "share_max": 50,
  "refresh_published_at": true
}
```

响应 `VideoBrushResult`：

```json
{
  "affected": 3,
  "view_added": 380, "like_added": 120, "share_added": 24,
  "refreshed_published": 1
}
```

### 3.4 修改 / 审核 `PATCH /admin/short-videos/{video_id}`

```json
{
  "title": "...", "description": "...",
  "audit_status": "approved", "online": true,
  "top": true, "featured": false,
  "reject_reason": "内容不符"
}
```

### 3.5 删除 `DELETE /admin/short-videos/{video_id}` → 204
级联软删评论、打赏、关联红包。

### 3.6 详情 `GET /admin/short-videos/{video_id}`

---

## 四、视频评论（`/api/v1/admin/short-video-comments`）

### 4.1 列表 `GET /admin/short-video-comments`

参数：`video_id` / `user_id` / `audit_status` / `top` / `page` / `page_size`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id`, `video_id`, `video_title` | | |
| `user_id`, `user_nickname` | | |
| `content` | string | 评论文本 |
| `parent_id` | int | 父评论 |
| `like_count` | int | |
| `top` | bool | 置顶 |
| `audit_status` | enum | pending/approved/rejected |
| `created_at` | | |

### 4.2 批量删除 `POST /admin/short-video-comments/batch-delete`
请求体：`{ "ids": [1, 2, 3] }` → 204

### 4.3 修改 / 审核 `PATCH /admin/short-video-comments/{comment_id}`
`{ "audit_status": "approved", "top": true, "reject_reason": "..." }`

### 4.4 删除 `DELETE /admin/short-video-comments/{comment_id}` → 204

---

## 五、打赏管理（`/api/v1/admin/short-video-tips`）

### 5.1 列表 `GET /admin/short-video-tips`

参数：`video_id` / `taker_user_id` / `payer_user_id` / `min_amount` / `start_date` / `end_date` / `page` / `page_size`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id`, `video_id`, `video_title` | | |
| `payer_user_id`, `payer_nickname` | | 打赏人 |
| `taker_user_id`, `taker_nickname` | | 收赏人 |
| `amount` | decimal string | |
| `gift_name` | string | |
| `gift_count` | int | |
| `message` | string | |
| `paid_at` | datetime | |

---

## 六、红包记录（`/api/v1/admin/video-red-packets`）

### 6.1 列表 `GET /admin/video-red-packets`

参数：`video_id` / `sender_user_id` / `status`（0待领/1已抢完/2已过期/3已退款） / `page` / `page_size`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id`, `video_id`, `video_title` | | |
| `sender_user_id`, `sender_nickname` | | 发红包者 |
| `total_amount` | decimal string | 总金额 |
| `total_count` | int | 红包个数 |
| `claimed_count` | int | 已领取 |
| `remaining_amount` | decimal string | 余金额 |
| `cover_text` | string | 红包封面文案 |
| `countdown_seconds` | int | 倒计时秒数 |
| `status` | enum | pending/done/expired/refunded |
| `effective_from`, `expire_at` | | |
| `created_at` | | |

### 6.2 领取明细 `GET /admin/video-red-packets/{packet_id}/claims`

```json
[{
  "id": 1, "packet_id": 10, "user_id": 200, "nickname": "...",
  "amount": "2.66", "claimed_at": "..."
}]
```

---

## 七、会员主页（`/api/v1/admin/short-video-homepages`）

### 7.1 列表 `GET /admin/short-video-homepages`

参数：`keyword` / `real_name_verified` / `identity_verified` / `page` / `page_size`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id`, `user_id`, `user_nickname`, `user_avatar` | | |
| `gender`, `age`, `city` | | |
| `bio` | string | 个人简介 |
| `video_count`, `follower_count`, `following_count`, `like_received` | int | |
| `real_name_verified`, `identity_verified`, `marital_status` | | 认证 |
| `identity_tier` | string | 实名/认证/婚信认证 |
| `feature_video_id` | int | 置顶视频 |
| `created_at`, `updated_at` | | |

### 7.2 修改 `PATCH /admin/short-video-homepages/{homepage_id}`
`{ "real_name_verified": true, "identity_verified": true, "feature_video_id": 99, "bio": "..." }`

### 7.3 删除 `DELETE /admin/short-video-homepages/{homepage_id}` → 204

---

## 八、配置域 `tools_short_video`

```json
{
  "normal_post_review": true,
  "white_user_ids": [101, 102],
  "hot_threshold_view": 10000,
  "hot_threshold_like": 500,
  "tip_min": 1,
  "tip_max": 999,
  "tip_gifts": [
    { "name": "小心心", "icon": "https://...", "amount": 1 },
    { "name": "大啤酒", "icon": "...", "amount": 18 }
  ],
  "red_packet_countdown": 10,
  "red_packet_min": 1,
  "red_packet_max": 200,
  "publish_agreement": "...",
  "categories": [
    { "name": "相亲日常", "sort": 100, "icon": "..." }
  ],
  "auto_brushed_max_per_day": 3
}
```

调用：`GET /api/v1/admin/configs/tools_short_video` / `PUT`。

---

## 九、数据库变更

### 9.1 新建表

#### `short_video_category`
```sql
CREATE TABLE `short_video_category` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL,
  `sort` int NOT NULL DEFAULT 100,
  `status` tinyint NOT NULL DEFAULT 1,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_svc_status_sort` (`status`, `sort`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频分类'
```

#### `short_video`
```sql
CREATE TABLE `short_video` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `user_id` bigint unsigned NOT NULL,
  `category_id` bigint unsigned DEFAULT NULL,
  `title` varchar(255) NOT NULL,
  `description` text,
  `cover` varchar(255) DEFAULT NULL,
  `video_url` varchar(255) NOT NULL,
  `duration` int NOT NULL DEFAULT 0,
  `size_kb` int NOT NULL DEFAULT 0,
  `width` int NOT NULL DEFAULT 0,
  `height` int NOT NULL DEFAULT 0,
  `audit_status` varchar(16) NOT NULL DEFAULT 'pending',
  `online` tinyint NOT NULL DEFAULT 1,
  `top` tinyint NOT NULL DEFAULT 0,
  `featured` tinyint NOT NULL DEFAULT 0,
  `view_count` int NOT NULL DEFAULT 0,
  `like_count` int NOT NULL DEFAULT 0,
  `comment_count` int NOT NULL DEFAULT 0,
  `share_count` int NOT NULL DEFAULT 0,
  `tip_total` decimal(12,2) NOT NULL DEFAULT 0,
  `red_packet_id` bigint unsigned DEFAULT NULL,
  `published_at` datetime DEFAULT NULL,
  `last_brushed_at` datetime DEFAULT NULL,
  `created_by` int unsigned DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_sv_user` (`user_id`, `created_at`),
  KEY `idx_sv_cat` (`category_id`, `audit_status`, `online`),
  KEY `idx_sv_top_hot` (`top`, `view_count`),
  KEY `idx_sv_audit` (`audit_status`, `online`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频'
```

#### `short_video_comment`
```sql
CREATE TABLE `short_video_comment` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `video_id` bigint unsigned NOT NULL,
  `user_id` bigint unsigned NOT NULL,
  `parent_id` bigint unsigned DEFAULT NULL,
  `content` varchar(1000) NOT NULL,
  `like_count` int NOT NULL DEFAULT 0,
  `top` tinyint NOT NULL DEFAULT 0,
  `audit_status` varchar(16) NOT NULL DEFAULT 'approved',
  `reject_reason` varchar(255) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_svc_video` (`video_id`, `created_at`),
  KEY `idx_svc_user` (`user_id`, `created_at`),
  KEY `idx_svc_audit` (`audit_status`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频评论'
```

#### `short_video_tip`
```sql
CREATE TABLE `short_video_tip` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `video_id` bigint unsigned NOT NULL,
  `payer_user_id` bigint unsigned NOT NULL,
  `taker_user_id` bigint unsigned NOT NULL,
  `amount` decimal(12,2) NOT NULL DEFAULT 0,
  `gift_name` varchar(64) DEFAULT NULL,
  `gift_count` int NOT NULL DEFAULT 1,
  `message` varchar(255) DEFAULT NULL,
  `paid_at` datetime NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_svt_video` (`video_id`, `paid_at`),
  KEY `idx_svt_payer` (`payer_user_id`, `paid_at`),
  KEY `idx_svt_taker` (`taker_user_id`, `paid_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频打赏记录'
```

#### `video_red_packet`
```sql
CREATE TABLE `video_red_packet` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `video_id` bigint unsigned NOT NULL,
  `sender_user_id` bigint unsigned NOT NULL,
  `total_amount` decimal(12,2) NOT NULL DEFAULT 0,
  `total_count` int NOT NULL DEFAULT 0,
  `claimed_count` int NOT NULL DEFAULT 0,
  `remaining_amount` decimal(12,2) NOT NULL DEFAULT 0,
  `cover_text` varchar(64) DEFAULT NULL,
  `countdown_seconds` int NOT NULL DEFAULT 10,
  `status` varchar(16) NOT NULL DEFAULT 'pending' COMMENT 'pending/done/expired/refunded',
  `effective_from` datetime DEFAULT NULL,
  `expire_at` datetime DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_vrp_video` (`video_id`, `created_at`),
  KEY `idx_vrp_sender` (`sender_user_id`, `created_at`),
  KEY `idx_vrp_status` (`status`, `expire_at`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频红包'
```

#### `video_red_packet_claim`
```sql
CREATE TABLE `video_red_packet_claim` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `packet_id` bigint unsigned NOT NULL,
  `user_id` bigint unsigned NOT NULL,
  `amount` decimal(12,2) NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_vrpc_dup` (`packet_id`, `user_id`),
  KEY `idx_vrpc_user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='红包领取明细'
```

#### `short_video_homepage`
```sql
CREATE TABLE `short_video_homepage` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `user_id` bigint unsigned NOT NULL,
  `bio` text,
  `real_name_verified` tinyint NOT NULL DEFAULT 0,
  `identity_verified` tinyint NOT NULL DEFAULT 0,
  `marital_status` varchar(32) DEFAULT NULL,
  `identity_tier` varchar(32) DEFAULT NULL,
  `feature_video_id` bigint unsigned DEFAULT NULL,
  `video_count` int NOT NULL DEFAULT 0,
  `follower_count` int NOT NULL DEFAULT 0,
  `following_count` int NOT NULL DEFAULT 0,
  `like_received` int NOT NULL DEFAULT 0,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_svhp_user` (`user_id`),
  KEY `idx_svhp_verified` (`real_name_verified`, `identity_verified`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='短视频会员主页'
```

### 9.2 幂等迁移
`_ensure_m7_columns` 内注册 6 张新表的 CREATE TABLE。

---

## 十、错误码表

| 状态码 | 场景 |
|---|---|
| 404 `VIDEO_NOT_FOUND` / `COMMENT_NOT_FOUND` / `PACKET_NOT_FOUND` / `HOMEPAGE_NOT_FOUND` |
| 409 `COMMENT_BATCH_TOO_LARGE`（>500） |
| 422 `BRUSH_RANGE_INVALID` |
| 422 `PACKET_EXPIRE_INVALID`（expire ≤ effective） |

---

## 十一、前端对应页面

| 页面 | 路径 |
|---|---|
| 短视频参数配置 | `src/app/(admin)/short-video-config/page.tsx` |
| 视频管理 | `src/app/(admin)/short-video-list/page.tsx` |
| 红包记录 | `src/app/(admin)/short-video-red-packet/page.tsx` |
| 评论审核 | `src/app/(admin)/short-video-comment/page.tsx` |
| 会员主页 | `src/app/(admin)/short-video-homepage/page.tsx` |
| 打赏管理 | `src/app/(admin)/short-video-tip/page.tsx` |

---

## 十二、文档完成自检清单

- [x] 6 表 + 配置域 + 24 端点
- [x] 路由顺序：`/brush` 在 `PATCH /{id}` 前
- [x] Decimal 字段全部 string 序列化
- [x] 测试 8 用例

---

## 十三、变更记录

- **2026-09-12 v1.0** —— M7-C 首版（视频/刷粉/评论/打赏/红包/主页/配置）
