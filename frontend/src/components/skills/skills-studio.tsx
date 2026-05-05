"use client"

import { useRouter } from "next/navigation"
import { useEffect } from "react"
import { AddFileDialog } from "@/components/skills/add-file-dialog"
import { EditorPanel } from "@/components/skills/editor-panel"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useSkillsStudioContext } from "@/providers/skills-studio"

function SkillsStudioContent() {
  const studio = useSkillsStudioContext()
  if (!studio) {
    return null
  }

  return (
    <>
      <div className="flex h-full min-h-0 flex-col">
        <div className="min-h-0 flex-1">
          <EditorPanel
            skill={studio.skill}
            skillLoading={studio.skillLoading}
            draft={studio.draft}
            draftLoading={studio.draftLoading}
            visibleFiles={studio.visibleFiles}
            selectedFile={studio.selectedFile}
            selectedPath={studio.selectedPath}
            draftFile={studio.draftFile}
            draftFileLoading={studio.draftFileLoading}
            currentTextValue={studio.currentTextValue}
            markdownEditorActivatedRef={studio.markdownEditorActivatedRef}
            onSelectPath={studio.onSelectPath}
            onEditorChange={studio.onEditorChange}
            onUndoSelectedFileChange={studio.onUndoSelectedFileChange}
            onSaveWorkingCopy={studio.onSaveWorkingCopy}
            onOpenNewFileDialog={studio.onOpenNewFileDialog}
          />
        </div>
      </div>

      <AddFileDialog
        open={studio.showNewFileDialog}
        onOpenChange={studio.onNewFileDialogChange}
        filePath={studio.newFilePath}
        onFilePathChange={studio.onNewFilePathChange}
        onCreateFile={studio.onCreateNewFile}
      />
    </>
  )
}

/**
 * Editor surface for a single skill. State is provided by
 * `SkillsStudioProvider` mounted in the workspace layout, so the global
 * controls header can render the same Versions/Save/Publish buttons that
 * act on this editor's working copy.
 *
 * @param props.workspaceId Current workspace identifier (used for the
 *   entitlement redirect target only).
 * @returns The skills studio editor view.
 */
export function SkillsStudio({ workspaceId }: { workspaceId: string }) {
  const router = useRouter()
  const { hasEntitlement, isLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  useEffect(() => {
    if (!isLoading && !agentAddonsEnabled) {
      router.replace(`/workspaces/${workspaceId}`)
    }
  }, [agentAddonsEnabled, isLoading, router, workspaceId])

  if (isLoading) {
    return <div className="size-full animate-pulse bg-muted/20" />
  }

  if (!agentAddonsEnabled) {
    return null
  }

  return <SkillsStudioContent />
}
