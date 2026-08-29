DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM agent_preset AS preset
        LEFT JOIN agent_preset_version AS current_version
            ON current_version.id = preset.current_version_id
        WHERE preset.deleted_at IS NULL
            AND preset.current_version_id IS NOT NULL
            AND (
                current_version.id IS NULL
                OR current_version.workspace_id <> preset.workspace_id
                OR current_version.preset_id <> preset.id
            )
    ) THEN
        RAISE EXCEPTION
            'Cannot normalize agent preset topology: invalid current preset version';
    END IF;
END $$;

CREATE TEMP TABLE _agent_preset_subagent_backfill
ON COMMIT DROP AS
WITH current_topology AS (
    SELECT
        parent.id AS parent_id,
        parent.workspace_id,
        COALESCE(current_version.agents, parent.agents) AS agents
    FROM agent_preset AS parent
    LEFT JOIN agent_preset_version AS current_version
        ON current_version.workspace_id = parent.workspace_id
        AND current_version.id = parent.current_version_id
    WHERE parent.deleted_at IS NULL
),
refs AS (
    SELECT
        topology.parent_id,
        topology.workspace_id,
        ref.value AS ref
    FROM current_topology AS topology
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE
            WHEN jsonb_typeof(topology.agents -> 'subagents') = 'array'
                THEN topology.agents -> 'subagents'
            ELSE '[]'::jsonb
        END
    ) AS ref(value)
)
SELECT
    refs.parent_id,
    refs.workspace_id,
    CASE
        WHEN refs.ref ->> 'preset_id' IS NOT NULL THEN child_by_id.id
        WHEN child_by_slug.match_count = 1 THEN child_by_slug.child_id
        ELSE NULL
    END AS child_id,
    COALESCE(NULLIF(refs.ref ->> 'name', ''), refs.ref ->> 'preset') AS alias,
    refs.ref ->> 'description' AS description,
    CASE
        WHEN jsonb_typeof(refs.ref -> 'max_turns') = 'number'
            THEN (refs.ref ->> 'max_turns')::integer
        ELSE NULL
    END AS max_turns
FROM refs
LEFT JOIN agent_preset AS child_by_id
    ON child_by_id.workspace_id = refs.workspace_id
    AND child_by_id.deleted_at IS NULL
    AND child_by_id.id = CASE
        WHEN refs.ref ->> 'preset_id' ~*
            '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$'
            THEN (refs.ref ->> 'preset_id')::uuid
        ELSE NULL
    END
LEFT JOIN LATERAL (
    SELECT
        (array_agg(candidate.id ORDER BY candidate.id))[1] AS child_id,
        count(*) AS match_count
    FROM agent_preset AS candidate
    WHERE candidate.workspace_id = refs.workspace_id
        AND candidate.slug = refs.ref ->> 'preset'
        AND candidate.deleted_at IS NULL
) AS child_by_slug ON TRUE;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM _agent_preset_subagent_backfill
        WHERE child_id IS NULL OR alias IS NULL
    ) THEN
        RAISE EXCEPTION
            'Cannot normalize agent preset topology: unresolved or cross-workspace subagent head';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM _agent_preset_subagent_backfill
        GROUP BY workspace_id, parent_id, alias
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION
            'Cannot normalize agent preset topology: duplicate subagent alias';
    END IF;
END $$;

INSERT INTO agent_preset_subagent (
    id,
    parent_preset_id,
    child_preset_id,
    alias,
    description,
    max_turns,
    workspace_id
)
SELECT
    gen_random_uuid(),
    parent_id,
    child_id,
    alias,
    description,
    max_turns,
    workspace_id
FROM _agent_preset_subagent_backfill
ORDER BY parent_id, alias;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM agent_preset AS preset
        JOIN agent_preset_version_skill AS version_edge
            ON version_edge.preset_version_id = preset.current_version_id
        LEFT JOIN skill
            ON skill.id = version_edge.skill_id
        LEFT JOIN skill_version
            ON skill_version.id = version_edge.skill_version_id
        WHERE preset.deleted_at IS NULL
            AND (
                version_edge.workspace_id <> preset.workspace_id
                OR skill.id IS NULL
                OR skill.workspace_id <> preset.workspace_id
                OR skill.deleted_at IS NOT NULL
                OR skill.archived_at IS NOT NULL
                OR skill_version.id IS NULL
                OR skill_version.workspace_id <> preset.workspace_id
                OR skill_version.skill_id <> skill.id
            )
    ) THEN
        RAISE EXCEPTION
            'Cannot normalize agent preset topology: unresolved or cross-workspace skill head';
    END IF;
END $$;

DELETE FROM agent_preset_skill AS head_edge
USING agent_preset AS preset
WHERE preset.workspace_id = head_edge.workspace_id
    AND preset.id = head_edge.preset_id
    AND preset.deleted_at IS NULL
    AND preset.current_version_id IS NOT NULL
    AND NOT EXISTS (
        SELECT 1
        FROM agent_preset_version_skill AS version_edge
        WHERE version_edge.workspace_id = head_edge.workspace_id
            AND version_edge.preset_version_id = preset.current_version_id
            AND version_edge.skill_id = head_edge.skill_id
    );

INSERT INTO agent_preset_skill (
    id,
    preset_id,
    skill_id,
    skill_version_id,
    workspace_id
)
SELECT
    gen_random_uuid(),
    preset.id,
    version_edge.skill_id,
    version_edge.skill_version_id,
    preset.workspace_id
FROM agent_preset AS preset
JOIN agent_preset_version_skill AS version_edge
    ON version_edge.workspace_id = preset.workspace_id
    AND version_edge.preset_version_id = preset.current_version_id
WHERE preset.deleted_at IS NULL
ON CONFLICT (workspace_id, preset_id, skill_id)
DO UPDATE SET skill_version_id = EXCLUDED.skill_version_id;
