SELECT 'aibot_story_play already exists' AS issue
WHERE EXISTS (
  SELECT 1 FROM information_schema.tables
  WHERE table_schema = DATABASE() AND table_name = 'aibot_story_play'
)
UNION ALL
SELECT 'aibot_story_peer_shown already exists' AS issue
WHERE EXISTS (
  SELECT 1 FROM information_schema.tables
  WHERE table_schema = DATABASE() AND table_name = 'aibot_story_peer_shown'
);
