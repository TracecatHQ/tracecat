"use client"

import { EditorPanel } from "@/components/skills/editor-panel"
import { useSkillsStudioContext } from "@/providers/skills-studio"

function SkillsStudioContent() {
  const studio = useSkillsStudioContext()
  if (!studio) {
    return null
  }

  return (
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
          onDeleteSelectedFile={studio.onDeleteSelectedFile}
          onReplaceSelectedFile={studio.onReplaceSelectedFile}
          pendingCreate={studio.pendingCreate}
          pendingCreateError={studio.pendingCreateError}
          onBeginCreate={studio.onBeginCreate}
          onSubmitCreate={studio.onSubmitCreate}
          onCancelCreate={studio.onCancelCreate}
          onChangeCreatePath={studio.onChangeCreatePath}
          moveSource={studio.moveSource}
          onBeginMove={studio.onBeginMove}
          onCancelMove={studio.onCancelMove}
          onCommitMove={studio.onCommitMove}
          renameTarget={studio.renameTarget}
          renameError={studio.renameError}
          onBeginRename={studio.onBeginRename}
          onCancelRename={studio.onCancelRename}
          onSubmitRename={studio.onSubmitRename}
        />
      </div>
    </div>
  )
}

/**
 * Editor surface for a single skill. State is provided by
 * `SkillsStudioProvider` mounted in the workspace layout, so the global
 * controls header can render the same Versions/Save/Publish buttons that
 * act on this editor's working copy.
 *
 * @returns The skills studio editor view.
 */
export function SkillsStudio() {
  return <SkillsStudioContent />
}
