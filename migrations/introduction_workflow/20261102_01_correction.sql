-- Data correction only, no DDL. Export review results + original rows before running.
-- Run once after verifying 20261102_01_review.sql; never run automatically on app startup.
START TRANSACTION;
UPDATE matchmaker_introduction mi
JOIN match_apply ma ON mi.note = CONCAT('discovery-apply:', ma.id)
SET mi.status = 'PENDING',
    mi.failure_reason = CASE WHEN ma.status = 2 THEN mi.failure_reason ELSE NULL END,
    mi.updated_at = UTC_TIMESTAMP()
WHERE mi.status = 'FAILED' AND ma.status IN (2, 3)
AND NOT EXISTS (
    SELECT 1 FROM business_audit_log a
    WHERE a.resource_type = 'matchmaker_introduction' AND a.resource_id = mi.id
    AND (a.action = 'matchmaker_workspace.introduction.contact_confirmed'
         OR (a.action = 'matchmaker_workspace.introduction.status' AND a.reason = 'FAILED'))
);
SELECT ROW_COUNT() AS corrected_introductions;
COMMIT;
