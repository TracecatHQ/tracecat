"use client"

import { ExternalLinkIcon, FileIcon, GitForkIcon, LockIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import { useMemo, useState } from "react"
import { Streamdown } from "streamdown"
import type { LibrarySkillDetailRead } from "@/client"
import { CreateAgentDialog } from "@/components/agents/create-agent-dialog"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { SkillFileTree } from "@/components/skills/file-tree"
import { SkillLibraryActionButton } from "@/components/skills/skill-library-action-button"
import { SkillLibraryProviderIcon } from "@/components/skills/skill-library-provider-icon"
import { Button } from "@/components/ui/button"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useLibrarySkill, useSkillLibrary } from "@/hooks/use-skill-library"
import { uniqueAgentPresetName } from "@/lib/agent-preset-name"
import { extractMarkdownFrontmatter } from "@/lib/markdown-frontmatter"
import {
  libraryProviderSlug,
  librarySkillTitle,
  librarySourceRepoUrl,
} from "@/lib/skill-library"
import { buildSkillFileTree, type VisibleFileEntry } from "@/lib/skills-studio"
import { useWorkspaceId } from "@/providers/workspace-id"

const SKILL_MANIFEST = "SKILL.md"

/**
 * Header actions for a library skill preview: fork, install, and uninstall.
 *
 * @param props Library skill slug.
 * @returns The action buttons.
 */
export function SkillLibrarySkillActions({ slug }: { slug: string }) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const canCreate = useScopeCheck("agent:create") === true
  const canDelete = useScopeCheck("agent:delete") === true
  // The header mounts by URL, before the page's read gate.
  const canRead = useScopeCheck("agent:read") === true
  const { librarySkill } = useLibrarySkill(workspaceId, slug, {
    enabled: canRead,
  })
  const {
    installLibrarySkill,
    installLibrarySkillIsPending,
    uninstallLibrarySkill,
    uninstallLibrarySkillIsPending,
    forkLibrarySkill,
    forkLibrarySkillIsPending,
  } = useSkillLibrary(workspaceId, { enabled: false })
  const isMutating =
    installLibrarySkillIsPending ||
    uninstallLibrarySkillIsPending ||
    forkLibrarySkillIsPending
  const [agentDraft, setAgentDraft] = useState<{
    name: string
    takenSlugs: string[]
  } | null>(null)
  const { refetchPresets } = useAgentPresets(workspaceId, { enabled: false })

  if (!librarySkill) return null
  const skill = librarySkill

  async function handleCreateAgent() {
    try {
      // Agents can bind only installed library skills.
      if (!skill.installed) await installLibrarySkill(slug)
      const { data: presets } = await refetchPresets()
      const takenSlugs = (presets ?? []).map((preset) => preset.slug)
      setAgentDraft({
        name: uniqueAgentPresetName(librarySkillTitle(slug), takenSlugs),
        takenSlugs,
      })
    } catch {
      // The mutation hook reports failures.
    }
  }
  const onCreateAgent = canCreate ? handleCreateAgent : undefined

  async function run(action: () => Promise<unknown>) {
    try {
      await action()
    } catch {
      // The mutation hook reports failures.
    }
  }

  async function handleFork() {
    try {
      const skill = await forkLibrarySkill(slug)
      router.push(`/workspaces/${workspaceId}/skills/${skill.id}`)
    } catch {
      // The mutation hook reports failures.
    }
  }

  return (
    <div className="flex items-center gap-2">
      {canCreate ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={isMutating}
          onClick={handleFork}
        >
          <GitForkIcon className="mr-1 size-3.5" />
          Fork
        </Button>
      ) : null}
      {librarySkill.installed && canDelete ? (
        <SkillLibraryActionButton
          label="Uninstall"
          disabled={isMutating}
          onAction={() => run(() => uninstallLibrarySkill(slug))}
          onCreateAgent={onCreateAgent}
          createAgentLabel="Create agent with this skill"
        />
      ) : null}
      {librarySkill.installed && !canDelete && onCreateAgent ? (
        <SkillLibraryActionButton
          label="Create agent"
          disabled={isMutating}
          onAction={onCreateAgent}
          createAgentLabel="Create agent with this skill"
        />
      ) : null}
      {!librarySkill.installed && canCreate ? (
        <SkillLibraryActionButton
          label="Install"
          disabled={isMutating}
          onAction={() => run(() => installLibrarySkill(slug))}
          onCreateAgent={onCreateAgent}
          createAgentLabel="Create agent with this skill"
        />
      ) : null}
      <CreateAgentDialog
        open={agentDraft !== null}
        onOpenChange={(open) => {
          if (!open) setAgentDraft(null)
        }}
        librarySkills={[slug]}
        defaultName={agentDraft?.name}
        takenSlugs={agentDraft?.takenSlugs}
      />
    </div>
  )
}

/**
 * Read-only preview of one library skill: its files, source, frontmatter, and
 * instructions.
 *
 * @param props Library skill slug.
 * @returns The preview page.
 */
export function SkillLibrarySkillView({ slug }: { slug: string }) {
  const workspaceId = useWorkspaceId()
  const { librarySkill, librarySkillIsLoading, librarySkillError } =
    useLibrarySkill(workspaceId, slug)
  const [selectedPath, setSelectedPath] = useState(SKILL_MANIFEST)
  const nodes = useMemo(
    () =>
      buildSkillFileTree(
        (librarySkill?.files ?? []).map(
          (file): VisibleFileEntry => ({
            path: file.path,
            contentType: "",
            sizeBytes: file.size_bytes,
            change: null,
            isNew: false,
          })
        )
      ),
    [librarySkill]
  )

  if (librarySkillIsLoading) {
    return <CenteredSpinner />
  }

  if (librarySkillError || !librarySkill) {
    return (
      <AlertNotification
        level="error"
        message={
          librarySkillError
            ? `Error loading library skill: ${librarySkillError.message}`
            : "This skill is not in the skill library."
        }
      />
    )
  }

  const selected =
    librarySkill.files.find((file) => file.path === selectedPath) ??
    librarySkill.files.find((file) => file.path === SKILL_MANIFEST)

  return (
    <div className="flex size-full min-h-0">
      <aside className="flex w-60 shrink-0 flex-col border-r">
        <div className="flex h-8 items-center border-b px-3 text-xs text-muted-foreground">
          Files
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-1">
          <SkillFileTree
            nodes={nodes}
            selectedPath={selected?.path ?? null}
            onSelectPath={setSelectedPath}
          />
        </div>
      </aside>
      <main className="flex min-w-0 flex-1 flex-col">
        <div className="flex h-8 items-center justify-between border-b px-3 text-xs">
          <span className="flex items-center gap-1.5 font-light">
            <FileIcon className="size-3.5 text-muted-foreground" />
            {selected?.path}
          </span>
          <span className="flex items-center gap-1 text-muted-foreground">
            <LockIcon className="size-3" />
            Read-only · fork to edit
          </span>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-4">
          <div className="mx-auto flex max-w-[1024px] flex-col gap-4">
            {selected?.path === SKILL_MANIFEST ? (
              <SkillManifestPreview
                skill={librarySkill}
                markdown={selected.content ?? ""}
              />
            ) : (
              <FilePreview content={selected?.content ?? null} />
            )}
          </div>
        </div>
      </main>
    </div>
  )
}

function SkillManifestPreview({
  skill,
  markdown,
}: {
  skill: LibrarySkillDetailRead
  markdown: string
}) {
  const parsed = extractMarkdownFrontmatter(markdown)
  const source = skill.source
  const sourceName = source?.group ?? source?.provider ?? "Tracecat"
  return (
    <>
      {source ? (
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-medium">Source</h3>
          <div className="flex items-center gap-3.5 rounded-lg border p-4">
            <SkillLibraryProviderIcon
              providerSlug={libraryProviderSlug(sourceName)}
              className="size-12 rounded-lg p-2"
            />
            <div className="flex min-w-0 flex-col gap-1">
              <span className="text-sm font-semibold">{sourceName}</span>
              {source.repo ? (
                <a
                  href={source.url ?? librarySourceRepoUrl(source.repo)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs text-muted-foreground hover:underline"
                >
                  {source.repo}
                  <ExternalLinkIcon className="size-3" />
                </a>
              ) : null}
              {!source.repo && source.group && source.provider ? (
                <span className="text-xs text-muted-foreground">
                  Maintained by {source.provider}
                </span>
              ) : null}
            </div>
          </div>
        </section>
      ) : null}
      <section className="flex flex-col gap-2">
        <div>
          <h3 className="text-sm font-medium">Frontmatter</h3>
          <p className="text-xs text-muted-foreground">
            Metadata the agent uses to discover this skill.
          </p>
        </div>
        <dl className="grid grid-cols-[120px_minmax(0,1fr)] rounded-md border text-xs">
          <dt className="px-3 py-2 text-muted-foreground">name</dt>
          <dd className="px-3 py-2 font-mono">{skill.slug}</dd>
          <dt className="border-t px-3 py-2 text-muted-foreground">
            description
          </dt>
          <dd className="border-t px-3 py-2 leading-relaxed">
            {skill.description ?? parsed?.description ?? ""}
          </dd>
        </dl>
      </section>
      <section className="flex flex-col gap-2">
        <div>
          <h3 className="text-sm font-medium">Instructions</h3>
          <p className="text-xs text-muted-foreground">
            Workflow, examples, and references the agent loads when the skill is
            triggered.
          </p>
        </div>
        <article className="rounded-md border px-5 py-4 text-sm">
          <Streamdown>{parsed?.body ?? markdown}</Streamdown>
        </article>
      </section>
    </>
  )
}

function FilePreview({ content }: { content: string | null }) {
  if (content === null) {
    return (
      <p className="text-sm text-muted-foreground">
        Binary file; no preview available.
      </p>
    )
  }
  return (
    <pre className="overflow-x-auto rounded-md border bg-muted/30 p-3 font-mono text-xs leading-relaxed">
      {content}
    </pre>
  )
}
