"use client"

import {
  ClipboardCheck,
  KeyRound,
  LibraryIcon,
  Pyramid,
  Radar,
  ScanSearch,
  SearchIcon,
} from "lucide-react"
import Link from "next/link"
import { type ComponentType, useMemo, useState } from "react"
import { CreateAgentDialog } from "@/components/agents/create-agent-dialog"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { SkillLibraryActionButton } from "@/components/skills/skill-library-action-button"
import { SkillLibraryProviderIcon } from "@/components/skills/skill-library-provider-icon"
import { SkillLibraryUninstallDialog } from "@/components/skills/skill-library-uninstall-dialog"
import { Badge } from "@/components/ui/badge"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useSkillLibrary } from "@/hooks/use-skill-library"
import { uniqueAgentPresetName } from "@/lib/agent-preset-name"
import {
  groupLibraryProviders,
  type LibraryProvider,
  type LibrarySkillCategory,
  libraryEntryPath,
  libraryMaintainer,
  libraryProviderMatches,
  libraryProviderSlug,
  librarySkillCategory,
  librarySkillTitle,
} from "@/lib/skill-library"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Render the skill library grouped by provider, split into installed and
 * available sections.
 *
 * @returns The skill library view.
 */
export function SkillLibraryView() {
  const workspaceId = useWorkspaceId()
  const canCreate = useScopeCheck("agent:create") === true
  const canDelete = useScopeCheck("agent:delete") === true
  const [query, setQuery] = useState("")
  const [pendingUninstall, setPendingUninstall] =
    useState<LibraryProvider | null>(null)
  const [agentDraft, setAgentDraft] = useState<{
    name: string
    slugs: string[]
    takenSlugs: string[]
  } | null>(null)
  const { refetchPresets } = useAgentPresets(workspaceId, { enabled: false })
  const {
    librarySkills,
    librarySkillsIsLoading,
    librarySkillsError,
    installLibrarySkills,
    installLibrarySkillsIsPending,
    uninstallLibrarySkills,
    uninstallLibrarySkillsIsPending,
  } = useSkillLibrary(workspaceId)
  const isMutating =
    installLibrarySkillsIsPending || uninstallLibrarySkillsIsPending

  const providers = useMemo(
    () => groupLibraryProviders(librarySkills ?? []),
    [librarySkills]
  )
  const normalizedQuery = query.trim().toLowerCase()
  const matches = providers.filter((provider) =>
    libraryProviderMatches(provider, normalizedQuery)
  )
  const installed = matches.filter((provider) => provider.installedCount > 0)
  const available = matches.filter((provider) => provider.installedCount === 0)
  const skillCount = matches.reduce(
    (sum, provider) => sum + provider.skills.length,
    0
  )

  async function handleInstall(provider: LibraryProvider) {
    try {
      await installLibrarySkills(
        provider.skills
          .filter((skill) => !skill.installed)
          .map((skill) => skill.slug)
      )
    } catch {
      // The mutation hook reports failures.
    }
  }

  async function handleCreateAgent(provider: LibraryProvider) {
    try {
      // Agents can bind only installed library skills.
      await handleInstallMissing(provider)
      const { data: presets } = await refetchPresets()
      const takenSlugs = (presets ?? []).map((preset) => preset.slug)
      setAgentDraft({
        name: uniqueAgentPresetName(libraryEntryTitle(provider), takenSlugs),
        slugs: provider.skills.map((skill) => skill.slug),
        takenSlugs,
      })
    } catch {
      // The mutation hook reports failures.
    }
  }

  async function handleInstallMissing(provider: LibraryProvider) {
    const missing = provider.skills
      .filter((skill) => !skill.installed)
      .map((skill) => skill.slug)
    if (missing.length > 0) await installLibrarySkills(missing)
  }

  async function handleUninstall() {
    if (!pendingUninstall) return
    try {
      await uninstallLibrarySkills(
        pendingUninstall.skills
          .filter((skill) => skill.installed)
          .map((skill) => skill.slug)
      )
    } catch {
      // The mutation hook reports failures.
    } finally {
      setPendingUninstall(null)
    }
  }

  if (librarySkillsIsLoading) {
    return <CenteredSpinner />
  }

  if (librarySkillsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading skill library: ${librarySkillsError.message}`}
      />
    )
  }

  if (providers.length === 0) {
    return (
      <Empty className="h-full">
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <LibraryIcon className="size-6" />
          </EmptyMedia>
          <EmptyTitle>No library skills available</EmptyTitle>
          <EmptyDescription>
            Library skills ship with Tracecat releases.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  function renderRows(rows: LibraryProvider[], emptyText: string) {
    if (rows.length === 0) {
      return (
        <div className="border-b px-4 py-4 text-xs text-muted-foreground">
          {emptyText}
        </div>
      )
    }
    return rows.map((provider) => (
      <LibraryProviderRow
        key={provider.slug}
        provider={provider}
        href={libraryEntryPath(workspaceId, provider)}
        canCreate={canCreate}
        canDelete={canDelete}
        disabled={isMutating}
        onInstall={() => handleInstall(provider)}
        onUninstall={() => setPendingUninstall(provider)}
        onCreateAgent={
          canCreate ? () => handleCreateAgent(provider) : undefined
        }
      />
    ))
  }

  return (
    <div className="flex size-full flex-col overflow-auto">
      <header className="flex h-10 shrink-0 items-center gap-3 border-b pl-3 pr-4">
        <div className="flex size-7 shrink-0 items-center justify-center">
          <SearchIcon className="size-4 text-muted-foreground" />
        </div>
        <Input
          type="text"
          aria-label="Search library skills"
          placeholder="Search library skills..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className={cn(
            "h-7 w-64 border-none bg-transparent p-0 text-sm shadow-none outline-none",
            "placeholder:text-muted-foreground",
            "focus-visible:ring-0 focus-visible:ring-offset-0"
          )}
        />
        <span className="ml-auto text-xs text-muted-foreground">
          {skillCount === 1 ? "1 skill" : `${skillCount} skills`}
        </span>
      </header>
      <section aria-label="Installed">
        <SectionHeader label="Installed" count={installed.length} />
        {renderRows(
          installed,
          normalizedQuery
            ? "No installed skills match."
            : "No library skills installed yet."
        )}
      </section>
      <section aria-label="Available">
        <SectionHeader label="Available" count={available.length} />
        {renderRows(
          available,
          normalizedQuery
            ? "No available skills match."
            : "Every library skill is installed."
        )}
      </section>
      <CreateAgentDialog
        open={agentDraft !== null}
        onOpenChange={(open) => {
          if (!open) setAgentDraft(null)
        }}
        librarySkills={agentDraft?.slugs}
        defaultName={agentDraft?.name}
        takenSlugs={agentDraft?.takenSlugs}
      />
      <SkillLibraryUninstallDialog
        open={pendingUninstall !== null}
        onOpenChange={(open) => {
          if (!open) setPendingUninstall(null)
        }}
        providerName={
          pendingUninstall ? libraryEntryTitle(pendingUninstall) : ""
        }
        skillCount={pendingUninstall?.installedCount ?? 0}
        pending={uninstallLibrarySkillsIsPending}
        onConfirm={handleUninstall}
      />
    </div>
  )
}

function SectionHeader({ label, count }: { label: string; count: number }) {
  return (
    <div className="flex h-8 items-center gap-2 border-b bg-muted/40 px-4 text-xs font-medium">
      {label}
      <span className="font-normal text-muted-foreground">{count}</span>
    </div>
  )
}

function libraryEntryTitle(provider: LibraryProvider): string {
  return provider.standalone ? librarySkillTitle(provider.name) : provider.name
}

function librarySummaryFallback(provider: LibraryProvider): string {
  if (provider.standalone) return provider.skills[0]?.description ?? ""
  return provider.skills.map((skill) => skill.slug).join(", ")
}

function skillsLabel(provider: LibraryProvider): string {
  const total = provider.skills.length
  if (provider.installedCount > 0 && provider.installedCount < total) {
    return `${provider.installedCount} of ${total} skills installed`
  }
  return `${total} skills`
}

const CATEGORY_ICONS: Record<
  LibrarySkillCategory,
  ComponentType<{ className?: string }>
> = {
  investigation: ScanSearch,
  detection: Radar,
  exposure: KeyRound,
  compliance: ClipboardCheck,
}

function LibraryEntryIcon({ provider }: { provider: LibraryProvider }) {
  if (!provider.standalone) {
    return <SkillLibraryProviderIcon providerSlug={provider.slug} />
  }
  const maintainer = provider.skills[0]?.source?.provider
  if (maintainer && libraryMaintainer(maintainer) === "community") {
    return (
      <SkillLibraryProviderIcon
        providerSlug={libraryProviderSlug(maintainer)}
      />
    )
  }
  const category = librarySkillCategory(provider.name)
  const Icon = category ? CATEGORY_ICONS[category] : Pyramid
  return (
    <span className="flex size-8 shrink-0 items-center justify-center rounded-md border bg-background">
      <Icon className="size-4 text-muted-foreground" />
    </span>
  )
}

function LibraryProviderRow({
  provider,
  href,
  canCreate,
  canDelete,
  disabled,
  onInstall,
  onUninstall,
  onCreateAgent,
}: {
  provider: LibraryProvider
  href: string
  canCreate: boolean
  canDelete: boolean
  disabled: boolean
  onInstall: () => void
  onUninstall: () => void
  onCreateAgent?: () => void
}) {
  const fullyInstalled = provider.installedCount === provider.skills.length
  const createAgentLabel =
    provider.skills.length > 1
      ? "Create agent with these skills"
      : "Create agent with this skill"
  return (
    <div className="flex flex-wrap items-center gap-3 border-b px-4 py-3 hover:bg-muted/30">
      <LibraryEntryIcon provider={provider} />
      <div className="flex min-w-0 flex-1 basis-80 flex-col gap-0.5">
        <Link href={href} className="text-sm font-semibold hover:underline">
          {libraryEntryTitle(provider)}
        </Link>
        <span className="truncate text-xs text-muted-foreground">
          {provider.summary ?? librarySummaryFallback(provider)}
        </span>
      </div>
      {provider.skills.length > 1 ? (
        <Badge variant="secondary" className="gap-1 text-[10px] font-normal">
          <Pyramid className="size-3" />
          {skillsLabel(provider)}
        </Badge>
      ) : null}
      {fullyInstalled && canDelete ? (
        <SkillLibraryActionButton
          label="Uninstall"
          className="w-24"
          disabled={disabled}
          onAction={onUninstall}
          onCreateAgent={onCreateAgent}
          createAgentLabel={createAgentLabel}
        />
      ) : null}
      {fullyInstalled && !canDelete && onCreateAgent ? (
        <SkillLibraryActionButton
          label="Create agent"
          className="w-24"
          disabled={disabled}
          onAction={onCreateAgent}
          createAgentLabel={createAgentLabel}
        />
      ) : null}
      {!fullyInstalled && canCreate ? (
        <SkillLibraryActionButton
          label="Install"
          className="w-24"
          disabled={disabled}
          onAction={onInstall}
          onCreateAgent={onCreateAgent}
          createAgentLabel={createAgentLabel}
        />
      ) : null}
    </div>
  )
}
