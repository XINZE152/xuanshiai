-- Read-only review before deploying workflow v2.
-- Only member-created discovery applications automatically marked FAILED by old logic.
-- Do not alter admin historical records or explicit matchmaker final decisions.
SELECT mi.id, mi.note, mi.status, mi.failure_reason, ma.status AS application_status,
       'PENDING' AS proposed_status
FROM matchmaker_introduction mi
JOIN match_apply ma ON mi.note = CONCAT('discovery-apply:', ma.id)
WHERE mi.status = 'FAILED' AND ma.status IN (2, 3)
AND NOT EXISTS (
    SELECT 1 FROM business_audit_log a
    WHERE a.resource_type = 'matchmaker_introduction' AND a.resource_id = mi.id
    AND (a.action = 'matchmaker_workspace.introduction.contact_confirmed'
         OR (a.action = 'matchmaker_workspace.introduction.status' AND a.reason = 'FAILED'))
);
