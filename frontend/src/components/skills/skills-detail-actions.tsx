"use client"

import { History, LayersPlus, Loader2, Save } from "lucide-react"
import Link from "next/link"
import { useEffect, useMemo, useState } from "react"
import type { SkillVersionRead } from "@/client"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { SkillFileTree } from "@/components/skills/file-tree"
import { SimpleEditor } from "@/components/tiptap-templates/simple/simple-editor"
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useSkillVersion, useSkillVersionFile } from "@/hooks/use-skills"
import { splitMarkdownFrontmatter } from "@/lib/markdown-frontmatter"
import {
  buildSkillFileTree,
  describeVersion,
  getLanguageForPath,
  isMarkdownPath,
} from "@/lib/skills-studio"
import { useSkillsStudioContext } from "@/providers/skills-studio"

/**
 * Icon-only action group rendered inside the global controls header for the
 * skill detail page: Versions dropdown, Save, Publish version. Pulls state
 * from `SkillsStudioProvider` so it stays in sync with the editor.
 *
 * @returns The header actions, or null if no studio context is mounted.
 */
export function SkillsDetailActions() {
  const studio = useSkillsStudioContext()
  if (!studio) {
    return null
  }
  return <SkillsDetailActionsContent />
}

function SkillsDetailActionsContent() {
  const studio = useSkillsStudioContext()
  if (!studio) {
    return null
  }

  const {
    workspaceId,
    skillId,
    skill,
    draft,
    versions,
    versionsLoading,
    restoreSkillVersionPending,
    hasUnsavedChanges,
    canPublish,
    saveWorkingCopyPending,
    patchSkillDraftPending,
    createSkillDraftUploadPending,
    publishSkillPending,
    onRestore,
    onSaveWorkingCopy,
    onPublish,
  } = studio

  const [versionToRestore, setVersionToRestore] =
    useState<SkillVersionRead | null>(null)
  const [selectedVersionPath, setSelectedVersionPath] = useState<string | null>(
    null
  )
  const { version: selectedVersionDetail, versionLoading } = useSkillVersion(
    workspaceId,
    skill?.id ?? null,
    versionToRestore?.id ?? null
  )
  const { versionFile, versionFileLoading } = useSkillVersionFile(
    workspaceId,
    skill?.id ?? null,
    versionToRestore?.id ?? null,
    selectedVersionPath
  )

  const versionFileTree = useMemo(() => {
    if (!selectedVersionDetail?.files?.length) {
      return []
    }
    return buildSkillFileTree(
      selectedVersionDetail.files.map((file) => ({
        path: file.path,
        contentType: file.content_type,
        sizeBytes: file.size_bytes,
        change: null,
        isNew: false,
      }))
    )
  }, [selectedVersionDetail])

  const selectedVersionMarkdownPreview = useMemo(() => {
    if (
      versionFile?.kind !== "inline" ||
      !isMarkdownPath(versionFile.path) ||
      !versionFile.text_content
    ) {
      return null
    }
    return splitMarkdownFrontmatter(versionFile.text_content)
  }, [versionFile])

  useEffect(() => {
    if (!versionToRestore) {
      setSelectedVersionPath(null)
      return
    }
    const firstPath = selectedVersionDetail?.files?.[0]?.path ?? null
    if (
      selectedVersionPath &&
      selectedVersionDetail?.files?.some(
        (file) => file.path === selectedVersionPath
      )
    ) {
      return
    }
    setSelectedVersionPath(firstPath)
  }, [selectedVersionDetail, selectedVersionPath, versionToRestore])

  const handleConfirmRestore = async () => {
    if (!versionToRestore) {
      return
    }
    try {
      await onRestore(versionToRestore.id)
    } catch {
      return
    }
    setVersionToRestore(null)
  }

  const saveDisabled =
    !hasUnsavedChanges ||
    saveWorkingCopyPending ||
    patchSkillDraftPending ||
    createSkillDraftUploadPending ||
    !draft
  const saveBusy =
    saveWorkingCopyPending ||
    patchSkillDraftPending ||
    createSkillDraftUploadPending

  // Avoid an unused `skillId` lint when the dialog is closed.
  void skillId

  return (
    <>
      <DropdownMenu>
        <Tooltip>
          <TooltipTrigger asChild>
            <DropdownMenuTrigger asChild>
              <Button size="icon" variant="ghost" className="size-7">
                <History className="size-4" />
                <span className="sr-only">Versions</span>
              </Button>
            </DropdownMenuTrigger>
          </TooltipTrigger>
          <TooltipContent>Versions</TooltipContent>
        </Tooltip>
        <DropdownMenuContent align="end" className="w-80 p-0">
          <div className="flex flex-col">
            <DropdownMenuLabel className="flex flex-col gap-1 px-3 py-2">
              <div className="text-xs font-medium">Published versions</div>
              <div className="text-xs text-muted-foreground">
                Select which published version is currently active.
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
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            size="icon"
            variant="ghost"
            className="size-7"
            onClick={() => void onSaveWorkingCopy()}
            disabled={saveDisabled}
          >
            {saveBusy ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Save className="size-4" />
            )}
            <span className="sr-only">Save</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent>Save</TooltipContent>
      </Tooltip>
      <Tooltip>
        <TooltipTrigger asChild>
          <Button
            size="icon"
            variant="ghost"
            className="size-7"
            onClick={() => void onPublish()}
            disabled={!canPublish || publishSkillPending}
          >
            {publishSkillPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <LayersPlus className="size-4" />
            )}
            <span className="sr-only">Publish version</span>
          </Button>
        </TooltipTrigger>
        <TooltipContent>Publish version</TooltipContent>
      </Tooltip>

      <AlertDialog
        open={versionToRestore !== null}
        onOpenChange={(open) => {
          if (!open && !restoreSkillVersionPending) {
            setVersionToRestore(null)
          }
        }}
      >
        <AlertDialogContent className="max-h-[85vh] max-w-5xl grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden">
          <AlertDialogHeader>
            <AlertDialogTitle>Select active version</AlertDialogTitle>
            <AlertDialogDescription>
              {versionToRestore
                ? `Set ${describeVersion(versionToRestore)} as the active published version for this skill?`
                : "Set the selected published version as the active version for this skill?"}
            </AlertDialogDescription>
          </AlertDialogHeader>
          {versionLoading ? (
            <div className="flex min-h-0 items-center gap-2 rounded-md border px-3 py-3 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading files…
            </div>
          ) : selectedVersionDetail?.files?.length ? (
            <div className="min-h-0 overflow-hidden space-y-2">
              <div className="text-xs font-medium text-muted-foreground">
                Files in this version
              </div>
              <div className="grid min-h-0 gap-3 md:h-[min(32rem,50vh)] md:grid-cols-[minmax(0,240px)_minmax(0,1fr)]">
                <ScrollArea className="min-h-0 rounded-md border p-2">
                  <SkillFileTree
                    nodes={versionFileTree}
                    selectedPath={selectedVersionPath}
                    onSelectPath={setSelectedVersionPath}
                  />
                </ScrollArea>
                <div className="flex min-h-0 min-w-0 overflow-hidden rounded-md border">
                  <div className="flex min-h-0 min-w-0 flex-1 flex-col">
                    <div className="border-b px-3 py-2">
                      <div className="truncate text-xs font-medium">
                        {selectedVersionPath ?? "Select a file"}
                      </div>
                    </div>
                    <div className="min-h-0 min-w-0 flex-1 overflow-auto">
                      {!selectedVersionPath ? (
                        <div className="flex h-full items-center justify-center px-4 text-sm text-muted-foreground">
                          Select a file to preview.
                        </div>
                      ) : versionFileLoading ? (
                        <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                          <Loader2 className="size-4 animate-spin" />
                          Loading preview…
                        </div>
                      ) : versionFile?.kind === "inline" &&
                        isMarkdownPath(versionFile.path) &&
                        selectedVersionMarkdownPreview ? (
                        <div className="flex min-h-0 min-w-0 h-full flex-col gap-4 overflow-auto p-3">
                          <div className="flex min-w-0 flex-col gap-2">
                            <div>
                              <div className="text-sm font-medium">
                                Frontmatter
                              </div>
                              <div className="text-xs text-muted-foreground">
                                Read-only skill metadata for this version.
                              </div>
                            </div>
                            <div className="overflow-hidden rounded-md border">
                              <CodeEditor
                                value={
                                  selectedVersionMarkdownPreview.frontmatter
                                }
                                language="yaml"
                                readOnly
                                wrapLongLines
                                className="[&_.cm-editor]:border-0 [&_.cm-editor]:rounded-none"
                              />
                            </div>
                          </div>
                          <div className="flex min-w-0 flex-col gap-2">
                            <div>
                              <div className="text-sm font-medium">
                                Instructions
                              </div>
                              <div className="text-xs text-muted-foreground">
                                Read-only markdown preview for this version.
                              </div>
                            </div>
                            <div className="overflow-hidden rounded-md border">
                              <div className="min-w-0 px-3 py-3">
                                <SimpleEditor
                                  value={selectedVersionMarkdownPreview.body}
                                  editable={false}
                                  showToolbar={false}
                                  className="[&_.simple-editor-content--readonly]:overflow-visible"
                                />
                              </div>
                            </div>
                          </div>
                        </div>
                      ) : versionFile?.kind === "inline" &&
                        isMarkdownPath(versionFile.path) ? (
                        <div className="min-h-0 min-w-0 h-full overflow-hidden p-3">
                          <SimpleEditor
                            value={versionFile.text_content ?? ""}
                            editable={false}
                            showToolbar={false}
                            className="h-full"
                            style={{ height: "100%" }}
                          />
                        </div>
                      ) : versionFile?.kind === "inline" ? (
                        <div className="min-h-0 min-w-0 h-full overflow-hidden p-3">
                          <CodeEditor
                            value={versionFile.text_content ?? ""}
                            language={getLanguageForPath(versionFile.path)}
                            readOnly
                            className="h-full [&_.cm-editor]:h-full [&_.cm-scroller]:min-h-full"
                          />
                        </div>
                      ) : versionFile?.download_url ? (
                        <div className="flex h-full flex-col items-center justify-center gap-3 px-4 text-center">
                          <p className="text-sm text-muted-foreground">
                            This file is not previewable inline.
                          </p>
                          <Button asChild size="sm" variant="outline">
                            <Link
                              href={versionFile.download_url}
                              target="_blank"
                              rel="noreferrer"
                            >
                              Open file
                            </Link>
                          </Button>
                        </div>
                      ) : (
                        <div className="flex h-full items-center justify-center px-4 text-sm text-muted-foreground">
                          Preview unavailable.
                        </div>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            </div>
          ) : null}
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
                  Updating...
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
