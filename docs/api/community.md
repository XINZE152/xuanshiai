# 社区动态、评论与纸飞机接口

## 1. 通用约定

接口前缀：`/api/v1`。所有接口都要求登录且已绑定手机号：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

成功响应没有统一 `data` 包装层；删除类接口使用 `204 No Content` 且没有响应体。错误响应统一为：

```json
{"detail":"错误原因"}
```

当前实现复用 FastAPI、Pydantic、SQLAlchemy AsyncSession 和 Redis 日额度工具。动态/纸飞机的图片地址必须由前端先获得，但本组接口当前只校验地址字符串长度和数组数量，不负责文件上传、图片内容识别或敏感词审核。

## 2. 社区动态

### 2.1 发布动态

#### `POST /api/v1/community/posts`

权限：已登录且绑定手机号。成功状态：`201 Created`。

请求字段：

| 字段 | 位置 | 类型 | 必填 | 默认值 | 规则 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `content` | body | string | 是 | 无 | 1~2000 字符 | 动态正文 |
| `images` | body | array[string] | 否 | `[]` | 最多 9 个地址 | 动态图片地址 |
| `video` | body | string/null | 否 | `null` | 最长 500 字符 | 动态视频地址 |
| `location` | body | string/null | 否 | `null` | 最长 128 字符 | 展示位置文本 |
| `topic_id` | body | integer/null | 否 | `null` | 非空时 `>=1` | 话题 ID，当前未提供话题查询接口 |

请求示例：

```json
{
  "content":"今天去看了一个展览",
  "images":["/storage/uploads/1/photo.webp"],
  "video":null,
  "location":"上海",
  "topic_id":null
}
```

非法示例：

```json
{"content":"","images":[]}
```

成功返回 `CommunityPostResponse`：

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `id` | integer | 是 | 不为空 | 动态 ID |
| `user_id` | integer | 是 | 不为空 | 作者 ID |
| `nickname` | string/null | 是 | 未设置时 `null` | 作者昵称 |
| `avatar` | string/null | 是 | 未设置时 `null` | 作者头像 |
| `content` | string | 是 | 不为空 | 动态正文 |
| `images` | array[string] | 是 | 无图片为 `[]` | 图片地址 |
| `video` | string/null | 是 | 无视频为 `null` | 视频地址 |
| `location` | string/null | 是 | 未填写为 `null` | 位置文本 |
| `like_count` | integer | 是 | 无点赞为 `0` | 点赞数 |
| `comment_count` | integer | 是 | 无评论为 `0` | 评论数 |
| `is_liked` | boolean | 是 | 不为空 | 当前用户是否点赞 |
| `created_at` | datetime | 是 | 不为空 | 创建时间 |

响应示例：

```json
{
  "id":101,"user_id":1,"nickname":"小明","avatar":"/storage/uploads/1/avatar.webp",
  "content":"今天去看了一个展览","images":["/storage/uploads/1/photo.webp"],"video":null,
  "location":"上海","like_count":0,"comment_count":0,"is_liked":false,
  "created_at":"2026-07-20T12:00:00"
}
```

### 2.2 查看动态流

#### `GET /api/v1/community/posts`

成功状态 `200 OK`。查询参数：

| 参数 | 位置 | 类型 | 必填 | 默认值 | 规则 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `mode` | query | string | 否 | `latest` | `latest` 或 `following` | 全站最新或我关注用户的动态 |
| `page` | query | integer | 否 | `1` | `1~1000` | 页码 |
| `page_size` | query | integer | 否 | `20` | `1~50` | 每页数量 |

请求示例：

```http
GET /api/v1/community/posts?mode=following&page=1&page_size=20
Authorization: Bearer <access_token>
```

返回字段：

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `items` | array[CommunityPostResponse] | 是 | 无数据为 `[]` | 动态列表 |
| `page` | integer | 是 | 不为空 | 当前页 |
| `page_size` | integer | 是 | 不为空 | 当前页大小 |
| `total` | integer | 是 | 无数据为 `0` | 当前模式下动态总数 |

排序：先按平台置顶字段 `is_top` 倒序，再按 `created_at` 倒序。成功示例：

```json
{"items":[],"page":1,"page_size":20,"total":0}
```

### 2.3 删除动态

#### `DELETE /api/v1/community/posts/{post_id}`

路径参数 `post_id>=1`，请求体无，成功状态 `204 No Content`。仅作者可以删除自己的有效动态，服务端执行软删除；重复删除或删除他人动态返回：

```json
{"detail":"动态不存在或无权删除"}
```

### 2.4 点赞和取消点赞

#### `PUT /api/v1/community/posts/{post_id}/like`

请求体无，成功状态 `200 OK`，返回更新后的完整动态对象，`is_liked=true`。

#### `DELETE /api/v1/community/posts/{post_id}/like`

请求体无，成功状态 `200 OK`，返回更新后的完整动态对象，`is_liked=false`。

两类操作使用已有社区点赞记录，重复点赞或重复取消不会产生重复记录；动态不存在返回 `404`。

## 3. 评论

### 3.1 查询评论

#### `GET /api/v1/community/posts/{post_id}/comments`

查询参数 `page` 默认 `1`、范围 `1~1000`；`page_size` 默认 `20`、范围 `1~50`。成功状态 `200 OK`，按评论创建时间正序返回数组，不返回 `total`。

返回字段：

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `id` | integer | 是 | 不为空 | 评论 ID |
| `post_id` | integer | 是 | 不为空 | 动态 ID |
| `user_id` | integer | 是 | 不为空 | 评论者 ID |
| `nickname` | string/null | 是 | 未设置时 `null` | 评论者昵称 |
| `avatar` | string/null | 是 | 未设置时 `null` | 评论者头像 |
| `parent_id` | integer/null | 是 | 一级评论为 `null` | 父评论 ID |
| `content` | string | 是 | 不为空 | 评论内容 |
| `like_count` | integer | 是 | 无点赞为 `0` | 评论点赞数 |
| `created_at` | datetime | 是 | 不为空 | 创建时间 |

无评论时返回 `[]`。

### 3.2 发表评论或回复

#### `POST /api/v1/community/posts/{post_id}/comments`

成功状态 `201 Created`。请求体：

| 字段 | 位置 | 类型 | 必填 | 默认值 | 规则 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `content` | body | string | 是 | 无 | 1~500 字符 | 评论内容 |
| `parent_id` | body | integer/null | 否 | `null` | 非空时 `>=1`，且必须属于同一动态 | 父评论 ID；空值表示一级评论 |

请求示例：

```json
{"content":"这个展览看起来很不错","parent_id":null}
```

回复示例：

```json
{"content":"我也很喜欢这个主题","parent_id":201}
```

返回一个 `CommunityCommentResponse`，字段与 3.1 相同。动态不存在或父评论不存在返回 `404`；正文为空、超过 500 字符或 `parent_id` 非法返回 `422`。

### 3.3 删除评论

#### `DELETE /api/v1/community/comments/{comment_id}`

请求体无，成功状态 `204 No Content`。仅评论作者可以删除自己的有效评论，服务端软删除并将动态评论数减一。重复删除或删除他人评论返回 `404`。

## 4. 纸飞机

### 4.1 发送纸飞机

#### `POST /api/v1/paper-planes`

成功状态 `201 Created`。当前使用 Redis 做自然日额度控制：普通用户每天最多 3 次；Redis 不可用时返回 `503`。每条纸飞机默认有效 24 小时，数据库写入失败会退还已扣额度。

请求字段：

| 字段 | 位置 | 类型 | 必填 | 默认值 | 规则 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `content` | body | string | 是 | 无 | 1~1000 字符 | 纸飞机正文 |
| `images` | body | array[string] | 否 | `[]` | 最多 6 个地址 | 附图地址 |
| `city` | body | string/null | 否 | `null` | 最长 64 字符 | 展示城市 |
| `tags` | body | array[string] | 否 | `[]` | 最多 5 个标签 | 纸飞机标签 |
| `is_anonymous` | body | boolean | 否 | `true` | 布尔值 | 是否匿名展示 |

请求示例：

```json
{
  "content":"想认识同样喜欢旅行的人",
  "images":[],
  "city":"杭州",
  "tags":["旅行","交友"],
  "is_anonymous":true
}
```

返回字段：

| 字段 | 类型 | 必返 | 空值含义 | 含义 |
| --- | --- | --- | --- | --- |
| `id` | integer | 是 | 不为空 | 纸飞机 ID |
| `content` | string | 是 | 不为空 | 正文 |
| `images` | array[string] | 是 | 无图片为 `[]` | 图片地址 |
| `city` | string/null | 是 | 未填写为 `null` | 城市 |
| `tags` | array[string] | 是 | 无标签为 `[]` | 标签 |
| `is_anonymous` | boolean | 是 | 不为空 | 是否匿名 |
| `reply_count` | integer | 是 | 无回复为 `0` | 回复数 |
| `created_at` | datetime | 是 | 不为空 | 创建时间 |

### 4.2 捡取纸飞机

#### `GET /api/v1/paper-planes`

查询参数：`page` 默认 `1`、范围 `1~1000`；`page_size` 默认 `20`、范围 `1~50`。成功状态 `200 OK`，按创建时间倒序返回数组。结果排除自己的纸飞机、已过期/非有效纸飞机，以及当前用户已经回复过的纸飞机。

无数据返回 `[]`。当前响应不返回发送者 `user_id`；如果产品需要查看非匿名发送者，需要新增兼容字段并同步隐私规则。

### 4.3 查看我的纸飞机

#### `GET /api/v1/paper-planes/mine`

查询参数与 4.2 相同。成功状态 `200 OK`，只返回当前用户创建且仍未删除的有效纸飞机；返回数组，无数据时为 `[]`。

### 4.4 回复纸飞机

#### `POST /api/v1/paper-planes/{plane_id}/replies`

路径参数 `plane_id>=1`，成功状态 `201 Created`。请求字段：

| 字段 | 位置 | 类型 | 必填 | 默认值 | 规则 | 含义 |
| --- | --- | --- | --- | --- | --- | --- |
| `content` | body | string | 是 | 无 | 1~1000 字符 | 回复正文 |
| `is_anonymous` | body | boolean | 否 | `true` | 布尔值 | 是否匿名回复 |

请求示例：

```json
{"content":"我也喜欢旅行，可以认识一下","is_anonymous":true}
```

返回字段：

| 字段 | 类型 | 必返 | 含义 |
| --- | --- | --- | --- |
| `id` | integer | 是 | 回复 ID |
| `plane_id` | integer | 是 | 纸飞机 ID |
| `user_id` | integer | 是 | 回复者 ID |
| `content` | string | 是 | 回复正文 |
| `is_anonymous` | boolean | 是 | 是否匿名 |
| `created_at` | datetime | 是 | 创建时间 |

纸飞机不存在、已过期或状态不可回复返回 `404`；不能回复自己的纸飞机，返回：

```json
{"detail":"不能回复自己的纸飞机"}
```

每条纸飞机回复数达到 5 条后状态变为已回应，不再出现在可捡列表中。

## 5. 错误响应

| HTTP | 触发条件 | 示例 detail | 前端处理 |
| --- | --- | --- | --- |
| `401` | 未登录或会话失效 | `请先登录` | 清理 Token 并登录 |
| `403` | 未绑定手机号 | `请先绑定手机号` | 跳转手机号绑定 |
| `404` | 动态、评论、纸飞机或父评论不存在 | `纸飞机不存在或已过期` | 刷新当前列表 |
| `422` | 长度、类型、范围、枚举不合法 | `Field required` | 修正请求参数 |
| `429` | 当日纸飞机额度用完 | `今日纸飞机次数已用完` | 显示次日可用或会员提示 |
| `503` | Redis 未配置或暂时不可用 | `Redis服务未配置或暂时不可用` | 稍后重试，不重复提交 |

## 6. 当前边界与变更记录

当前未提供：动态/纸飞机媒体上传接口、媒体内容审核、敏感词审核、话题查询与管理、评论点赞接口、纸飞机语音、纸飞机回复自动转私信、后台社区审核列表。文档不把这些能力标记为已完成。

### 2026-07-20

- 补充所有请求参数位置、类型、必填性、默认值、范围和完整示例。
- 补充动态、评论、纸飞机和分页响应字段含义及空数据响应。
- 明确 Redis 日额度、纸飞机 24 小时有效期、5 条回复上限和当前媒体审核边界。
- 明确当前响应数组没有 `total` 的接口契约，后续改动需要兼容迁移。
