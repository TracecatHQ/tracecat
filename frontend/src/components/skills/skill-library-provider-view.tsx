"use client"

import { ExternalLinkIcon, GitForkIcon, Pyramid } from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useEffect, useMemo, useState } from "react"
import type { LibrarySkillRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  SkillLibraryMaintainerMark,
  SkillLibraryProviderIcon,
} from "@/components/skills/skill-library-provider-icon"
import { SkillLibraryUninstallDialog } from "@/components/skills/skill-library-uninstall-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { useSkillLibrary } from "@/hooks/use-skill-library"
import {
  groupLibraryProviders,
  libraryEntryPath,
  librarySourceRepoUrl,
} from "@/lib/skill-library"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Render one library provider's skills with per-skill and bulk install
 * controls.
 *
 * @param props The provider's URL slug.
 * @returns The provider page.
 */
export function SkillLibraryProviderView({
  providerSlug,
}: {
  providerSlug: string
}) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const canCreate = useScopeCheck("agent:create") === true
  const canDelete = useScopeCheck("agent:delete") === true
  const [confirmOpen, setConfirmOpen] = useState(false)
  const {
    librarySkills,
    librarySkillsIsLoading,
    librarySkillsError,
    installLibrarySkill,
    installLibrarySkillIsPending,
    uninstallLibrarySkill,
    uninstallLibrarySkillIsPending,
    installLibrarySkills,
    installLibrarySkillsIsPending,
    uninstallLibrarySkills,
    uninstallLibrarySkillsIsPending,
    forkLibrarySkill,
    forkLibrarySkillIsPending,
  } = useSkillLibrary(workspaceId)
  const isMutating =
    installLibrarySkillIsPending ||
    uninstallLibrarySkillIsPending ||
    installLibrarySkillsIsPending ||
    uninstallLibrarySkillsIsPending ||
    forkLibrarySkillIsPending
  const provider = useMemo(
    () =>
      groupLibraryProviders(librarySkills ?? []).find(
        (candidate) => candidate.slug === providerSlug
      ),
    [librarySkills, providerSlug]
  )
  const soleSkillPath =
    provider?.skills.length === 1
      ? libraryEntryPath(workspaceId, provider)
      : null

  useEffect(() => {
    // A one-skill entry has no overview; its preview is the page.
    if (soleSkillPath) router.replace(soleSkillPath)
  }, [router, soleSkillPath])

  if (librarySkillsIsLoading || soleSkillPath) {
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

  if (!provider) {
    return (
      <AlertNotification
        level="error"
        message="This provider is not in the skill library."
      />
    )
  }

  const fullyInstalled = provider.installedCount === provider.skills.length
  const missing = provider.skills
    .filter((skill) => !skill.installed)
    .map((skill) => skill.slug)
  const present = provider.skills
    .filter((skill) => skill.installed)
    .map((skill) => skill.slug)

  async function run(action: () => Promise<unknown>) {
    try {
      await action()
    } catch {
      // The mutation hook reports failures.
    }
  }

  async function handleFork(slug: string) {
    try {
      const skill = await forkLibrarySkill(slug)
      router.push(`/workspaces/${workspaceId}/skills/${skill.id}`)
    } catch {
      // The mutation hook reports failures.
    }
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex max-w-[880px] flex-col gap-7 py-8">
        <header className="flex flex-col gap-3">
          <SkillLibraryProviderIcon
            providerSlug={provider.slug}
            className="size-14 rounded-xl p-2.5"
          />
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <h2 className="text-2xl font-semibold tracking-tight">
                {provider.name}
              </h2>
              <p className="text-sm text-muted-foreground">
                {provider.description ??
                  `Skills for ${provider.name}. Install all of them or only the ones you need, then attach them to agents.`}
              </p>
            </div>
            {fullyInstalled && canDelete ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={isMutating}
                onClick={() => setConfirmOpen(true)}
              >
                Uninstall all
              </Button>
            ) : null}
            {!fullyInstalled && canCreate ? (
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={isMutating}
                onClick={() => run(() => installLibrarySkills(missing))}
              >
                Install all
              </Button>
            ) : null}
          </div>
        </header>
        <section className="flex flex-col gap-1">
          <h3 className="mb-2 border-b pb-2 text-sm font-semibold">
            Skills{" "}
            <span className="font-normal text-muted-foreground">
              {provider.skills.length}
            </span>
          </h3>
          {provider.skills.map((skill) => (
            <ProviderSkillRow
              key={skill.slug}
              skill={skill}
              workspaceId={workspaceId}
              providerSlug={provider.slug}
              canCreate={canCreate}
              canDelete={canDelete}
              disabled={isMutating}
              onInstall={() => run(() => installLibrarySkill(skill.slug))}
              onUninstall={() => run(() => uninstallLibrarySkill(skill.slug))}
              onFork={() => handleFork(skill.slug)}
            />
          ))}
        </section>
        {provider.repos.length > 0 ? (
          <section className="flex flex-col gap-2">
            <h3 className="border-b pb-2 text-sm font-semibold">Information</h3>
            <dl className="grid grid-cols-[140px_minmax(0,1fr)] rounded-md border text-xs">
              <dt className="px-3 py-2.5 text-muted-foreground">Sources</dt>
              <dd className="flex flex-col gap-1.5 px-3 py-2.5">
                {provider.repos.map((repo) => (
                  <a
                    key={repo}
                    href={librarySourceRepoUrl(repo)}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex w-fit items-center gap-1 underline"
                  >
                    {repo}
                    <ExternalLinkIcon className="size-3" />
                  </a>
                ))}
              </dd>
            </dl>
          </section>
        ) : null}
      </div>
      <SkillLibraryUninstallDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        providerName={provider.name}
        skillCount={present.length}
        pending={uninstallLibrarySkillsIsPending}
        onConfirm={async () => {
          await run(() => uninstallLibrarySkills(present))
          setConfirmOpen(false)
        }}
      />
    </div>
  )
}

function ProviderSkillRow({
  skill,
  workspaceId,
  providerSlug,
  canCreate,
  canDelete,
  disabled,
  onInstall,
  onUninstall,
  onFork,
}: {
  skill: LibrarySkillRead
  workspaceId: string
  providerSlug: string
  canCreate: boolean
  canDelete: boolean
  disabled: boolean
  onInstall: () => void
  onUninstall: () => void
  onFork: () => void
}) {
  return (
    <div className="flex items-center gap-3 rounded-md p-3 hover:bg-muted/30">
      <span className="relative flex size-7 shrink-0 items-center justify-center rounded-md border bg-background">
        <Pyramid className="size-4 text-primary" />
        <SkillLibraryMaintainerMark provider={skill.source?.provider} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <Link
          href={`/workspaces/${workspaceId}/skills/library/${providerSlug}/${skill.slug}`}
          className="text-sm font-medium hover:underline"
        >
          {skill.slug}
        </Link>
        {skill.description ? (
          <span className="truncate text-xs text-muted-foreground">
            {skill.description}
          </span>
        ) : null}
      </div>
      {skill.installed ? (
        <Badge variant="secondary" className="text-[10px]">
          Installed
        </Badge>
      ) : null}
      {canCreate ? (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={disabled}
          onClick={onFork}
        >
          <GitForkIcon className="mr-1 size-3.5" />
          Fork
        </Button>
      ) : null}
      {skill.installed && canDelete ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="w-20"
          disabled={disabled}
          onClick={onUninstall}
        >
          Uninstall
        </Button>
      ) : null}
      {!skill.installed && canCreate ? (
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="w-20"
          disabled={disabled}
          onClick={onInstall}
        >
          Install
        </Button>
      ) : null}
    </div>
  )
}
