-- 20260917_04 资料卡「关于我」问答成为 user_profile 一等字段。
-- 独立 JSON 列，不复用 tags / love_view / hobbies 等已有列。
-- 完整度不计 QA。回滚 down 删除本列。

ALTER TABLE `user_profile`
    ADD COLUMN `qa_answers` json DEFAULT NULL COMMENT '关于我问答 [{question_id,question,answer}]' AFTER `self_intro`;
