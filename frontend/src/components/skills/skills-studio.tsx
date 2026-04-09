"use client"

import { AddFileDialog } from "@/components/skills/add-file-dialog"
import { CreateSkillDialog } from "@/components/skills/create-skill-dialog"
import { EditorPanel } from "@/components/skills/editor-panel"
import { SkillListPanel } from "@/components/skills/skill-list-panel"
import { UploadSkillDialog } from "@/components/skills/upload-skill-dialog"
import { useSkillsStudio } from "@/components/skills/use-skills-studio"
import { WorkingCopyBar } from "@/components/skills/working-copy-bar"

/**
 * Full-screen skills authoring and QA surface for a workspace.
 *
 * @param props Component props.
 * @returns The skills studio split view.
 *
 * @example
 * <SkillsStudio workspaceId={workspaceId} initialSkillId={skillId} />
 */
export function SkillsStudio({
  workspaceId,
  initialSkillId,
}: {
  workspaceId: string
  initialSkillId?: string
}) {
  const studio = useSkillsStudio({ workspaceId, initialSkillId })

  return (
    <>
      <div className="size-full overflow-hidden">
        <div className="grid h-full min-h-0 grid-cols-[320px_minmax(0,1fr)]">
          <div className="min-h-0 border-r">
            <SkillListPanel
              workspaceId={studio.workspaceId}
              activeSkillId={studio.selectedSkillId}
              search={studio.search}
              onSearchChange={studio.onSearchChange}
              visibleSkills={studio.visibleSkills}
              skillsLoading={studio.skillsLoading}
              skillsError={studio.skillsError}
              onSelectSkill={studio.onSelectSkill}
              onCopyLocalAgentPrompt={studio.onCopyLocalAgentPrompt}
              onOpenNewSkillDialog={studio.onOpenNewSkillDialog}
              onOpenUploadSkillDialog={studio.onOpenUploadSkillDialog}
            />
          </div>
          <div className="flex min-h-0 flex-col">
            {studio.selectedSkillId && studio.skill && studio.draft ? (
              <WorkingCopyBar
                skill={studio.skill}
                draft={studio.draft}
                versions={studio.versions}
                versionsLoading={studio.versionsLoading}
                restoreSkillVersionPending={studio.restoreSkillVersionPending}
                hasUnsavedChanges={studio.hasUnsavedChanges}
                canPublish={studio.canPublish}
                patchSkillDraftPending={studio.patchSkillDraftPending}
                createSkillDraftUploadPending={
                  studio.createSkillDraftUploadPending
                }
                publishSkillPending={studio.publishSkillPending}
                onRestore={studio.onRestore}
                onSaveWorkingCopy={studio.onSaveWorkingCopy}
                onPublish={studio.onPublish}
              />
            ) : null}
            <div className="min-h-0 flex-1">
              <EditorPanel
                skill={studio.skill}
                skillLoading={studio.skillLoading}
                draft={studio.draft}
                draftLoading={studio.draftLoading}
                activeSkillId={studio.selectedSkillId}
                visibleFiles={studio.visibleFiles}
                selectedFile={studio.selectedFile}
                selectedPath={studio.selectedPath}
                draftFile={studio.draftFile}
                draftFileLoading={studio.draftFileLoading}
                currentTextValue={studio.currentTextValue}
                markdownEditorActivatedRef={studio.markdownEditorActivatedRef}
                onSelectPath={studio.onSelectPath}
                onEditorChange={studio.onEditorChange}
                onDeleteSelectedFile={studio.onDeleteSelectedFile}
                onUndoSelectedFileChange={studio.onUndoSelectedFileChange}
                onReplaceFile={studio.onReplaceFile}
                onSaveWorkingCopy={studio.onSaveWorkingCopy}
                onOpenNewFileDialog={studio.onOpenNewFileDialog}
                onOpenNewSkillDialog={studio.onOpenNewSkillDialog}
                onOpenUploadSkillDialog={studio.onOpenUploadSkillDialog}
              />
            </div>
          </div>
        </div>
      </div>

      <CreateSkillDialog
        open={studio.showNewSkillDialog}
        onOpenChange={studio.onNewSkillDialogChange}
        title={studio.newSkillTitle}
        onTitleChange={studio.onNewSkillTitleChange}
        slug={studio.newSkillSlug}
        onSlugChange={studio.onNewSkillSlugChange}
        description={studio.newSkillDescription}
        onDescriptionChange={studio.onNewSkillDescriptionChange}
        pending={studio.createSkillPending}
        onCreate={studio.onCreateSkill}
      />

      <UploadSkillDialog
        open={studio.showUploadSkillDialog}
        onOpenChange={studio.onUploadSkillDialogChange}
        isDragOver={studio.isDragOver}
        onDragOver={studio.onDragOver}
        onDragLeave={studio.onDragLeave}
        onDrop={studio.onDrop}
        onDirectoryInput={studio.onDirectoryInput}
        uploadSkillPending={studio.uploadSkillPending}
      />

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
