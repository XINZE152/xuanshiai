SELECT 'aibot_story_play missing' AS issue
WHERE NOT EXISTS (
  SELECT 1 FROM information_schema.tables
  WHERE table_schema = DATABASE() AND table_name = 'aibot_story_play'
)
UNION ALL
SELECT 'aibot_story_peer_shown missing' AS issue
WHERE NOT EXISTS (
  SELECT 1 FROM information_schema.tables
  WHERE table_schema = DATABASE() AND table_name = 'aibot_story_peer_shown'
);
