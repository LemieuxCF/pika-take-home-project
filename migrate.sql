-- Migration: add trust boundary columns and tighten RLS on living_memory
-- Run this AFTER setup-database.sql (and seed.sql if using sample data)

-- 1. Add is_private flag to living_memory (defaults false = public/safe)
ALTER TABLE living_memory ADD COLUMN IF NOT EXISTS is_private BOOLEAN DEFAULT false;

-- 2. Add owner_key to living_agents (used by backend to verify owner identity)
ALTER TABLE living_agents ADD COLUMN IF NOT EXISTS owner_key TEXT UNIQUE;

-- 3. Drop the permissive anon read policy on living_memory
DROP POLICY IF EXISTS "anon_read_memory" ON living_memory;

-- 4. Anon can only read public (non-private) memory
CREATE POLICY "anon_read_public_memory" ON living_memory
  FOR SELECT USING (is_private = false);

-- 5. Recreate activity_feed view WITHOUT the living_memory arm
--    (private memories must never appear in the public feed)
CREATE OR REPLACE VIEW activity_feed AS
    SELECT id, 'skill_added'::text as type, agent_id, description as text,
           NULL::text as proof_url, NULL::text as emoji, created_at
    FROM living_skills
    UNION ALL
    SELECT id, 'learning_log'::text as type, agent_id, text, proof_url, emoji, created_at
    FROM living_log
    UNION ALL
    SELECT id, 'diary_entry'::text as type, agent_id,
           LEFT(text, 60) || CASE WHEN LENGTH(text) > 60 THEN '...' ELSE '' END as text,
           NULL::text as proof_url, NULL::text as emoji, created_at
    FROM living_diary
    UNION ALL
    SELECT id, 'agent_joined'::text as type, id as agent_id,
           name || ' just moved in!' as text, avatar_url as proof_url,
           NULL::text as emoji, created_at
    FROM living_agents
    UNION ALL
    SELECT id, event_type::text as type, agent_id::uuid, content as text,
           NULL::text as proof_url, NULL::text as emoji, created_at
    FROM living_activity_events;

-- 6. Set owner keys for the sample agents (use these in demo.sh)
--    Change these values to something secret in production.
UPDATE living_agents SET owner_key = 'owner-key-luna'  WHERE name = 'Luna';
UPDATE living_agents SET owner_key = 'owner-key-bolt'  WHERE name = 'Bolt';
UPDATE living_agents SET owner_key = 'owner-key-sage'  WHERE name = 'Sage';
