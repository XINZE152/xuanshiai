"""AI 客服（简单版，Q54，2026-11-11）。

这是一层**确定性**实现，不调大模型：

1. 用后台维护的 ``help_doc`` 语料（`GET /support/help-docs` 同一份数据）做检索；
2. 命中就返回 ``body`` 原文——答案与帮助中心完全一致，不会自相矛盾；
3. 问的是本平台的事、但知识库没有 → ``no_answer``，答「暂时无法帮助您，等待升级」；
4. 明显与本平台无关（写诗、编程、股票、天气……）→ ``off_topic``，直接拒答并引导
   用户改问使用问题。

为什么不上大模型：AI 域有三层门禁（``ai_master_enabled`` / ``ai_enabled`` / API Key），
本机与部分环境全部关闭，接上去就是「点了永远失败」——这正是 Q54 原来的症状。
规则版没有开关依赖、没有调用成本、结果是可复现的，能立刻上线；等 AI 域真正开闸，
把 :func:`answer_support_question` 换成 provider 调用即可，路由与前端契约不用动。

措辞上刻意不做「保证解决」的承诺：未命中一律引导转人工，避免用户被一句编造的
答案带偏（婚恋平台的错误引导代价很高）。
"""

from __future__ import annotations

import re
from typing import Sequence

from app.schemas.support import AiChatReply, HelpDocItem

ANSWERED = "answered"
NO_ANSWER = "no_answer"
OFF_TOPIC = "off_topic"

#: 检索时最多读取的帮助文档条数。后台文档量级很小（几十条），一次取全比翻页稳。
DOC_LIMIT = 200

#: 命中阈值。分词用二元组，命中一个标题级二元组记 4 分，
#: 因此 3 分等价于「至少共用一个实词片段」，再低就会把「怎么」「如何」匹配进来。
MATCH_THRESHOLD = 3

NO_ANSWER_REPLY = (
    "暂时无法帮助您解答这个问题，我们正在升级这部分能力。"
    "可以点「联系客服」把问题提交给人工，客服会在工作时间回复你。"
)

CLARIFY_REPLY = (
    "请描述具体是什么问题，比如「充值未到账」「怎么修改头像」「消息怎么撤回」。"
    "说清场景我才能按帮助中心的内容回答你。"
)

OFF_TOPIC_REPLY = (
    "我只能回答本平台使用相关的问题，比如账号登录、资料审核、匹配推荐、聊天消息、"
    "会员与支付、举报与隐私。请描述你遇到的具体情况，或点「联系客服」转人工。"
)

#: 平台内问题的领域词。命中即视为「问的是本平台的事」——
#: 此时宁可回答「暂时无法帮助您」，也不要甩一句「跟我无关」。
_DOMAIN_KEYWORDS: tuple[str, ...] = (
    "会员",
    "vip",
    "充值",
    "支付",
    "付款",
    "退款",
    "订单",
    "发票",
    "余额",
    "实名",
    "认证",
    "证件",
    "人脸",
    "审核",
    "头像",
    "照片",
    "资料",
    "资料卡",
    "匹配",
    "推荐",
    "喜欢",
    "心动",
    "关注",
    "粉丝",
    "浏览量",
    "访客",
    "聊天",
    "消息",
    "语音",
    "视频",
    "通话",
    "撤回",
    "删除",
    "拉黑",
    "屏蔽",
    "举报",
    "投诉",
    "封号",
    "禁言",
    "申诉",
    "隐私",
    "权限",
    "授权",
    "可见",
    "账号",
    "登录",
    "注册",
    "注销",
    "手机号",
    "密码",
    "验证码",
    "换绑",
    "红娘",
    "线下",
    "活动",
    "礼物",
    "积分",
    "邀请",
    "纸飞机",
    "情报",
    "动态",
    "社区",
    "评论",
    "点赞",
    "通知",
    "提醒",
    "设置",
    "帮助",
    "客服",
    "mbti",
    "标签",
    "身高",
    "学历",
    "城市",
    "择偶",
    "偏好",
    "分身",
    "军师",
)

#: 明显不属于客服范围的问题。命中直接拒答，优先于领域词——
#: 否则「帮我写一段会员系统的代码」会因为带「会员」被当成域内问题。
_OFF_TOPIC_KEYWORDS: tuple[str, ...] = (
    "写诗",
    "作诗",
    "写小说",
    "写作文",
    "写文案",
    "翻译",
    "编程",
    "代码",
    "python",
    "java",
    "sql",
    "算法题",
    "数学题",
    "作业",
    "考试",
    "股票",
    "彩票",
    "基金",
    "炒币",
    "算命",
    "塔罗",
    "风水",
    "天气",
    "新闻",
    "政治",
    "游戏攻略",
    "电影",
    "讲个笑话",
    "讲故事",
    "唱歌",
    "你是谁",
    "你叫什么",
    "你几岁",
    "忽略以上",
    "忽略之前",
    "系统提示",
    "你的设定",
    "扮演",
    "越狱",
    "开发者模式",
)

#: 疑问句里的高频虚词二元组，参与匹配会让几乎所有文档都被命中，先剔除。
_STOP_BIGRAMS = frozenset(
    {
        "怎么",
        "怎样",
        "如何",
        "什么",
        "为什",
        "为何",
        "可以",
        "能不",
        "不能",
        "请问",
        "我想",
        "我要",
        "是否",
        "哪里",
        "多少",
        "多久",
        "一个",
        "这个",
        "那个",
        "现在",
        "已经",
        "我的",
        "我是",
        "有没",
        "没有",
        "是不",
        "不是",
        "的话",
        "时候",
        "帮我看",
        "看一下",
        "一下",
        "麻烦",
        "谢谢",
        "咨询",
    }
)

_TOKEN_RE = re.compile(r"[\u4e00-\u9fff0-9a-z]+")


def normalize(text: str | None) -> str:
    """只保留汉字与字母数字，丢掉标点、空格、emoji。"""
    if not text:
        return ""
    return "".join(_TOKEN_RE.findall(str(text).lower()))


def _bigrams(text: str) -> set[str]:
    if len(text) < 2:
        return {text} if text else set()
    return {text[index : index + 2] for index in range(len(text) - 1)}


def _query_bigrams(query: str) -> set[str]:
    return _bigrams(normalize(query)) - _STOP_BIGRAMS


def _score(query_grams: set[str], doc: HelpDocItem) -> int:
    """标题命中权重最高，其次摘要，最后正文。"""
    if not query_grams:
        return 0
    title = _bigrams(normalize(doc.title))
    subtitle = _bigrams(normalize(doc.subtitle))
    body = _bigrams(normalize(doc.body))
    return 4 * len(query_grams & title) + 2 * len(query_grams & subtitle) + len(query_grams & body)


def _has_domain_keyword(query: str) -> bool:
    return any(keyword in query for keyword in _DOMAIN_KEYWORDS)


def _is_off_topic(query: str) -> bool:
    return any(keyword in query for keyword in _OFF_TOPIC_KEYWORDS)


def pick_doc(query: str, docs: Sequence[HelpDocItem]) -> tuple[HelpDocItem | None, int]:
    """返回得分最高的文档与其分数；同分取 id 较小（列表更靠前）的一条。"""
    grams = _query_bigrams(query)
    best: HelpDocItem | None = None
    best_score = 0
    for doc in docs:
        score = _score(grams, doc)
        if score > best_score:
            best, best_score = doc, score
    return best, best_score


def answer_support_question(content: str, docs: Sequence[HelpDocItem]) -> AiChatReply:
    """把一句用户提问变成一条客服回复。

    判定顺序（不可调换）：

    1. 无关内容（写诗、编程、股票……）→ 拒答；
    2. 太短且没有任何领域词（「怎么办」「?」）→ 请用户补充场景，不要瞎猜；
    3. 命中知识库且有正文 → 直接给答案；
    4. 问的是平台的事但没答案 / 命中的文档没有正文 → 「暂时无法帮助您」；
    5. 其余一律按无关处理（保守策略：宁可让用户改问，也不猜）。
    """
    raw = (content or "").strip()
    normalized = normalize(raw)
    if len(normalized) < 2:
        return AiChatReply(status=OFF_TOPIC, answer=OFF_TOPIC_REPLY, escalate=True)

    if _is_off_topic(normalized):
        return AiChatReply(status=OFF_TOPIC, answer=OFF_TOPIC_REPLY, escalate=True)

    domain = _has_domain_keyword(normalized)
    # 「怎么办」这类只有虚词的短提问：词条标题里恰好含「么办」就会被误命中，
    # 与其抛一条「充值未到账」糊到用户脸上，不如请他补一句场景。
    if len(normalized) < 4 and not domain:
        return AiChatReply(status=NO_ANSWER, answer=CLARIFY_REPLY, escalate=True)

    doc, score = pick_doc(raw, docs)
    if doc is not None and score >= MATCH_THRESHOLD:
        body = (doc.body or "").strip()
        if body:
            return AiChatReply(
                status=ANSWERED,
                answer=body,
                matched_title=doc.title,
                escalate=False,
            )
        # 命中了词条但后台没维护答案：明确说「暂时无法帮助您」，不要拿标题冒充答案。
        return AiChatReply(
            status=NO_ANSWER,
            answer=NO_ANSWER_REPLY,
            matched_title=doc.title,
            escalate=True,
        )

    if domain:
        return AiChatReply(status=NO_ANSWER, answer=NO_ANSWER_REPLY, escalate=True)

    return AiChatReply(status=OFF_TOPIC, answer=OFF_TOPIC_REPLY, escalate=True)
