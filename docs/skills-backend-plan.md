# Backend Plan: Workspace Skill Registry for Agent Presets

## Summary

Build skills as a new workspace-scoped backend capability attached to `AgentPreset`, not to sessions or a new agent model. Skills get their own draft/publish lifecycle; presets keep their current lifecycle. A preset update that changes skill bindings still creates a new immutable preset version immediately, and that preset version snapshots exact skill versions so sessions remain reproducible.

Storage is object-storage-backed from day one, but not as per-version tarballs. Use content-addressed blobs plus DB manifests. That keeps large user uploads safe in object storage, allows in-platform text editing, avoids copying unchanged files on publish, and gives the executor exact file trees to materialize into the Claude sandbox.

## Key Changes

### Data model

Add new singular tables:

- `skill`
  - Workspace-scoped logical skill.
  - Fields: `id`, `workspace_id`, `slug`, `current_version_id`, `draft_revision`, cached `title`, cached `description`, timestamps, optional archived marker.
  - `slug` is the stable identifier and filesystem directory name under `.claude/skills`.

- `skill_blob`
  - Workspace-scoped content-addressed file metadata.
  - Fields: `id`, `workspace_id`, `sha256`, `bucket`, `key`, `size_bytes`, `content_type`, timestamps.
  - Unique on `(workspace_id, sha256)`.

- `skill_upload`
  - Ephemeral per-file upload session for staged direct uploads.
  - Fields: `id`, `workspace_id`, `skill_id`, `sha256`, `size_bytes`, `content_type`, `bucket`, `key`, `expires_at`, `created_by`, `completed_at`.
  - Internal upload primitive for direct-to-object-storage flows. Not a whole-skill snapshot and not the primary product-facing import API.

- `skill_draft_file`
  - Mutable draft manifest.
  - Fields: `id`, `workspace_id`, `skill_id`, `path`, `blob_id`, timestamps.
  - Unique on `(skill_id, path)`.

- `skill_version`
  - Immutable published snapshot.
  - Fields: `id`, `workspace_id`, `skill_id`, `version`, `manifest_sha256`, `file_count`, `total_size_bytes`, cached `title`, cached `description`, timestamps.
  - Unique on `(skill_id, version)`.

- `skill_version_file`
  - Immutable published manifest rows.
  - Fields: `id`, `workspace_id`, `skill_version_id`, `path`, `blob_id`, timestamps.
  - Unique on `(skill_version_id, path)`.

- `agent_preset_skill`
  - Mutable preset-head skill bindings.
  - Association table rather than a direct field on `AgentPreset` because the relationship carries skill-version metadata, not just membership.
  - Fields: `preset_id`, `skill_id`, `skill_version_id`, timestamps.
  - Required metadata is intentionally minimal: the selected exact published `SkillVersion` and timestamps.
  - Do not add cached skill metadata here; title/description stay on `Skill` and `SkillVersion`.

- `agent_preset_version_skill`
  - Immutable preset-version snapshot of exact skill versions.
  - Association table rather than a direct field on `AgentPresetVersion` because each attached skill resolves to its own exact `skill_version_id`.
  - Fields: `preset_version_id`, `skill_id`, `skill_version_id`, timestamps.

Add `current_version_id`-style relationships on `Skill` for consistency with `AgentPreset`.

### Skill lifecycle and validation

Skill draft is mutable; skill versions are immutable.

- Creating a skill seeds a draft with a default root `SKILL.md`.
- Draft edits do not create versions.
- Publish validates and snapshots the current draft into `skill_version`.
- Restore copies a historical `skill_version` back into the draft, increments `draft_revision`, and does not repoint `current_version_id` by itself. A new publish is required.

Publish rules:

- Root `SKILL.md` must exist.
- Paths must be normalized relative POSIX paths with no absolute paths, `..`, or duplicate normalized paths.
- Full spec is supported: `SKILL.md`, `references/`, `scripts/`, `assets/`, plus arbitrary additional relative files.
- Cached `title` and `description` come from parsed `SKILL.md` frontmatter.
- Draft may be invalid; publish fails with structured validation errors.
- Only published skills can be attached to presets.

Use optimistic concurrency on draft edits with `draft_revision`, modeled after workflow `graph_version`.

### Public backend API

Add a new workspace API under `/agent/skills`:

- `GET /agent/skills`
  - Cursor-paginated list of skills.
- `POST /agent/skills`
  - Create logical skill and seeded draft.
- `POST /agent/skills:upload`
  - Primary one-shot import API for local skill directories.
  - Accepts a file tree payload, creates the logical skill, writes the full draft manifest, and does not publish.
  - Fails with `409 Conflict` if the slug already exists in the workspace.
  - This is the default UI/local-agent entrypoint; clients should not need to orchestrate per-file draft mutations themselves.
- `GET /agent/skills/{skill_id}`
  - Skill summary, current published version info, draft status.
- `GET /agent/skills/{skill_id}/draft`
  - Draft manifest, `draft_revision`, publishability, validation errors.
- `GET /agent/skills/{skill_id}/draft/file?path=...`
  - For text files: return inline UTF-8 content.
  - For binary or large files: return metadata plus presigned download URL.
- `PATCH /agent/skills/{skill_id}/draft`
  - `base_revision` plus draft operations.
  - Operations:
    - `upsert_text_file`
    - `attach_uploaded_blob`
    - `delete_file`
- `POST /agent/skills/{skill_id}/draft/uploads`
  - Create a per-file upload session and return presigned upload details for large or binary files.
  - Advanced/internal primitive for direct-to-object-storage flows. Keep this available for large uploads and future MCP-style staged transfer, but do not make it the default product path.
- `POST /agent/skills/{skill_id}/publish`
  - Publish current draft into a new immutable version.
- `GET /agent/skills/{skill_id}/versions`
  - Cursor-paginated version list.
- `GET /agent/skills/{skill_id}/versions/{version_id}`
  - Published version manifest.
- `POST /agent/skills/{skill_id}/versions/{version_id}/restore`
  - Replace draft with the selected published snapshot.
- `DELETE /agent/skills/{skill_id}`
  - Archive logical skill. Block if it is still attached to any preset head.

Upload behavior:

- Primary UX is a server-side `upload_skill()`-style import that accepts a full file tree, creates the skill, and writes the draft in one operation.
- Text edits from the in-platform editor continue to go through the draft API; backend writes blobs and updates the draft manifest.
- Large text and binary files may use staged per-file upload sessions plus presigned object-storage upload, then get attached to the draft by `attach_uploaded_blob`.
- Drag-and-drop folder upload in the UI should be a convenience layer over the same server-side import flow, not a different storage model.
- Add a new skills bucket config, separate from registry and attachments.

### Agent preset integration

Extend preset schemas and service behavior so `AgentPreset` and `AgentPresetVersion` expose skill bindings.

Add public schema fields:

- `AgentPresetCreate.skills`
- `AgentPresetUpdate.skills`
- `AgentPresetRead.skills`
- `AgentPresetVersionRead.skills`

Binding shape:

- `skill_id`
- `skill_version_id`

Association semantics:

- `AgentPreset` conceptually has many skills, but the relationship is not a plain many-to-many because each binding carries the selected `SkillVersion`.
- `agent_preset_skill` is the mutable-head representation of that selection.
- `agent_preset_version_skill` is the immutable execution snapshot after those exact versions have been copied into a preset version.
- If ordering becomes important later, add an explicit `position` field to the link table rather than overloading the skill slug or relying on insertion order.

Preset behavior:

- Changing `skills` on the preset head counts as an execution-changing mutation and creates a new `AgentPresetVersion` immediately.
- Invariant: every `AgentPresetVersion` stores exact `skill_version_id` values for all bound skills.
- The mutable preset head also stores exact `skill_version_id` values. Preset version creation snapshots those exact refs without any extra resolution step.
- Restoring a preset version copies those exact skill-version bindings back to the preset head.
- Version compare should include skill binding changes.
- Execution always resolves from `AgentPresetVersion`, never from mutable preset-head bindings.

### Runtime and sandbox integration

Add internal resolved skill refs to the execution path.

- Extend internal agent config payloads with resolved preset-version skill refs so the executor can materialize them before spawn.
- Do not rely on the sandbox runtime to resolve DB state.

Executor behavior:

- Resolve `AgentPresetVersion` to exact `SkillVersion` snapshots.
- Materialize each skill into a per-run staging directory under the outer executor job dir.
- Add worker-local extracted-skill cache keyed by `skill_version.manifest_sha256`.
- Download/write files in sorted path order for deterministic directory contents.

Sandbox/direct-mode behavior:

- Extend `build_agent_nsjail_config` and spawn plumbing to accept a staged `skills_dir`.
- Bind-mount staged skills read-only to `/home/agent/.claude/skills`.
- In direct mode, use a per-run `HOME` directory and expose the same `.claude/skills` tree there for parity.

Claude runtime changes:

- Stop disallowing the `Skill` tool.
- Set `ClaudeAgentOptions.setting_sources=["user"]`.
- Leave filesystem tools unchanged.
- This work lands in the existing agent runtime/executor path, not in a new sidecar.

## Test Plan

- Unit tests for `SkillService`
  - create skill seeds default draft
  - `upload_skill` creates a draft and fails on slug conflict
  - draft patch enforces `draft_revision`
  - path normalization rejects traversal and duplicates
  - publish requires root `SKILL.md`
  - publish snapshots draft rows into immutable version rows
  - restore copies published version back into draft
  - archive blocks when preset head still references the skill

- Unit tests for upload/blob handling
  - text edit writes/reuses content-addressed blobs
  - upload session returns presigned details and finalization binds uploaded blob
  - duplicate blob uploads dedupe by `(workspace_id, sha256)`

- Unit tests for `AgentPresetService`
  - create/update validates skill bindings
  - every preset version stores exact `skill_version_id` values
  - preset version snapshot copies the mutable head's exact skill versions
  - restore preset version copies historical skill versions back onto the mutable head
  - version diff includes skill binding changes

- Unit tests for runtime plumbing
  - executor stages resolved skills into the expected directory shape
  - cache hit skips blob downloads
  - nsjail config includes read-only skills mount
  - direct mode uses per-run HOME with `.claude/skills`
  - Claude runtime options include `setting_sources=["user"]` and no longer block `Skill`

- Integration tests
  - MinIO-backed publish of skill with text files plus binary asset
  - attach skill to preset, resolve preset version, and materialize exact skill tree in execution
  - direct-mode execution on macOS/CI remains functional with staged skills

## Assumptions and Defaults

- Workspace scope only in this phase.
- Full skill spec is supported, including `assets/`.
- Skill editing uses draft/publish; preset lifecycle stays as-is.
- `AgentPresetVersion` is the execution boundary and always snapshots exact `SkillVersion` rows.
- Presets attach only to published skill versions; drafts are never executable.
- Restoring a skill version restores into draft; restoring a preset version repoints current preset state immediately, matching current preset behavior.
- Default import UX is a one-shot `upload_skill()` flow that creates a draft and rejects clashing slugs.
- Add a dedicated skills blob bucket/config rather than reusing the registry bucket.

## Implementation Appendix

This section is intentionally concrete. It is not a redesign; it is the set of defaults to implement unless code discovery uncovers a strong reason to deviate.

### API contract

- Keep `POST /agent/skills` as the minimal API to create an empty logical skill with a seeded `SKILL.md` draft.
- Add `POST /agent/skills:upload` as the primary import endpoint for local directory upload.
- `POST /agent/skills:upload` should accept a multipart request with:
  - scalar fields: `slug`, optional `title`, optional `description`
  - repeated file parts annotated with normalized relative paths
- Default upload mode is `replace` because the endpoint creates a new skill and fails on slug conflict.
- Upload does not publish.
- Upload should be atomic at the product level: if any file fails validation or storage finalization, no `Skill` or draft manifest should remain committed.
- Return payload should include:
  - `skill_id`
  - `slug`
  - `draft_revision`
  - `file_count`
  - `publishable`
  - `validation_errors`

### Draft validation behavior

- Upload should allow an invalid draft only if that meaningfully improves UX.
- Default behavior for v1:
  - require a root `SKILL.md` in `POST /agent/skills:upload`
  - validate all paths during upload
  - allow non-blocking content validation failures to surface as draft validation errors rather than hard request failure
- Publish remains the hard gate for executable state.

### Path normalization rules

- Normalize all uploaded paths to relative POSIX paths before any writes.
- Reject:
  - absolute paths
  - empty paths
  - paths containing `..`
  - duplicate paths after normalization
  - reserved paths if we later define any
- Preserve case exactly as uploaded; do not lowercase paths.

### Database constraints and indexes

- `skill.slug` should be unique per workspace.
- `skill_draft_file` should have unique `(skill_id, path)`.
- `skill_version_file` should have unique `(skill_version_id, path)`.
- `agent_preset_skill` should have unique `(preset_id, skill_id)`.
- `agent_preset_version_skill` should have unique `(preset_version_id, skill_id)`.
- Add indexes for:
  - `skill(workspace_id, slug)`
  - `skill_version(skill_id, version desc)` or equivalent access path
  - `skill_blob(workspace_id, sha256)`

### Storage layout

- Use a dedicated skills bucket.
- Store canonical blobs under a content-addressed prefix, for example:
  - `skills/blobs/{workspace_id}/{sha256}`
- Store staged uploads under a separate temporary prefix, for example:
  - `skills/uploads/{workspace_id}/{skill_id}/{upload_id}`
- Do not make object keys user-derived beyond scoped prefixes.
- `skill_blob.key` should always point at canonical blob storage, not temporary upload keys.

### Upload finalization and dedupe

- During upload/import:
  - hash each file
  - write or promote to canonical blob storage
  - reuse existing `skill_blob` row when `(workspace_id, sha256)` already exists
- `skill_upload` remains the low-level primitive for staged direct uploads and future remote/MCP-friendly flows.
- `POST /agent/skills:upload` may internally bypass `skill_upload` if the server is directly handling multipart file bodies.

### Transaction boundaries

- Treat skill creation plus draft manifest write as one logical transaction.
- Recommended sequence:
  1. Validate slug and paths.
  2. Stage file bytes to object storage.
  3. Open DB transaction.
  4. Insert `Skill`.
  5. Upsert/reuse `skill_blob` rows.
  6. Insert `skill_draft_file` rows.
  7. Commit.
- If DB commit fails after objects were written, leave unreferenced objects for async garbage collection rather than attempting fragile inline cleanup.

### Publish behavior

- Publish should run in one transaction over DB metadata.
- Publish steps:
  1. Validate draft manifest.
  2. Compute manifest hash deterministically from sorted `(path, blob_sha256)` entries.
  3. Insert `skill_version`.
  4. Insert `skill_version_file` rows.
  5. Set `skill.current_version_id`.
  6. Commit.
- If the computed manifest hash already matches an existing version for the same skill, prefer returning the existing version instead of creating duplicate snapshots.

### Preset resolution behavior

- Mutable preset head stores exact `skill_version_id` bindings in `agent_preset_skill`.
- Version creation copies those exact `skill_version_id` bindings into `agent_preset_version_skill`.
- Restoring a preset version copies those same exact refs back to the mutable head.

### Runtime materialization

- Stage each resolved skill under:
  - `{job_dir}/skills/{skill_slug}/...`
- Bind mount the parent skills directory read-only to:
  - `/home/agent/.claude/skills`
- Materialize files in sorted path order.
- Cache extracted skill trees by `skill_version.manifest_sha256`.
- Cache contents are an optimization only; cache misses must always be recoverable from blob storage plus DB manifests.

### Cleanup and garbage collection

- Expire incomplete `skill_upload` rows with a background cleanup job.
- Delete temporary upload objects whose sessions expired or were never finalized.
- Do not eagerly delete `skill_blob` rows on draft mutation.
- Add a deferred garbage-collection path for unreferenced canonical blobs once reference counting or reachability scanning is implemented.
