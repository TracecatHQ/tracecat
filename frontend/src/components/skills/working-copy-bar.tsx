"use client"

import { History, Loader2, Save, Send } from "lucide-react"
import { useState } from "react"
import type { SkillDraftRead, SkillRead, SkillVersionRead } from "@/client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ScrollArea } from "@/components/ui/scroll-area"
import { describeVersion, renderValidationState } from "@/lib/skills-studio"

type WorkingCopyBarProps = {
  skill?: SkillRead
  draft?: SkillDraftRead
  versions?: SkillVersionRead[]
  versionsLoading: boolean
  restoreSkillVersionPending: boolean
  hasUnsavedChanges: boolean
  canPublish: boolean
  patchSkillDraftPending: boolean
  createSkillDraftUploadPending: boolean
  publishSkillPending: boolean
  onRestore: (versionId: string) => Promise<void>
  onSaveWorkingCopy: () => Promise<void>
  onPublish: () => Promise<void>
}

/**
 * Compact top bar for working-copy state and primary authoring actions.
 *
 * @param props Current skill state and action handlers.
 * @returns Rendered working-copy status bar.
 *
 * @example
 * <WorkingCopyBar
 *   skill={skill}
 *   draft={draft}
 *   hasUnsavedChanges={false}
 *   canPublish
 *   patchSkillDraftPending={false}
 *   createSkillDraftUploadPending={false}
 *   publishSkillPending={false}
 *   onSaveWorkingCopy={handleSave}
 *   onPublish={handlePublish}
 * />
 */
export function WorkingCopyBar({
  skill,
  draft,
  versions,
  versionsLoading,
  restoreSkillVersionPending,
  hasUnsavedChanges,
  canPublish,
  patchSkillDraftPending,
  createSkillDraftUploadPending,
  publishSkillPending,
  onRestore,
  onSaveWorkingCopy,
  onPublish,
}: WorkingCopyBarProps) {
  const [versionToRestore, setVersionToRestore] =
    useState<SkillVersionRead | null>(null)

  const handleConfirmRestore = async () => {
    if (!versionToRestore) {
      return
    }
    await onRestore(versionToRestore.id)
    setVersionToRestore(null)
  }

  return (
    <>
      <div className="flex items-center justify-between gap-4 border-b px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-sm font-semibold">
              {skill?.title ?? "Working copy"}
            </h2>
            {renderValidationState(
              draft?.is_publishable,
              draft?.validation_errors?.length ?? 0
            )}
            {hasUnsavedChanges ? (
              <Badge variant="secondary">Unsaved changes</Badge>
            ) : (
              <Badge variant="outline">Saved</Badge>
            )}
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="ghost">
                <History className="mr-2 size-4" />
                Versions
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-80 p-0">
              <div className="flex flex-col">
                <DropdownMenuLabel className="flex flex-col gap-1 px-3 py-2">
                  <div className="text-xs font-medium">Published versions</div>
                  <div className="text-xs text-muted-foreground">
                    Restore a published snapshot into the working copy.
                  </div>
                </DropdownMenuLabel>
                <DropdownMenuSeparator className="mx-0 my-0" />
                {versionsLoading ? (
                  <div className="flex items-center gap-2 px-3 py-3 text-sm text-muted-foreground">
                    <Loader2 className="size-4 animate-spin" />
                    Loading versions…
                  </div>
                ) : !versions || versions.length === 0 ? (
                  <div className="px-3 py-3 text-sm text-muted-foreground">
                    No published versions yet.
                  </div>
                ) : (
                  <ScrollArea className="max-h-80">
                    <DropdownMenuGroup className="flex flex-col p-1">
                      {versions.map((version) => (
                        <DropdownMenuItem
                          key={version.id}
                          className="items-start px-3 py-2"
                          disabled={restoreSkillVersionPending}
                          onSelect={() => setVersionToRestore(version)}
                        >
                          <div className="flex min-w-0 flex-col gap-0.5">
                            <div className="font-medium">
                              {describeVersion(version)}
                            </div>
                            <div className="text-muted-foreground">
                              {new Date(version.created_at).toLocaleString()}
                            </div>
                          </div>
                        </DropdownMenuItem>
                      ))}
                    </DropdownMenuGroup>
                  </ScrollArea>
                )}
              </div>
            </DropdownMenuContent>
          </DropdownMenu>
          <Button
            size="sm"
            onClick={() => void onSaveWorkingCopy()}
            disabled={
              !hasUnsavedChanges ||
              patchSkillDraftPending ||
              createSkillDraftUploadPending ||
              !draft
            }
          >
            {patchSkillDraftPending || createSkillDraftUploadPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Save className="mr-2 size-4" />
            )}
            Save
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => void onPublish()}
            disabled={!canPublish || publishSkillPending}
          >
            {publishSkillPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Send className="mr-2 size-4" />
            )}
            Publish version
          </Button>
        </div>
      </div>
      <AlertDialog
        open={versionToRestore !== null}
        onOpenChange={(open) => {
          if (!open && !restoreSkillVersionPending) {
            setVersionToRestore(null)
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Restore published version</AlertDialogTitle>
            <AlertDialogDescription>
              {versionToRestore
                ? `Restore ${describeVersion(versionToRestore)} into the working copy? This replaces the current draft state.`
                : "Restore the selected published version into the working copy?"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={restoreSkillVersionPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={(event) => {
                event.preventDefault()
                void handleConfirmRestore()
              }}
              disabled={restoreSkillVersionPending}
            >
              {restoreSkillVersionPending ? (
                <>
                  <Loader2 className="mr-2 size-4 animate-spin" />
                  Restoring...
                </>
              ) : (
                "Restore version"
              )}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
