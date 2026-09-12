# 商家联盟 接口契约（M7-B）

> **模块范围**：管理后台「商家联盟」菜单下 5 个页面
>
> - 商家联盟参数配置 `/merchant-alliance-config`
> - 商家管理（CRUD / 上下架 / 编辑）`/merchant-management`
> - 商品管理 `/merchant-product`
> - 商家订单 `/merchant-order`
>
> 商家联盟说明页：`active-alliance`（在活动运营方案菜单，不在本模块，无接口）。
>
> **后端文件**：
> 1. 商家 / 分类 / 商品 / 订单：`app/api/routes/merchant_admin.py`
> 2. 配置域 `tools_merchant_alliance` 走通用接口 `/api/v1/admin/configs`

---

## 一、通用约定

### 1.1 鉴权

- Token 类型：红娘后台独立 Token
- 请求头：`Authorization: Bearer <access-token>`
- 所有 admin 端点未登录 → 401；权限点缺失 → 403

### 1.2 权限点

| 路径前缀 | 读取 | 写入 |
|---|---|---|
| `/admin/merchants/*` | `community.merchant.read` | `community.merchant.manage` |
| `/admin/merchant-categories/*` | `community.merchant.read` | `community.merchant.manage` |
| `/admin/merchant-products/*` | `community.merchant.read` | `community.merchant.manage` |
| `/admin/merchant-orders/*` | `community.merchant.read` | `community.merchant.manage` |
| 配置域 `tools_merchant_alliance` | `platform.config.read` | `platform.config.write` |

路径子串映射见 `app/api/dependencies.py`。

### 1.3 响应与异常

- 分页壳 `{ items, page, page_size, total, has_more }`，字典下拉直接 list。
- 异常码：401 / 403 / 404 / 409 / 422 / 500。

---

## 二、商家分类（`/api/v1/admin/merchant-categories`）

### 2.1 列表 `GET /admin/merchant-categories`

```json
[{ "id": 1, "name": "婚纱摄影", "sort": 100, "status": 1, "created_at": "..." }]
```

### 2.2 新增 `POST /admin/merchant-categories`

```json
{ "name": "婚纱摄影", "sort": 100, "status": 1 }
```

### 2.3 排序 `POST /admin/merchant-categories/reorder`
请求体：`{ "orders": [{ "id": 1, "sort": 50 }, ...] }` → 204

### 2.4 修改 `PATCH /admin/merchant-categories/{category_id}`

### 2.5 删除 `DELETE /admin/merchant-categories/{category_id}` → 204
如果分类下仍有商家 → 409 `CATEGORY_HAS_MERCHANT`。

---

## 三、商家管理（`/api/v1/admin/merchants`）

### 3.1 列表 `GET /admin/merchants`

参数：`keyword`（公司名/联系人） / `category_id` / `verified`（0未审 / 1已审） / `online`（0下架 / 1上架） / `page` / `page_size`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | int | |
| `name` | string | 公司/商家名 |
| `category_id`, `category_name` | | 商家分类 |
| `logo` | string | |
| `contact_name`, `contact_phone` | | |
| `address` | string | |
| `description` | string | |
| `tags` | string[] | 标签数组 |
| `qualifications` | string[] | 资质图片 |
| `discount` | string | 优惠说明 |
| `online` | bool | 上架 |
| `verified` | int | 0未审 / 1已审 / 2拒绝 |
| `verify_status` | enum | `pending` / `approved` / `rejected` |
| `verify_remark` | string | |
| `sort_order` | int | |
| `commission_rate` | decimal | |
| `join_at`, `created_at` | | |
| `product_count` | int | 关联 `merchant_product` 数量 |
| `order_count` | int | 关联 `merchant_order` 数量 |

### 3.2 新增 `POST /admin/merchants`

```json
{
  "name": "...", "category_id": 1, "logo": "...",
  "contact_name": "...", "contact_phone": "...", "address": "...",
  "description": "...", "tags": ["...","..."],
  "qualifications": ["https://..."], "discount": "...",
  "online": true, "commission_rate": "0.05"
}
```

### 3.3 详情 `GET /admin/merchants/{merchant_id}`

### 3.4 修改 `PUT /admin/merchants/{merchant_id}`
请求体与新增一致，所有字段可空。

### 3.5 上下架 `PATCH /admin/merchants/{merchant_id}/visible`
`{ "online": bool }`。

### 3.6 删除 `DELETE /admin/merchants/{merchant_id}` → 204
软删（含其商品与未完成订单）。

### 3.7 字典下拉 `GET /admin/merchants/options`

```json
[{ "id": 1, "name": "杭州婚纱 X 摄影" }]
```

---

## 四、商家商品（`/api/v1/admin/merchant-products`）

### 4.1 列表 `GET /admin/merchant-products`

参数：`merchant_id` / `keyword` / `category_id` / `status`（0下架/1上架） / `page` / `page_size`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id`, `merchant_id`, `merchant_name` | | |
| `title`, `subtitle` | string | |
| `cover`, `images` | string/string[] | |
| `category_id`, `category_name` | | |
| `price`, `original_price` | decimal | string 序列化 |
| `stock` | int | |
| `sales` | int | 销量冗余 |
| `status` | int | 0下架 / 1上架 |
| `detail_html` | string | 富文本 |
| `tags` | string[] | |
| `effective_start`, `effective_end` | date | |
| `created_at` | | |

### 4.2 新增 `POST /admin/merchant-products`

```json
{
  "merchant_id": 1, "title": "...",
  "cover": "...", "images": ["..."], "category_id": 2,
  "price": "99.00", "original_price": "199.00",
  "stock": 100, "detail_html": "...",
  "tags": ["...","..."],
  "effective_start": "2026-04-01", "effective_end": "2026-12-31"
}
```

### 4.3 上架/下架 `PATCH /admin/merchant-products/{product_id}/status`

### 4.4 修改 `PUT /admin/merchant-products/{product_id}`

### 4.5 删除 `DELETE /admin/merchant-products/{product_id}` → 204

### 4.6 详情 `GET /admin/merchant-products/{product_id}`

---

## 五、商家订单（`/api/v1/admin/merchant-orders`）

### 5.1 列表 `GET /admin/merchant-orders`

参数：`merchant_id` / `product_id` / `buyer_user_id` / `keyword`（订单号/商品名） / `status`（0待付 / 1已付 / 2取消 / 3退款） / `pay_status`（free/paid/unpaid） / `start_date` / `end_date` / `page` / `page_size`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id`, `order_no` | int/string | |
| `merchant_id`, `merchant_name` | | |
| `product_id`, `product_title`, `product_cover` | | |
| `buyer_user_id`, `buyer_nickname`, `buyer_phone` | | |
| `quantity` | int | |
| `unit_price`, `total_amount` | decimal | string |
| `coupon_id`, `discount_amount` | decimal | string |
| `paid_amount` | decimal | 实际支付 |
| `status` | int | 0=待付 / 1=已付 / 2=已取消 / 3=退款中 |
| `pay_status` | enum | free/paid/unpaid |
| `pay_method` | string | |
| `pay_at`, `refund_at`, `created_at` | | |
| `remark` | string | |

### 5.2 导出 `GET /admin/merchant-orders/export`
Excel 导出，文件名 `merchant-orders-YYYYMMDD.xlsx`。

### 5.3 修改订单 `PATCH /admin/merchant-orders/{order_id}`
请求体：
- `status`（0/1/2/3）
- `pay_status`（free/paid/unpaid）
- `paid_amount`（decimal string）
- `refund_reason`

### 5.4 详情 `GET /admin/merchant-orders/{order_id}`

---

## 六、配置域 `tools_merchant_alliance`

```json
{
  "title": "优选合作商城",
  "intro": "本联盟商家均为品牌合作方",
  "share_cover": "https://...",
  "share_title": "邀请您体验商家福利",
  "share_desc": "...",
  "banner_images": ["https://...", "https://..."],
  "entry_categories": [{ "name": "婚纱摄影", "icon": "..." }],
  "purchase_notice": "1. 订单使用规则 2. 退款规则 ...",
  "show_qualification": true,
  "auto_audit": false
}
```

调用：`GET /api/v1/admin/configs/tools_merchant_alliance` / `PUT`。

---

## 七、数据库变更

### 7.1 新建表

#### `merchant_category`
```sql
CREATE TABLE `merchant_category` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(64) NOT NULL,
  `sort` int NOT NULL DEFAULT 100,
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1启用/0禁用',
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_mc_status_sort` (`status`, `sort`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商家分类'
```

#### `merchant`
```sql
CREATE TABLE `merchant` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `name` varchar(128) NOT NULL,
  `category_id` bigint unsigned DEFAULT NULL,
  `logo` varchar(255) DEFAULT NULL,
  `contact_name` varchar(64) DEFAULT NULL,
  `contact_phone` varchar(20) DEFAULT NULL,
  `address` varchar(255) DEFAULT NULL,
  `description` text,
  `tags` varchar(512) DEFAULT NULL COMMENT 'JSON array',
  `qualifications` text COMMENT 'JSON array urls',
  `discount` varchar(255) DEFAULT NULL,
  `commission_rate` decimal(5,4) NOT NULL DEFAULT 0 COMMENT '0~1',
  `online` tinyint NOT NULL DEFAULT 1,
  `verified` tinyint NOT NULL DEFAULT 0 COMMENT '0未审/1通过/2拒绝',
  `verify_remark` varchar(255) DEFAULT NULL,
  `sort_order` int NOT NULL DEFAULT 100,
  `join_at` datetime DEFAULT NULL,
  `deleted_at` datetime DEFAULT NULL,
  `created_by` int unsigned DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `idx_m_category` (`category_id`, `online`, `verified`),
  KEY `idx_m_name` (`name`),
  KEY `idx_m_online_sort` (`online`, `sort_order`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商家表'
```

#### `merchant_product`
```sql
CREATE TABLE `merchant_product` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `merchant_id` bigint unsigned NOT NULL,
  `category_id` bigint unsigned DEFAULT NULL,
  `title` varchar(128) NOT NULL,
  `subtitle` varchar(255) DEFAULT NULL,
  `cover` varchar(255) DEFAULT NULL,
  `images` text COMMENT 'JSON array urls',
  `price` decimal(12,2) NOT NULL DEFAULT 0,
  `original_price` decimal(12,2) NOT NULL DEFAULT 0,
  `stock` int NOT NULL DEFAULT 0,
  `sales` int NOT NULL DEFAULT 0,
  `detail_html` text,
  `tags` varchar(512) DEFAULT NULL,
  `effective_start` date DEFAULT NULL,
  `effective_end` date DEFAULT NULL,
  `status` tinyint NOT NULL DEFAULT 1 COMMENT '1上架/0下架',
  `created_by` int unsigned DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  `deleted_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `idx_mp_merchant` (`merchant_id`, `status`),
  KEY `idx_mp_title` (`title`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商家商品'
```

#### `merchant_order`
```sql
CREATE TABLE `merchant_order` (
  `id` bigint unsigned NOT NULL AUTO_INCREMENT,
  `order_no` varchar(32) NOT NULL,
  `merchant_id` bigint unsigned NOT NULL,
  `product_id` bigint unsigned NOT NULL,
  `buyer_user_id` bigint unsigned NOT NULL,
  `quantity` int NOT NULL DEFAULT 1,
  `unit_price` decimal(12,2) NOT NULL DEFAULT 0,
  `total_amount` decimal(12,2) NOT NULL DEFAULT 0,
  `coupon_id` bigint unsigned DEFAULT NULL,
  `discount_amount` decimal(12,2) NOT NULL DEFAULT 0,
  `paid_amount` decimal(12,2) NOT NULL DEFAULT 0,
  `pay_status` varchar(16) NOT NULL DEFAULT 'unpaid',
  `pay_method` varchar(16) DEFAULT NULL,
  `pay_at` datetime DEFAULT NULL,
  `status` tinyint NOT NULL DEFAULT 0 COMMENT '0待付/1已付/2已取消/3退款中',
  `refund_reason` varchar(255) DEFAULT NULL,
  `remark` varchar(255) DEFAULT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `uk_mo_order_no` (`order_no`),
  KEY `idx_mo_merchant` (`merchant_id`, `status`),
  KEY `idx_mo_buyer` (`buyer_user_id`, `created_at`),
  KEY `idx_mo_product` (`product_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='商家订单'
```

### 7.2 幂等迁移
`_ensure_m7_columns` 在 `database_setup_marriage.py` 主流程中调用，新增 4 张表 + `merchant_category.sort/status` 兜底列。

---

## 八、错误码表

| 状态码 | 场景 |
|---|---|
| 400 `MERCHANT_PRICE_INVALID` | |
| 409 `CATEGORY_HAS_MERCHANT` / `PRODUCT_HAS_ORDER` |
| 422 `MERCHANT_NOT_FOUND` / `PRODUCT_NOT_FOUND` / `ORDER_NOT_FOUND` |
| 409 `MERCHANT_DELETE_HAS_PAID` | 已有未完成支付订单 |

---

## 九、前端对应页面

| 页面 | 路径 |
|---|---|
| 商家联盟参数配置 | `src/app/(admin)/merchant-alliance-config/page.tsx` |
| 商家管理 | `src/app/(admin)/merchant-management/page.tsx` |
| 商品管理 | `src/app/(admin)/merchant-product/page.tsx` |
| 商家订单 | `src/app/(admin)/merchant-order/page.tsx` |

---

## 十、文档完成自检清单

- [x] 4 表 + 配置域 + 14 端点
- [x] 路由静态段在动态段之前
- [x] 测试 8 用例
- [x] 前端 tsc 通过

---

## 十一、变更记录

- **2026-09-12 v1.0** —— M7-B 首版（商家 + 商品 + 订单 + 配置）
