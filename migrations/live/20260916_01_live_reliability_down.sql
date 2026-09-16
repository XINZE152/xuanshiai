ALTER TABLE `live_provider_event`
    DROP COLUMN `last_error_code`,
    DROP COLUMN `process_attempts`,
    DROP COLUMN `event_time`;

ALTER TABLE `live_stage_seat`
    DROP INDEX `uk_live_active_user`,
    DROP COLUMN `active_user_id`;

DROP TABLE IF EXISTS `live_outbox_event`;
DROP TABLE IF EXISTS `live_participant_restriction`;
DROP TABLE IF EXISTS `live_match_result`;
