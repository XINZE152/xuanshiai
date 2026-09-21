-- Paper-plane bilateral contact exchange. Only consent state is stored.
ALTER TABLE paper_plane_message MODIFY COLUMN type TINYINT DEFAULT 1 COMMENT '1文本 2图片 3语音';

CREATE TABLE IF NOT EXISTS paper_plane_contact_exchange (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    conversation_id BIGINT UNSIGNED NOT NULL,
    kind VARCHAR(16) NOT NULL COMMENT 'wechat|phone',
    requester_user_id BIGINT UNSIGNED NOT NULL,
    target_user_id BIGINT UNSIGNED NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING' COMMENT 'PENDING|APPROVED|REJECTED|REVOKED',
    requester_consented_at DATETIME NULL,
    target_consented_at DATETIME NULL,
    responded_at DATETIME NULL,
    idempotency_key VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NOT NULL,
    response_idempotency_key VARCHAR(128) CHARACTER SET utf8mb4 COLLATE utf8mb4_bin NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_paper_plane_exchange_request_key (requester_user_id, idempotency_key),
    KEY idx_paper_plane_exchange_conversation (conversation_id, kind, requester_user_id, status),
    KEY idx_paper_plane_exchange_target (target_user_id, status),
    CONSTRAINT ck_paper_plane_exchange_kind CHECK (kind IN ('wechat', 'phone')),
    CONSTRAINT ck_paper_plane_exchange_users CHECK (requester_user_id <> target_user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
COMMENT='纸飞机双方联系方式交换申请（只存同意状态，不存联系方式）';
