# 红娘（牵线）一期接口

接口前缀：`/api/v1`。

一期范围：

- 服务红娘列表、详情和排行榜。
- 用户提交基础牵线服务申请。
- 用户查询自己的申请。
- 已通过审核的服务红娘查询和处理分配给自己的申请。
- 超级管理员查询、分配和处理牵线服务申请。
- 红娘申请提交、我的申请和管理员审核继续使用 `docs/api/identity.md` 中的接口。

二期范围：人脸第三方接入、会员、积分、支付、私人定制付费、咨询预约、佣金结算和 AI 红娘。

## 1. 认证和权限

- 公开接口：红娘列表、详情、排行榜。
- 登录用户：提交牵线申请、查询我的牵线申请。
- 有效服务红娘角色：查询分配给自己的申请、处理自己的申请。
- 超级管理员：查询、分配和处理全部牵线申请。当前系统使用 `user_role.role_code=admin` 表示超级管理员。
- 提交牵线申请要求实名认证通过；当前具体功能门禁仍以产品确认矩阵为准。
- 拉黑、封禁和用户状态校验必须在业务服务层执行，不能只依赖前端。

通用请求头：

```http
Authorization: Bearer <access_token>
Content-Type: application/json
```

## 2. 查询服务红娘列表

### `GET /api/v1/matchmakers`

- 权限：公开。
- 用途：查询审核通过、账号正常且有效服务红娘角色的列表。
- Query 参数：

| 参数 | 类型 | 必填 | 默认值 | 规则 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `page` | integer | 否 | `1` | 1~1000 | 页码 |
| `page_size` | integer | 否 | `20` | 1~50 | 每页数量 |

请求示例：

```http
GET http://127.0.0.1:8000/api/v1/matchmakers?page=1&page_size=20
```

成功响应 `200`：

```json
{
  "items": [
    {
      "user_id": 12,
      "nickname": "红娘小张",
      "avatar": "/storage/uploads/12/avatar.webp",
      "application_type": "service_matchmaker",
      "intro": "有多年婚恋咨询和沟通经验",
      "certification_tags": ["平台认证"],
      "success_count": 3,
      "rating_score": 4.8,
      "rating_count": 5,
      "is_available": true
    }
  ],
  "page": 1,
  "page_size": 20,
  "total": 1,
  "has_more": false
}
```

字段说明：`success_count` 为已完成服务数；`rating_score` 无评价时为 `0`；`rating_count` 为评价数量；`is_available` 只有有效服务红娘角色才为 `true`；资质原图不会通过公开接口返回，只返回认证标签。

## 3. 查询服务红娘详情

### `GET /api/v1/matchmakers/{matchmaker_id}`

- 权限：公开。
- Path 参数：`matchmaker_id`，正整数，红娘用户 ID。
- 资源规则：只返回审核通过、账号正常且服务红娘角色有效的红娘。

请求示例：

```http
GET http://127.0.0.1:8000/api/v1/matchmakers/12
```

成功响应字段与列表中的单个 `MatchmakerCard` 相同。

错误：

```json
{"detail":"服务红娘不存在或暂不可用"}
```

- `404`：红娘不存在、申请未通过、角色暂停或账号不可用。
- `422`：`matchmaker_id` 小于 1 或格式错误。

## 4. 查询热心红娘排行榜

### `GET /api/v1/matchmakers/ranking`

- 权限：公开。
- Query 参数：同红娘列表。
- 排序：已完成服务数降序、评分降序、审核时间降序、申请 ID 降序。
- 一期不实现收费排序和会员曝光；排行榜只使用已有完成服务和评价数据。

请求示例：

```http
GET http://127.0.0.1:8000/api/v1/matchmakers/ranking?page=1&page_size=10
```

返回结构与 `GET /api/v1/matchmakers` 相同。

## 5. 提交牵线服务申请

### `POST /api/v1/matchmaker/service-requests`

- 权限：登录用户。
- Content-Type：`application/json`。
- 前置条件：用户账号正常、实名认证通过、目标红娘有效、双方未拉黑。
- 一期服务类型固定为 `2`，表示基础/免费牵线服务。
- 幂等规则：同一用户向同一红娘存在 `待接单(0)` 或 `服务中(1)` 申请时，重复提交返回 `409`。

请求字段：

| 字段 | 位置 | 类型 | 必填 | 规则 | 含义 |
| --- | --- | --- | --- | --- | --- |
| `matchmaker_id` | body | integer | 是 | `>=1`，不能是当前用户 | 目标红娘用户 ID |
| `requirement` | body | string | 是 | 10~2000 字符 | 用户牵线需求 |

请求示例：

```http
POST http://127.0.0.1:8000/api/v1/matchmaker/service-requests
Authorization: Bearer <user_access_token>
Content-Type: application/json
```

```json
{
  "matchmaker_id": 12,
  "requirement": "希望寻找认真稳定的婚恋关系，年龄和城市可以适当放宽"
}
```

成功响应 `201`：

```json
{
  "id": 31,
  "user_id": 23,
  "matchmaker_id": 12,
  "service_type": 2,
  "status": 0,
  "requirement": "希望寻找认真稳定的婚恋关系，年龄和城市可以适当放宽",
  "feedback": null,
  "created_at": "2026-07-22T10:00:00",
  "updated_at": "2026-07-22T10:00:00",
  "start_at": null,
  "end_at": null
}
```

状态：`0` 待接单、`1` 服务中、`2` 已完成、`3` 已取消。

业务顺序：校验账号/实名/红娘/拉黑关系 -> 查询重复申请并加行锁 -> 创建服务申请 -> 写入红娘通知 -> 提交事务。通知失败不能造成重复申请，后续可加入通知重试。

错误：

| HTTP | 触发条件 | 示例 |
| --- | --- | --- |
| `401` | 未登录或 Token 失效 | `请先登录` |
| `403` | 未实名认证、账号受限 | `提交牵线申请前必须完成实名认证` |
| `404` | 目标红娘不存在或不可用 | `服务红娘不存在或暂不可用` |
| `409` | 已有处理中申请 | `已有处理中牵线申请，不能重复提交` |
| `422` | 目标是自己、需求长度非法 | `不能向自己提交牵线申请` |

## 6. 查询我的牵线申请

### `GET /api/v1/matchmaker/service-requests/mine`

- 权限：登录用户。
- Query 参数：`page` 1~1000，默认 1；`page_size` 1~50，默认 20。
- 资源范围：只返回当前用户作为申请人的记录。

请求示例：

```http
GET http://127.0.0.1:8000/api/v1/matchmaker/service-requests/mine?page=1&page_size=20
Authorization: Bearer <user_access_token>
```

返回分页结构：`items` 为服务申请数组，`total` 为总数，`has_more` 表示是否还有下一页。

## 7. 查询分配给我的申请

### `GET /api/v1/matchmaker/service-requests/assigned`

- 权限：登录用户且必须拥有有效 `service_matchmaker` 角色。
- Query 参数：`page` 1~1000，默认 1；`page_size` 1~50，默认 20。
- 资源范围：只返回 `matchmaker_id` 等于当前用户的记录。

错误：

```json
{"detail":"当前用户不是有效服务红娘"}
```

## 8. 红娘处理服务申请

### `PATCH /api/v1/matchmaker/service-requests/{service_id}`

- 权限：登录用户且必须是该申请被分配的服务红娘。
- Path 参数：`service_id`，正整数。
- 只有 `0待接单`、`1服务中` 可以继续处理。
- `status=1` 自动写入 `start_at`。
- `status=2/3` 自动写入 `end_at`。
- `status=2/3` 必须填写 `feedback`。

请求：

```http
PATCH http://127.0.0.1:8000/api/v1/matchmaker/service-requests/31
Authorization: Bearer <matchmaker_access_token>
Content-Type: application/json
```

```json
{"status":1,"feedback":"已接单，将先确认你的择偶要求"}
```

完成示例：

```json
{"status":2,"feedback":"已完成初步沟通并给出匹配建议"}
```

错误：

- `403`：不是被分配的服务红娘。
- `404`：服务申请不存在。
- `409`：申请已完成或已取消。
- `422`：状态值非法或完成/取消未填写处理说明。

## 9. 管理员查询服务申请

### `GET /api/v1/admin/matchmaker/service-requests`

- 权限：超级管理员。
- Query 参数：`status` 可选 `0~3`；`page` 1~1000；`page_size` 1~50。
- 返回所有服务申请的分页列表。

请求示例：

```http
GET http://127.0.0.1:8000/api/v1/admin/matchmaker/service-requests?status=0&page=1&page_size=20
Authorization: Bearer <admin_access_token>
```

## 10. 管理员分配或处理服务申请

### `PATCH /api/v1/admin/matchmaker/service-requests/{service_id}`

- 权限：超级管理员。
- 可以分配有效服务红娘、修改状态或补充处理说明。
- `matchmaker_id` 必须是当前有效 `service_matchmaker` 角色用户。
- `status=2/3` 必须填写 `feedback`。

请求示例：

```http
PATCH http://127.0.0.1:8000/api/v1/admin/matchmaker/service-requests/31
Authorization: Bearer <admin_access_token>
Content-Type: application/json
```

```json
{
  "matchmaker_id": 12,
  "status": 1,
  "feedback": "已分配给服务红娘小张"
}
```

错误：

- `401`：未登录或 Token 失效。
- `403`：当前用户不是超级管理员。
- `404`：服务申请不存在。
- `422`：红娘角色无效、状态非法或完成/取消未填写说明。

## 11. 一期状态和权限矩阵

| 操作 | 普通登录用户 | 服务红娘 | 超级管理员 |
| --- | --- | --- | --- |
| 查看红娘列表/详情/排行 | 是 | 是 | 是 |
| 提交牵线申请 | 实名后 | 是 | 是 |
| 查看我的申请 | 只能本人 | 只能本人 | 可全部查询 |
| 查看分配申请 | 否 | 只能本人被分配项 | 可全部查询 |
| 处理服务申请 | 否 | 只能本人被分配项 | 可处理全部 |
| 分配红娘 | 否 | 否 | 是 |
| 查看申请人联系方式 | 一期不返回 | 按业务授权 | 后台按治理需要 |

## 12. 现有模糊点

- 具体哪些功能必须实名认证仍待产品确认；本模块当前提交牵线申请先要求实名认证。
- 一期是否允许收费红娘、会员、积分和爆灯尚未实现，当前 `service_type=2` 固定为基础/免费牵线。
- 私人定制、付费咨询、预约日期、支付和退款属于二期。
- 红娘资料认证后哪些字段不可修改仍待产品确认；当前一期通过申请审核状态控制展示。
- 红娘服务申请被拒绝、过期或用户主动撤回后的规则尚未冻结；当前一期只提供红娘处理和管理员处理，未增加用户撤回接口。
- 服务完成后的评价、评分反作弊和排行榜刷量治理只使用已有预留表，完整评价接口属于后续 P1/P2 任务。
- 超级管理员查看敏感材料、导出证据和聊天原文的二次确认及留存期限仍待确认。
- 申请认识次数、推荐算法、会员曝光和爆灯规则不在本期红娘服务接口中实现。

## 13. Swagger 手动测试前置条件

1. 启动后端：

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

2. 打开 `http://127.0.0.1:8000/docs`。
3. 先准备三类账号：
   - 普通实名用户：用于提交牵线申请。
   - 已审核通过的服务红娘：用于查看和处理分配申请。
   - 超级管理员：用于查询、分配和治理申请。
4. 在 Swagger 页面点击右上角 `Authorize`，输入：

```text
Bearer <对应账号的 access_token>
```

5. 数据库中至少需要存在：
   - 一条 `user_matchmaker_apply.application_type='service_matchmaker'` 且 `status=1` 的申请。
   - 对应用户一条 `user_role.role_code='service_matchmaker'` 且 `status=1` 的角色。
   - 一个已完成实名认证的普通测试用户。

## 14. Swagger 手动测试顺序

### 场景 A：公开查看红娘

1. 不授权调用 `GET /api/v1/matchmakers`，确认返回 `200` 和分页结构。
2. 调用 `GET /api/v1/matchmakers/ranking`，确认返回排序字段。
3. 复制某个 `user_id`，调用 `GET /api/v1/matchmakers/{matchmaker_id}`。
4. 使用不存在的 ID，确认返回 `404 服务红娘不存在或暂不可用`。

### 场景 B：普通用户提交牵线申请

1. 使用普通实名用户 Token 调用 `POST /api/v1/matchmaker/service-requests`。
2. 请求体填写目标红娘 ID和不少于 10 个字符的需求。
3. 确认返回 `201`、`status=0`、`service_type=2`。
4. 再次提交相同目标红娘，确认返回 `409`，不会生成第二条处理中申请。
5. 调用 `GET /api/v1/matchmaker/service-requests/mine`，确认能查询刚创建的记录。
6. 使用未实名用户重复提交，确认返回 `403`。
7. 将目标改为自己的用户 ID，确认返回 `422`。

### 场景 C：服务红娘接单和完成

1. 使用服务红娘 Token 调用 `GET /api/v1/matchmaker/service-requests/assigned`。
2. 确认能看到分配给自己的待接单记录。
3. 调用 `PATCH /api/v1/matchmaker/service-requests/{service_id}`：

```json
{"status":1,"feedback":"已接单，开始沟通"}
```

4. 确认返回 `status=1` 且 `start_at` 不为空。
5. 再次调用同一接口：

```json
{"status":2,"feedback":"已完成初步沟通"}
```

6. 确认返回 `status=2` 且 `end_at` 不为空。
7. 对已完成记录再次修改，确认返回 `409`。
8. 使用另一个普通用户 Token 处理该记录，确认返回 `403`。

### 场景 D：超级管理员分配和处理

1. 使用管理员 Token 调用 `GET /api/v1/admin/matchmaker/service-requests?status=0`。
2. 使用 `PATCH /api/v1/admin/matchmaker/service-requests/{service_id}` 分配有效服务红娘。
3. 使用不存在或未审核通过的用户 ID 作为 `matchmaker_id`，确认返回 `422`。
4. 管理员将申请置为 `status=1`，确认红娘在 assigned 列表中可以看到。
5. 使用普通用户 Token 调用管理员接口，确认返回 `403`。

### 场景 E：输入校验

逐项确认 Swagger 返回 `422`：

- `matchmaker_id=0`。
- `requirement` 少于 10 个字符。
- `requirement` 超过 2000 个字符。
- `status=9`。
- `status=2` 但不填写 `feedback`。
- `service_id` 非正整数。

## 15. 运行检查

```powershell
.\.venv\Scripts\python.exe -m compileall -q app
.\.venv\Scripts\ruff.exe check app tests
.\.venv\Scripts\pytest.exe
```

如果使用 `uv run` 遇到本机 uv 缓存目录权限或路径错误，使用项目已有 `.venv` 中的工具执行上述检查。
