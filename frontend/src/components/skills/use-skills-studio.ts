"use client"

import { useRouter } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type {
  SkillDraftAttachUploadedBlobOp,
  SkillDraftDeleteFileOp,
  SkillDraftFileRead,
  SkillDraftRead,
  SkillDraftUpsertTextFileOp,
  SkillRead,
  SkillReadMinimal,
  SkillVersionRead,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  useCreateSkillDraftUpload,
  useDeleteSkill,
  usePatchSkillDraft,
  usePublishSkill,
  useRestoreSkillVersion,
  useSkill,
  useSkillDraft,
  useSkillDraftFile,
  useSkillVersions,
} from "@/hooks/use-skills"
import { getApiErrorDetail } from "@/lib/errors"
import {
  buildVisibleFiles,
  comparePaths,
  computeFileSha256,
  type DraftChange,
  getTextContentType,
  isEditablePath,
  isMarkdownPath,
  uploadFileToSession,
  type VisibleFileEntry,
  validateSkillDraftPath,
} from "@/lib/skills-studio"

/** State and handlers backing the single-skill editor surface. */
type UseSkillsStudioReturn = {
  // Identity
  workspaceId: string
  skillId: string
  selectedPath: string | null

  // Delete skill
  showDeleteSkillDialog: boolean
  deleteSkillTarget: SkillReadMinimal | null
  deleteSkillPending: boolean
  onOpenDeleteSkillDialog: (skill: SkillReadMinimal) => void
  onDeleteSkillDialogChange: (open: boolean) => void
  onConfirmDeleteSkill: () => Promise<void>

  // Editor panel
  skill?: SkillRead
  skillLoading: boolean
  draft?: SkillDraftRead
  draftLoading: boolean
  visibleFiles: VisibleFileEntry[]
  selectedFile: VisibleFileEntry | null
  draftFile?: SkillDraftFileRead
  draftFileLoading: boolean
  currentTextValue: string | null
  markdownEditorActivatedRef: React.MutableRefObject<boolean>
  onSelectPath: (path: string) => void
  onEditorChange: (nextValue: string) => void
  onUndoSelectedFileChange: () => void
  onSaveWorkingCopy: () => Promise<void>
  onOpenNewFileDialog: () => void

  // Inspector / working copy
  hasUnsavedChanges: boolean
  canPublish: boolean
  saveWorkingCopyPending: boolean
  patchSkillDraftPending: boolean
  createSkillDraftUploadPending: boolean
  publishSkillPending: boolean
  onPublish: () => Promise<void>
  versions?: SkillVersionRead[]
  versionsLoading: boolean
  restoreSkillVersionPending: boolean
  onRestore: (versionId: string) => Promise<void>

  // Add file dialog
  showNewFileDialog: boolean
  onNewFileDialogChange: (open: boolean) => void
  newFilePath: string
  onNewFilePathChange: (value: string) => void
  onCreateNewFile: () => void
}

type DraftChangesForSkill = Record<string, DraftChange>

const EMPTY_DRAFT_CHANGES: DraftChangesForSkill = {}

/**
 * Encapsulates state, data-fetching, and handlers for the skills studio
 * editor pane scoped to a single skill.
 *
 * @param params.workspaceId Current workspace identifier.
 * @param params.skillId Active skill identifier.
 * @returns Everything the editor needs to render and interact.
 */
export function useSkillsStudio(params: {
  workspaceId: string
  skillId: string
}): UseSkillsStudioReturn {
  const { workspaceId, skillId } = params
  const router = useRouter()
  const markdownEditorActivatedRef = useRef(false)

  // ── State ──────────────────────────────────────────────────────────
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [draftChanges, setDraftChanges] =
    useState<DraftChangesForSkill>(EMPTY_DRAFT_CHANGES)
  const [showNewFileDialog, setShowNewFileDialog] = useState(false)
  const [newFilePath, setNewFilePath] = useState("")
  const [saveWorkingCopyPending, setSaveWorkingCopyPending] = useState(false)
  const saveWorkingCopyPendingRef = useRef(false)

  const updateDraftChanges = useCallback(
    (
      updater:
        | DraftChangesForSkill
        | ((current: DraftChangesForSkill) => DraftChangesForSkill)
    ) => {
      setDraftChanges((current) => {
        const next = typeof updater === "function" ? updater(current) : updater
        return next
      })
    },
    []
  )

  // ── Data fetching ──────────────────────────────────────────────────
  const { skill, skillLoading } = useSkill(workspaceId, skillId)
  const { draft, draftLoading } = useSkillDraft(workspaceId, skillId)
  const { versions, versionsLoading } = useSkillVersions(workspaceId, skillId)

  const visibleFiles = useMemo(
    () => buildVisibleFiles(draft?.files, draftChanges),
    [draft?.files, draftChanges]
  )
  const selectedFile =
    visibleFiles.find((file) => file.path === selectedPath) ?? null

  const selectedFileQueryPath = useMemo(() => {
    if (!selectedFile || selectedFile.change?.kind === "delete") {
      return null
    }
    if (selectedFile.isNew) {
      return null
    }
    return selectedFile.path
  }, [selectedFile])

  const { draftFile, draftFileLoading } = useSkillDraftFile(
    workspaceId,
    skillId,
    selectedFileQueryPath
  )

  const { patchSkillDraft, patchSkillDraftPending } =
    usePatchSkillDraft(workspaceId)
  const { createSkillDraftUpload, createSkillDraftUploadPending } =
    useCreateSkillDraftUpload(workspaceId)
  const { publishSkill, publishSkillPending } = usePublishSkill(workspaceId)
  const { restoreSkillVersion, restoreSkillVersionPending } =
    useRestoreSkillVersion(workspaceId)
  const { deleteSkill, deleteSkillPending } = useDeleteSkill(workspaceId)

  // Delete skill dialog state
  const [showDeleteSkillDialog, setShowDeleteSkillDialog] = useState(false)
  const [deleteSkillTarget, setDeleteSkillTarget] =
    useState<SkillReadMinimal | null>(null)

  // ── Derived ────────────────────────────────────────────────────────
  const hasUnsavedChanges = useMemo(
    () => Object.keys(draftChanges).length > 0,
    [draftChanges]
  )
  const canPublish = hasUnsavedChanges && Boolean(draft?.is_publishable)

  const currentTextValue = useMemo(() => {
    if (!selectedFile) {
      return null
    }
    const pending = selectedFile.change
    if (pending?.kind === "text") {
      return pending.content
    }
    if (pending?.kind === "delete") {
      return null
    }
    if (selectedFile.isNew) {
      return ""
    }
    if (draftFile?.kind === "inline") {
      return draftFile.text_content ?? ""
    }
    return null
  }, [selectedFile, draftFile])

  // ── Effects ────────────────────────────────────────────────────────
  useEffect(() => {
    setSelectedPath(null)
    setDraftChanges(EMPTY_DRAFT_CHANGES)
    markdownEditorActivatedRef.current = false
  }, [skillId])

  useEffect(() => {
    markdownEditorActivatedRef.current = false
  }, [draft?.draft_revision, selectedPath])

  useEffect(() => {
    if (!selectedPath && visibleFiles[0]) {
      setSelectedPath(visibleFiles[0].path)
      return
    }
    if (
      selectedPath &&
      !visibleFiles.some((file) => file.path === selectedPath)
    ) {
      setSelectedPath(visibleFiles[0]?.path ?? null)
    }
  }, [selectedPath, visibleFiles])

  // ── Stable callbacks ────────────────────────────────────────────────
  const handleOpenNewFileDialog = useCallback(
    () => setShowNewFileDialog(true),
    []
  )
  const handleOpenDeleteSkillDialog = useCallback(
    (target: SkillReadMinimal) => {
      setDeleteSkillTarget(target)
      setShowDeleteSkillDialog(true)
    },
    []
  )

  // ── Handlers ───────────────────────────────────────────────────────
  const handleEditorChange = (nextValue: string) => {
    if (!selectedFile) {
      return
    }

    if (
      isMarkdownPath(selectedFile.path) &&
      !selectedFile.change &&
      !markdownEditorActivatedRef.current
    ) {
      return
    }

    updateDraftChanges((current) => {
      let serverText: string | null = null
      if (draftFile?.kind === "inline") {
        serverText = draftFile.text_content ?? ""
      }
      if (serverText !== null && nextValue === serverText) {
        const next = { ...current }
        delete next[selectedFile.path]
        return next
      }

      return {
        ...current,
        [selectedFile.path]: {
          kind: "text",
          content: nextValue,
          contentType:
            selectedFile.contentType || getTextContentType(selectedFile.path),
        },
      }
    })
  }

  const handleUndoSelectedFileChange = () => {
    if (!selectedFile) {
      return
    }
    updateDraftChanges((current) => {
      const next = { ...current }
      delete next[selectedFile.path]
      return next
    })
  }

  const handleCreateNewFile = () => {
    const path = newFilePath.trim()
    if (!path) {
      return
    }
    const pathError = validateSkillDraftPath(path)
    if (pathError) {
      toast({
        title: "Invalid file path",
        description: pathError,
        variant: "destructive",
      })
      return
    }
    if (visibleFiles.some((file) => file.path === path)) {
      toast({
        title: "File already exists",
        description: "Choose a new path instead of replacing an existing file.",
        variant: "destructive",
      })
      return
    }
    if (!isEditablePath(path)) {
      toast({
        title: "Unsupported file type",
        description:
          "Only text-editable file types can be created inline (for example .md, .py, .ts, .json, and .yaml).",
        variant: "destructive",
      })
      return
    }
    updateDraftChanges((current) => ({
      ...current,
      [path]: {
        kind: "text",
        content: "",
        contentType: getTextContentType(path),
      },
    }))
    setSelectedPath(path)
    setNewFilePath("")
    setShowNewFileDialog(false)
  }

  const handleSaveWorkingCopy = async () => {
    if (!draft || saveWorkingCopyPendingRef.current) {
      return
    }

    if (Object.keys(draftChanges).length === 0) {
      return
    }

    saveWorkingCopyPendingRef.current = true
    setSaveWorkingCopyPending(true)

    try {
      const sortedChanges = Object.entries(draftChanges).sort(
        ([left], [right]) => comparePaths(left, right)
      )
      const operations: Array<
        | SkillDraftUpsertTextFileOp
        | SkillDraftAttachUploadedBlobOp
        | SkillDraftDeleteFileOp
      > = []

      for (const [path, change] of sortedChanges) {
        if (change.kind === "text") {
          operations.push({
            op: "upsert_text_file",
            path,
            content: change.content,
            content_type: change.contentType,
          })
          continue
        }

        if (change.kind === "delete") {
          operations.push({ op: "delete_file", path })
          continue
        }

        const sha256 = await computeFileSha256(change.file)
        const uploadSession = await createSkillDraftUpload({
          skillId,
          requestBody: {
            sha256,
            size_bytes: change.file.size,
            content_type: change.contentType,
          },
        })
        await uploadFileToSession(
          change.file,
          uploadSession.upload_url,
          uploadSession.method ?? "PUT",
          uploadSession.headers ?? {}
        )
        operations.push({
          op: "attach_uploaded_blob",
          path,
          upload_id: uploadSession.upload_id,
        })
      }

      await patchSkillDraft({
        skillId,
        requestBody: {
          base_revision: draft.draft_revision,
          operations,
        },
      })
      updateDraftChanges((current) => {
        const next = { ...current }
        for (const [path, change] of sortedChanges) {
          if (next[path] === change) {
            delete next[path]
          }
        }
        return next
      })
    } catch (error) {
      toast({
        title: "Save failed",
        description: getApiErrorDetail(error) ?? "Failed to save working copy.",
        variant: "destructive",
      })
    } finally {
      saveWorkingCopyPendingRef.current = false
      setSaveWorkingCopyPending(false)
    }
  }

  const handlePublish = async () => {
    try {
      await publishSkill({ skillId })
    } catch (error) {
      toast({
        title: "Publish failed",
        description: getApiErrorDetail(error) ?? "Failed to publish skill.",
        variant: "destructive",
      })
    }
  }

  const handleRestore = async (versionId: string) => {
    await restoreSkillVersion({ skillId, versionId })
  }

  const handleConfirmDeleteSkill = async () => {
    if (!deleteSkillTarget) {
      return
    }
    try {
      await deleteSkill({ skillId: deleteSkillTarget.id })
      setShowDeleteSkillDialog(false)
      setDeleteSkillTarget(null)
      router.push(`/workspaces/${workspaceId}/skills`)
    } catch {
      // The mutation hook already reports delete failures to the user.
    }
  }

  // ── Return ─────────────────────────────────────────────────────────
  return {
    workspaceId,
    skillId,
    selectedPath,

    showDeleteSkillDialog,
    deleteSkillTarget,
    deleteSkillPending,
    onOpenDeleteSkillDialog: handleOpenDeleteSkillDialog,
    onDeleteSkillDialogChange: setShowDeleteSkillDialog,
    onConfirmDeleteSkill: handleConfirmDeleteSkill,

    skill,
    skillLoading,
    draft,
    draftLoading,
    visibleFiles,
    selectedFile,
    draftFile,
    draftFileLoading,
    currentTextValue,
    markdownEditorActivatedRef,
    onSelectPath: setSelectedPath,
    onEditorChange: handleEditorChange,
    onUndoSelectedFileChange: handleUndoSelectedFileChange,
    onSaveWorkingCopy: handleSaveWorkingCopy,
    onOpenNewFileDialog: handleOpenNewFileDialog,

    hasUnsavedChanges,
    canPublish,
    saveWorkingCopyPending,
    patchSkillDraftPending,
    createSkillDraftUploadPending,
    publishSkillPending,
    onPublish: handlePublish,
    versions,
    versionsLoading,
    restoreSkillVersionPending,
    onRestore: handleRestore,

    showNewFileDialog,
    onNewFileDialogChange: setShowNewFileDialog,
    newFilePath,
    onNewFilePathChange: setNewFilePath,
    onCreateNewFile: handleCreateNewFile,
  }
}
