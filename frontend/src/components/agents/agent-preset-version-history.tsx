"use client"

import { Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type {
  AgentPresetCreate,
  AgentPresetSkillBindingRead,
  AgentPresetVersionReadMinimal,
} from "@/client"
import {
  formatAgentPresetVersionLabel,
  getAgentPresetVersionNumber,
} from "@/components/agents/agent-preset-version-select"
import { toast } from "@/components/ui/use-toast"
import type {
  VersionedDocumentDescriptor,
  VersionFileDiffState,
  VersionFileFingerprint,
  VersionHistoryEntry,
} from "@/components/version-history/types"
import { VersionDiffBody } from "@/components/version-history/version-diff-body"
import { VersionHistoryMenu } from "@/components/version-history/version-history-menu"
import {
  useAgentPreset,
  useAgentPresetVersion,
  useAgentPresetVersions,
  useRestoreAgentPresetVersion,
} from "@/hooks/use-agent-presets"
import { useSkills } from "@/hooks/use-skills"
import {
  AGENT_PRESET_CONFIG_PATH,
  AGENT_PRESET_INSTRUCTIONS_PATH,
  agentPresetPayloadToDocumentInput,
  agentPresetVersionToDocumentInput,
  buildAgentPresetVirtualFiles,
} from "@/lib/agent-preset-document"
import { compareVersionManifests } from "@/lib/version-diff"

/** Shown in place of the diff when the draft form cannot be serialized. */
const DRAFT_UNAVAILABLE_MESSAGE = "Fix form errors before comparing versions."

/** Props for {@link AgentPresetVersionHistory}. */
export type AgentPresetVersionHistoryProps = {
  /** Workspace the preset belongs to. */
  workspaceId: string
  /** Preset whose version history is shown. */
  presetId: string
  /** Version the preset currently points at, or null if never published. */
  currentVersionId: string | null
  /** Snapshots the draft as an API payload. Null when the form can't serialize. */
  getDraftPayload: () => AgentPresetCreate | null
  /** Disables the trigger button. */
  disabled?: boolean
}

/**
 * Renders a preset's two virtual files as fingerprint entries. For presets the
 * fingerprint IS the serialized file text — both sides are computed locally, so
 * equality checks cost nothing and no file bodies are ever fetched.
 */
function toManifest(files: {
  instructions: string
  config: string
}): VersionFileFingerprint[] {
  return [
    { path: AGENT_PRESET_INSTRUCTIONS_PATH, fingerprint: files.instructions },
    { path: AGENT_PRESET_CONFIG_PATH, fingerprint: files.config },
  ]
}

/** Picks one virtual file's text by path. */
function pickFileContent(
  files: { instructions: string; config: string },
  path: string
): string | null {
  if (path === AGENT_PRESET_INSTRUCTIONS_PATH) {
    return files.instructions
  }
  if (path === AGENT_PRESET_CONFIG_PATH) {
    return files.config
  }
  return null
}

/** Props for the private diff content mounted inside the restore dialog. */
type AgentPresetVersionDiffContentProps = {
  workspaceId: string
  presetId: string
  /** Version chosen in the dropdown; the mount is keyed on it by the shell. */
  versionId: string
  currentVersionId: string | null
  /** Minimal version list, used only to resolve version numbers for labels. */
  versions: AgentPresetVersionReadMinimal[] | undefined
  /**
   * The CURRENT preset head's skill bindings keyed by `skill_id`. The draft
   * form tracks only skill ids, so the draft side of the diff resolves each
   * skill's pinned version from here; a skill missing from the map was just
   * attached in the form and has no pin yet.
   */
  headSkillBindings: ReadonlyMap<string, AgentPresetSkillBindingRead>
  getDraftPayload: () => AgentPresetCreate | null
  /** Reports whether the draft snapshot is missing, to gate the Restore action. */
  onDraftMissingChange: (draftMissing: boolean) => void
}

/**
 * Dialog body: fetches the selected version, serializes both sides through the
 * shared preset-document normalizer, and diffs them. Mounted only while the
 * dialog is open, inside the shell's `key={versionId}` boundary, so all state
 * here is per-(open, version).
 */
function AgentPresetVersionDiffContent({
  workspaceId,
  presetId,
  versionId,
  currentVersionId,
  versions,
  headSkillBindings,
  getDraftPayload,
  onDraftMissingChange,
}: AgentPresetVersionDiffContentProps) {
  // Snapshot the draft exactly once per mount via the lazy initializer. The
  // dialog is modal, so the form cannot change underneath it, and this avoids
  // a `useWatch` that would re-render the instructions editor on every
  // keystroke just to keep a live diff nobody can see move.
  const [draftPayload] = useState(getDraftPayload)
  const draftMissing = draftPayload === null

  // Report draft availability upward so the shell's Restore action can be
  // disabled. An effect (not a render-time call) because it sets parent state.
  useEffect(() => {
    onDraftMissingChange(draftMissing)
  }, [draftMissing, onDraftMissingChange])

  const { presetVersion, presetVersionIsLoading, presetVersionError } =
    useAgentPresetVersion(workspaceId, presetId, versionId, {
      enabled: !draftMissing,
    })
  const { skills } = useSkills(draftMissing ? undefined : workspaceId)

  const [selectedPath, setSelectedPath] = useState<string | null>(null)

  const skillNamesById = useMemo(
    () =>
      new Map((skills ?? []).map((skill) => [skill.id, skill.name] as const)),
    [skills]
  )

  const draftFiles = useMemo(
    () =>
      draftPayload
        ? buildAgentPresetVirtualFiles(
            agentPresetPayloadToDocumentInput(
              draftPayload,
              skillNamesById,
              headSkillBindings
            )
          )
        : null,
    [draftPayload, skillNamesById, headSkillBindings]
  )
  const versionFiles = useMemo(
    () =>
      presetVersion
        ? buildAgentPresetVirtualFiles(
            agentPresetVersionToDocumentInput(
              presetVersion,
              draftPayload ?? { agents: undefined, skills: [] },
              skillNamesById,
              headSkillBindings
            )
          )
        : null,
    [presetVersion, draftPayload, skillNamesById, headSkillBindings]
  )

  const files = useMemo(() => {
    if (!draftFiles || !versionFiles) {
      return []
    }
    return compareVersionManifests(
      toManifest(draftFiles),
      toManifest(versionFiles)
    )
  }, [draftFiles, versionFiles])

  // Land the user on the first file the restore would actually change,
  // falling back to the instructions when nothing differs.
  const effectiveSelectedPath =
    selectedPath ??
    files.find((file) => file.status !== "unchanged")?.path ??
    AGENT_PRESET_INSTRUCTIONS_PATH

  const diff = useMemo<VersionFileDiffState | null>(() => {
    if (!draftFiles || !versionFiles) {
      return null
    }
    // Direction is fixed: `oldValue` is the current draft and `newValue` is
    // the selected version, so highlighted text is what restoring brings back
    // and struck-through text is draft content that would be lost.
    return {
      path: effectiveSelectedPath,
      oldValue: pickFileContent(draftFiles, effectiveSelectedPath),
      newValue: pickFileContent(versionFiles, effectiveSelectedPath),
    }
  }, [draftFiles, versionFiles, effectiveSelectedPath])

  const versionLabel = formatAgentPresetVersionLabel({
    currentVersionNumber: getAgentPresetVersionNumber(
      versions,
      currentVersionId
    ),
    selectedVersionNumber: getAgentPresetVersionNumber(versions, versionId),
    currentVersionId,
    selectedVersionId: versionId,
  })

  if (draftMissing) {
    return (
      <VersionDiffBody
        files={[]}
        selectedPath={null}
        onSelectPath={setSelectedPath}
        diff={null}
        draftLabel="Current draft"
        versionLabel={versionLabel}
        message={DRAFT_UNAVAILABLE_MESSAGE}
      />
    )
  }

  if (presetVersionError) {
    return (
      <VersionDiffBody
        files={[]}
        selectedPath={null}
        onSelectPath={setSelectedPath}
        diff={null}
        draftLabel="Current draft"
        versionLabel={versionLabel}
        message="Failed to load this version."
      />
    )
  }

  if (presetVersionIsLoading || !versionFiles) {
    return (
      <div className="flex min-h-0 items-center justify-center gap-2 rounded-md border px-4 py-8 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading version…
      </div>
    )
  }

  return (
    <VersionDiffBody
      files={files}
      selectedPath={effectiveSelectedPath}
      onSelectPath={setSelectedPath}
      diff={diff}
      draftLabel="Current draft"
      versionLabel={versionLabel}
      pinnedPaths={[AGENT_PRESET_INSTRUCTIONS_PATH, AGENT_PRESET_CONFIG_PATH]}
    />
  )
}

/**
 * Version history for an agent preset: wires the preset's versions, restore
 * mutation, and virtual-file diff into the document-agnostic
 * {@link VersionHistoryMenu} shell. All react-query lives here; the shell stays
 * presentation-only.
 */
export function AgentPresetVersionHistory({
  workspaceId,
  presetId,
  currentVersionId,
  getDraftPayload,
  disabled,
}: AgentPresetVersionHistoryProps) {
  const { preset } = useAgentPreset(workspaceId, presetId)
  const { versions, versionsIsLoading, versionsError } = useAgentPresetVersions(
    workspaceId,
    presetId
  )
  const { restoreAgentPresetVersion } =
    useRestoreAgentPresetVersion(workspaceId)

  // Surface a version-list failure once per error instance. Keyed on the error
  // object so re-renders don't re-toast, but a fresh failure after a refetch
  // does. Deliberately no throw and no navigation: the dropdown shows its own
  // inline error state and the rest of the builder stays usable.
  useEffect(() => {
    if (!versionsError) {
      return
    }
    toast({
      title: "Couldn't load version history",
      description: "Please try again later.",
      variant: "destructive",
    })
  }, [versionsError])

  // The current head's skill bindings, keyed by skill id. The draft side of
  // the diff resolves its skill version pins from these.
  const headSkillBindings = useMemo(
    () =>
      new Map(
        (preset?.skills ?? []).map(
          (binding) => [binding.skill_id, binding] as const
        )
      ),
    [preset?.skills]
  )

  // Whether the last-opened diff found the draft unserializable. Reported by
  // the dialog content on mount — the outer component never calls
  // `getDraftPayload()` itself, so the builder header renders without ever
  // serializing the form. Stale-true between dialogs is harmless: the Restore
  // action it gates only exists while a dialog is open, and each open remounts
  // the content, which re-reports.
  const [restoreDisabled, setRestoreDisabled] = useState(false)

  const entries = useMemo<VersionHistoryEntry[]>(
    () =>
      [...(versions ?? [])]
        .sort((left, right) => right.version - left.version)
        .map((version) => ({
          id: version.id,
          // `currentVersionId` is intentionally not passed: the shell renders
          // its own "Current" marker, so the label must not repeat it.
          label: formatAgentPresetVersionLabel({
            selectedVersionNumber: version.version,
            selectedVersionId: version.id,
          }),
          createdAt: version.created_at,
          isCurrent: version.id === currentVersionId,
        })),
    [versions, currentVersionId]
  )

  const documentDescriptor = useMemo<VersionedDocumentDescriptor>(
    () => ({
      entityLabel: "agent",
      name: preset?.name ?? "this agent",
      currentVersionId,
    }),
    [preset?.name, currentVersionId]
  )

  async function handleRestore(versionId: string) {
    // Rejections propagate to the shell, which keeps the dialog open; the
    // mutation's own onError already surfaces a toast.
    await restoreAgentPresetVersion({ presetId, versionId })
  }

  return (
    <VersionHistoryMenu
      document={documentDescriptor}
      entityLabel="agent"
      versions={entries}
      isLoading={versionsIsLoading}
      loadError={Boolean(versionsError)}
      onRestore={handleRestore}
      disabled={disabled}
      restoreDisabled={restoreDisabled}
      renderVersionDiff={(versionId) => (
        <AgentPresetVersionDiffContent
          workspaceId={workspaceId}
          presetId={presetId}
          versionId={versionId}
          currentVersionId={currentVersionId}
          versions={versions}
          headSkillBindings={headSkillBindings}
          getDraftPayload={getDraftPayload}
          onDraftMissingChange={setRestoreDisabled}
        />
      )}
    />
  )
}
