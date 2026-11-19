# 工作台会员匹配候选

任务标识：MATCH-FILTER-20261112。

`GET /api/v1/matchmaker/workbench/members/{member_id}/match-candidates`，需要红娘登录及该会员的工作台访问权限；无请求体。返回 JSON。

| 参数 | 位置 | 说明 |
| --- | --- | --- |
| member_id | path | 正整数，目标会员 ID。 |
| page | query | 页码，1–1000，默认 1。 |
| page_size | query | 每页 1–100 条，默认 20。 |
| filters | query | 可选 JSON 字符串，格式及字段和值同工作台会员列表资料筛选；最长 4096 字符。 |

示例：`?page=1&page_size=20&filters=%7B%22ageRange%22%3A%22range%3A25%3A35%22%7D`，请求头 `Authorization: Bearer <token>`。无请求体。

目标会员仍须通过红娘工作台的访问权限检查。候选范围覆盖所有归属的已过审会员，包括未分配会员；默认使用目标会员的择偶要求过滤，并排除目标本人、停用或删除、未通过资料审核、停止相亲和已经脱单的会员，以及默认异性筛选不匹配的会员。`filters` 中出现的性别、年龄、身高、学历、收入、婚姻、现居地字段替换相应默认条件；字段值为空字符串表示取消该项要求，未出现的字段仍沿用原要求。其他资料筛选与以上条件共同生效。筛选先于分页和总数统计。

响应包含 `items`、`page`、`page_size`、`total`、`has_more`、`preference` 和 `filter_version`（新版为 1，旧版缺失）。每个 `items` 元素包含 `user_id`、`nickname`、可空 `avatar`、可空 `gender`（1 男、2 女）、可空 `birthday`、可空 `residence`、可空 `height`、可空 `is_married`、可空 `matchmaker_name`。`preference` 可为空，否则含可空 `age_min`、`age_max`、`height_min`、`height_max`、`income_min`、`education_min`。空列表时 `items` 为 `[]`、`total` 为 0、`has_more` 为 false。前端应检查版本，旧服务可能忽略筛选。

查询无状态、不修改会员数据，无额外次数限制或幂等键要求。无登录返回 401；无权限或会员不存在返回 403/404；非法 JSON、未知字段、无效值、页码越界返回 422，客户端应展示错误并允许更正。例如 `filters={"ageRange":"invalid"}` 为非法条件。
