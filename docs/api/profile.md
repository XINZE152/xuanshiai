# 个人资料与媒体 API

统一前缀：`/api/v1`。除固定标签选项接口外，以下接口都需要：
`Authorization: Bearer <access_token>`。

## 固定标签选项

`GET /api/v1/profile/tag-options`

该接口无需登录，返回前端资料表单使用的固定标签目录。当前版本为 `v1`，标签只能从返回的分类选项中选择，不能提交自定义文本。

响应示例：

```json
{
  "version": "v1",
  "categories": [
    {
      "key": "relationship_expectation",
      "label": "期望关系",
      "options": ["寻找长期伴侣", "先交友看缘分", "轻松约会"]
    }
  ]
}
```

## 资料完善度

`GET /api/v1/users/me/completion`

响应示例：

```json
{
  "score": 57,
  "missing_items": ["相册", "实名认证"],
  "items": [
    {"key":"gender","label":"性别","weight":7,"completed":true},
    {"key":"avatar","label":"头像","weight":15,"completed":true}
  ],
  "can_browse": false,
  "can_apply": false,
  "can_chat": false
}
```

完善度权重总计 100：

| 项目 | 权重 |
| --- | ---: |
| 性别 | 7 |
| 出生日期/年龄 | 7 |
| 所在地区 | 7 |
| 婚姻状况 | 5 |
| 职业 | 4 |
| 学历 | 4 |
| 收入 | 4 |
| 身高 | 4 |
| 头像 | 15 |
| 自我介绍 | 10 |
| 相册 | 10 |
| 兴趣标签 | 5 |
| 性格标签 | 3 |
| MBTI | 2 |
| 择偶要求 | 3 |
| 实名认证 | 5 |
| 单身承诺 | 5 |

推荐、申请和聊天权限只有在 `score=100` 时开放；申请和聊天还要求实名认证通过。

## 基础资料

### 查询资料

`GET /api/v1/users/me/profile`

返回性别、出生日期、年龄、婚姻状况、身高、职业、学历、收入、地区、MBTI、标签、头像、背景墙和媒体列表。

### 更新资料

`PATCH /api/v1/users/me/profile`

请求示例：

```json
{
  "gender": 1,
  "birthday": "1995-05-20",
  "is_married": 1,
  "height": 175,
  "occupation": "软件工程师",
  "industry": "互联网",
  "education_level": 3,
  "income": 15000,
  "residence_province_code": "310000",
  "residence_city_code": "310100",
  "self_intro": "喜欢旅行、摄影和运动，希望认识真诚的人。",
  "interest_tags": ["旅行", "摄影", "健身"],
  "personality_tags": ["真诚", "耐心", "乐观"],
  "mbti": "ENFP",
  "tag_selections": {
    "relationship_expectation": ["寻找长期伴侣"],
    "sports": ["健身", "跑步"]
  }
}
```

性别一旦写入不可修改；出生日期必须对应年满 18 岁；身高范围为 150~200cm；标签必须各选择 3~5 个；MBTI 必须是 16 种标准类型之一。
`tag_selections` 的分类 key 和选项值必须来自 `/api/v1/profile/tag-options`，每个分类最多选择 5 项。

### 主页预览

`GET /api/v1/users/me/profile/preview`

返回：

```json
{
  "preview_notice": "这是别人看到你的样子",
  "profile": {}
}
```

## 自我介绍模板

`GET /api/v1/users/me/intro-templates`

返回一期内置模板。模板库后台配置和录音转文字暂未接入第三方服务。

## 头像和相册

以下接口使用 `multipart/form-data`，字段名均为 `file`。

- `POST /api/v1/users/me/avatar`：上传头像，支持 JPG/JPEG/PNG，单文件最大 5MB，服务端转换为 WebP 并生成缩略图；重复上传覆盖旧头像。
- `POST /api/v1/users/me/background`：上传主页背景墙，支持 JPG/JPEG/PNG，单文件最大 5MB，重复上传覆盖旧背景。
- `POST /api/v1/users/me/photos`：上传相册图片，支持 JPG/JPEG/PNG，单文件最大 5MB，最多 9 张，服务端转换为 WebP。
- `DELETE /api/v1/users/me/photos/{media_id}`：删除相册图片。
- `PUT /api/v1/users/me/photos/{media_id}/primary`：设置首图。
- `PUT /api/v1/users/me/photos/order`：调整顺序。

排序请求：

```json
{"media_ids":[12,8,15]}
```

## 视频

`POST /api/v1/users/me/video`

使用 `multipart/form-data` 上传 MP4 视频。单文件最大 50MB，每个用户最多 1 个，时长最长 30 秒。服务端使用 `ffprobe` 从文件元数据读取真实时长，不接受客户端提交的时长字段。

如果服务端没有安装 `ffprobe`，返回 `503`；无法识别或格式不是 MP4 返回 `415`；超过大小返回 `413`；超过时长返回 `422`。

## 择偶要求

### 查询

`GET /api/v1/users/me/preferences`

### 更新

`PUT /api/v1/users/me/preferences`

请求示例：

```json
{
  "age_min": 25,
  "age_max": 35,
  "height_min": 160,
  "height_max": 175,
  "education_min": 3,
  "income_min": 10000,
  "marriage_status": 1,
  "preferred_province_code": "310000",
  "preferred_city_codes": ["310100"],
  "accept_long_distance": false,
  "accept_cross_province": false,
  "housing_requirement": 0,
  "smoking_requirement": 2,
  "drinking_requirement": 1,
  "extra_requirement": "希望作息规律，愿意沟通。"
}
```

年龄和身高下限不能大于上限；更新成功后立即影响后续推荐，不追溯已生成的推荐列表。

## 错误码

| HTTP | 场景 |
| --- | --- |
| `401` | 未登录或 Token 无效 |
| `404` | 媒体不存在或用户不存在 |
| `409` | 性别修改、相册数量、视频数量或媒体状态冲突 |
| `413` | 文件超过大小限制 |
| `415` | 图片/视频格式不支持或内容无法识别 |
| `422` | 字段范围、标签数量、MBTI、年龄、时长或排序参数不合法 |
| `503` | `ffprobe` 未安装或视频处理能力未配置 |
