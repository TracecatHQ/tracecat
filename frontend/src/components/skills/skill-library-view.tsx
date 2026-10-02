"use client"

import { GitForkIcon, LibraryIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import type { LibrarySkillRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { useSkillLibrary } from "@/hooks/use-skill-library"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Render the Tracecat skill library with this workspace's install state.
 *
 * @returns The skill library view.
 */
export function SkillLibraryView() {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const canCreate = useScopeCheck("agent:create") === true
  const canDelete = useScopeCheck("agent:delete") === true
  const {
    librarySkills,
    librarySkillsIsLoading,
    librarySkillsError,
    installLibrarySkill,
    installLibrarySkillIsPending,
    uninstallLibrarySkill,
    uninstallLibrarySkillIsPending,
    forkLibrarySkill,
    forkLibrarySkillIsPending,
  } = useSkillLibrary(workspaceId)
  const isMutating =
    installLibrarySkillIsPending ||
    uninstallLibrarySkillIsPending ||
    forkLibrarySkillIsPending

  async function handleFork(slug: string) {
    try {
      const skill = await forkLibrarySkill(slug)
      router.push(`/workspaces/${workspaceId}/skills/${skill.id}`)
    } catch {
      // The mutation hook reports failures.
    }
  }

  async function handleInstall(slug: string) {
    try {
      await installLibrarySkill(slug)
    } catch {
      // The mutation hook reports failures.
    }
  }

  async function handleUninstall(slug: string) {
    try {
      await uninstallLibrarySkill(slug)
    } catch {
      // The mutation hook reports failures.
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

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        <div className="space-y-3">
          <h2 className="text-2xl font-semibold tracking-tight">
            Skill library
          </h2>
          <p className="text-base text-muted-foreground">
            Prebuilt skills maintained by Tracecat. Install one to attach it to
            agents, or fork it into an editable workspace skill.
          </p>
        </div>
        {!librarySkills || librarySkills.length === 0 ? (
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
        ) : (
          <div className="divide-y rounded-md border">
            {librarySkills.map((skill) => (
              <LibrarySkillRow
                key={skill.slug}
                skill={skill}
                canCreate={canCreate}
                canDelete={canDelete}
                disabled={isMutating}
                onInstall={() => handleInstall(skill.slug)}
                onUninstall={() => handleUninstall(skill.slug)}
                onFork={() => handleFork(skill.slug)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function LibrarySkillRow({
  skill,
  canCreate,
  canDelete,
  disabled,
  onInstall,
  onUninstall,
  onFork,
}: {
  skill: LibrarySkillRead
  canCreate: boolean
  canDelete: boolean
  disabled: boolean
  onInstall: () => void
  onUninstall: () => void
  onFork: () => void
}) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <LibraryIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{skill.slug}</span>
          {skill.installed ? (
            <Badge variant="secondary">Installed</Badge>
          ) : null}
        </div>
        {skill.description ? (
          <p className="text-xs text-muted-foreground">{skill.description}</p>
        ) : null}
      </div>
      <div className="flex shrink-0 items-center gap-2">
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
            disabled={disabled}
            onClick={onInstall}
          >
            Install
          </Button>
        ) : null}
      </div>
    </div>
  )
}
