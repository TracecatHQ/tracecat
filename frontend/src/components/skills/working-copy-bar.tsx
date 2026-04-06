"use client"

import { Bot, History, Loader2, Save, Send } from "lucide-react"
import { useState } from "react"
import type { SkillDraftRead, SkillRead, SkillVersionRead } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from "@/components/ui/sheet"
import { describeVersion, renderValidationState } from "@/lib/skills-studio"
import { cn } from "@/lib/utils"

type WorkingCopyBarProps = {
  skill?: SkillRead
  draft?: SkillDraftRead
  versions?: SkillVersionRead[]
  versionsLoading: boolean
  selectedVersionId: string | null
  restoreSkillVersionPending: boolean
  hasUnsavedChanges: boolean
  canPublish: boolean
  patchSkillDraftPending: boolean
  createSkillDraftUploadPending: boolean
  publishSkillPending: boolean
  onSelectVersionId: (versionId: string) => void
  onRestore: (versionId: string) => Promise<void>
  onOpenPlayground: () => void
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
  selectedVersionId,
  restoreSkillVersionPending,
  hasUnsavedChanges,
  canPublish,
  patchSkillDraftPending,
  createSkillDraftUploadPending,
  publishSkillPending,
  onSelectVersionId,
  onRestore,
  onOpenPlayground,
  onSaveWorkingCopy,
  onPublish,
}: WorkingCopyBarProps) {
  const [versionsOpen, setVersionsOpen] = useState(false)

  return (
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
        <Button size="sm" variant="outline" onClick={onOpenPlayground}>
          <Bot className="mr-2 size-4" />
          Playground
        </Button>
        <Sheet open={versionsOpen} onOpenChange={setVersionsOpen}>
          <SheetTrigger asChild>
            <Button size="sm" variant="outline">
              <History className="mr-2 size-4" />
              Versions
            </Button>
          </SheetTrigger>
          <SheetContent side="right" className="w-[440px] sm:max-w-[440px]">
            <SheetHeader>
              <SheetTitle>Published versions</SheetTitle>
              <SheetDescription>
                Restore any published snapshot back into the working copy.
              </SheetDescription>
            </SheetHeader>
            <div className="mt-6 space-y-2 overflow-y-auto">
              {versionsLoading ? (
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="size-4 animate-spin" />
                  Loading versions…
                </div>
              ) : !versions || versions.length === 0 ? (
                <div className="text-sm text-muted-foreground">
                  No published versions yet.
                </div>
              ) : (
                versions.map((version) => (
                  <div
                    key={version.id}
                    className={cn(
                      "rounded-md border p-3",
                      selectedVersionId === version.id && "border-foreground"
                    )}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="font-medium">
                          {describeVersion(version)}
                        </div>
                        <div className="text-xs text-muted-foreground">
                          {new Date(version.created_at).toLocaleString()}
                        </div>
                      </div>
                      <div className="flex items-center gap-1">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => onSelectVersionId(version.id)}
                        >
                          Use for test
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => void onRestore(version.id)}
                          disabled={restoreSkillVersionPending}
                        >
                          Restore
                        </Button>
                      </div>
                    </div>
                  </div>
                ))
              )}
            </div>
          </SheetContent>
        </Sheet>
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
  )
}
