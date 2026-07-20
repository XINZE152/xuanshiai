# 首页推荐与名片浏览接口

统一前缀：`/api/v1`。

除 `GET /discovery/filter-options` 外，接口都需要登录态：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

## 筛选选项

### `GET /discovery/filter-options`

返回性别、婚姻状况、学历和固定城市选项。无需登录。

响应示例：

```json
{
  "genders": [{"value": 1, "label": "男"}, {"value": 2, "label": "女"}],
  "marriage_statuses": [{"value": 1, "label": "未婚"}],
  "education_levels": [{"value": 3, "label": "本科"}],
  "cities": ["北京", "上海"]
}
```

### `GET /discovery/recommendations`

推荐名片流。分页默认每页 20 条，推荐流会排除已浏览、已划过、申请中、匹配中和互相拉黑的用户。

可选查询参数：`gender`、`age_min`、`age_max`、`province_code`、`city_code`、`district_code`、`marriage_status`、`education_min`、`height_min`、`height_max`、`income_min`、`income_max`、`pure_free`、`page`、`page_size`。

`page_size` 范围为 1~20；年龄范围为 18~100；身高和收入的上下限必须合法且下限不大于上限。

### `GET /discovery/plaza`

广场名片流。参数与推荐流一致，不排除已浏览用户，但仍过滤未完善资料、不可见、拉黑和停用用户。

### `GET /discovery/search`

登录后按昵称或标签搜索用户，返回同样的名片分页结构。支持参数：

- `nickname`：昵称模糊搜索，最多 64 字；会按字面匹配 `%` 和 `_`，不会把它们当作通配符。
- `tag`：搜索用户的兴趣标签、性格标签或固定标签分类中的标签值，最多 64 字。
- `page`、`page_size`：分页参数，`page_size` 范围为 1~20。

`nickname` 和 `tag` 至少传一个，同时传入时需要同时满足。搜索结果只展示资料完善且当前可见的用户，并按年龄相仿度、标签重合度和活跃时间排序。

示例：

```text
GET /api/v1/discovery/search?tag=旅行&page=1&page_size=20
GET /api/v1/discovery/search?nickname=小明
GET /api/v1/discovery/search?nickname=小明&tag=摄影
```

### `GET /discovery/filters/saved`

返回当前用户保存的筛选条件。未保存时返回：

```json
{"filters": null}
```

### `PUT /discovery/filters/saved`

保存当前用户的筛选条件，请求体与推荐接口查询参数对应：

```json
{
  "gender": 2,
  "age_min": 25,
  "age_max": 35,
  "city_code": "310100",
  "page": 1,
  "page_size": 20
}
```

## 浏览、收藏和主页

### `GET /users/{user_id}/profile`

查看他人主页并记录浏览行为。普通用户每天 20 次完整浏览；合拍度大于 80 分时额外获得 5 次；VIP 不受该额度限制。普通用户超过额度后仍返回头像、昵称和年龄，其余名片字段置空并将 `detail_locked` 设为 `true`。

如果对方开启“仅 VIP 查看详情”，普通用户只能看到锁定名片。VIP 开启无痕浏览后，不会写入对方访客记录。

### `GET /discovery/browse-history`

浏览记录。普通用户只返回当天记录，VIP 返回历史记录。支持 `page` 和 `page_size`（1~50）。

### `GET /discovery/visitors`

查看谁看过我。普通用户只返回数量，VIP 返回访客名片列表。

### `GET /discovery/favorites`

返回当前用户收藏的名片列表。

### `PUT /discovery/favorites/{target_id}` / `DELETE /discovery/favorites/{target_id}`

收藏或取消收藏。收藏只对当前用户可见，不通知对方。

## 认识申请与爆灯

### `POST /discovery/applications/{target_id}`

请求体：

```json
{"message": "你好，很高兴认识你"}
```

留言可为空，最多 255 字。资料完整度未达到 100% 时返回 `403`。普通用户每日 3 次，VIP 每日 10 次；申请有效期 48 小时，超过有效期会在后续访问时自动标记为 `status=3`。

### `GET /discovery/applications/incoming`

返回收到的申请。

### `GET /discovery/applications/outgoing`

返回发出的申请。

### `POST /discovery/applications/{application_id}/accept`

仅申请接收方可调用。状态改为同意，同时创建双方匹配记录和私信会话。

### `POST /discovery/applications/{application_id}/reject`

仅申请接收方可调用。请求体可选：`{"reason": "暂时不合适"}`。拒绝不可撤回，并退还申请发起方当日 1 次额度。

### `POST /discovery/superlikes/{target_id}`

普通用户每日 1 次，VIP 每日 3 次。成功后写入爆灯记录并创建对方通知。

## 分享海报

### `GET /users/{user_id}/poster?template=1`

`template` 范围为 1~25，返回 `image/png`。服务端调用微信小程序码接口生成目标用户主页二维码，再合成海报。

## 错误码

- `401`：未登录或登录会话失效。
- `403`：资料未完善、无权处理该资源或被拉黑。
- `404`：用户不存在、用户不可见或资源不属于当前用户可操作范围。
- `409`：重复收藏、已有进行中的申请或申请已被处理。
- `422`：请求参数格式、范围或上下限校验失败。
- `429`：当日浏览、申请或爆灯额度已用完。
- `503`：Redis 不可用，或微信小程序码配置/调用失败。

## 必填配置

请在项目根目录 `.env`（不要提交到 Git）填写实际值：

```env
DATABASE_URL=mysql+aiomysql://<user>:<password>@<host>:3306/<database>
REDIS_URL=redis://<host>:6379/0
SECRET_KEY=<long-random-secret>
WECHAT_APP_ID=<your-wechat-app-id>
WECHAT_APP_SECRET=<your-wechat-app-secret>
WECHAT_MINI_PROGRAM_PAGE=pages/profile/profile
PUBLIC_BASE_URL=https://<your-api-domain>
```

Redis 是每日额度和配额回退的必需依赖；Redis 未启动时相关操作会返回 `503`，不会放行超额请求。生成海报必须填写微信 AppID 和 AppSecret。数据库初始化脚本会创建 `user_notification`、`user_discovery_filter`，并为旧版 `user_boost` 补齐 `start_at`、`end_at`、`status` 字段。

底部五 Tab 和首页顶部本地 Tab 属于小程序前端交互；当前仓库没有前端代码，因此本次只提供后端接口和数据契约。
