"use client"

import { ChevronDownIcon, FilePlus, Plus, Upload } from "lucide-react"
import { useRouter } from "next/navigation"
import {
  type ChangeEvent,
  type DragEvent,
  useCallback,
  useRef,
  useState,
} from "react"
import { CreateSkillDialog } from "@/components/skills/create-skill-dialog"
import { UploadSkillConfirmDialog } from "@/components/skills/upload-skill-confirm-dialog"
import { UploadSkillDialog } from "@/components/skills/upload-skill-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import { useCreateSkill, useUploadSkill } from "@/hooks/use-skills"
import { getApiErrorDetail } from "@/lib/errors"
import {
  extractDroppedFiles,
  fileToUploadEntry,
  getUploadRootName,
} from "@/lib/skills-studio"
import { slugify } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * "+ Create new" dropdown shown in the controls header on the skills list
 * page. Mirrors `CreateWorkflowButton` and owns its own dialog state for
 * creating from scratch or uploading an existing skill directory.
 *
 * @returns The dropdown button plus dialogs.
 */
export function CreateSkillButton() {
  const router = useRouter()
  const workspaceId = useWorkspaceId()
  const { createSkill, createSkillPending } = useCreateSkill(workspaceId)
  const { uploadSkill, uploadSkillPending } = useUploadSkill(workspaceId)

  const [showNewSkillDialog, setShowNewSkillDialog] = useState(false)
  const [newSkillName, setNewSkillName] = useState("")
  const [newSkillDescription, setNewSkillDescription] = useState("")

  const [showUploadSkillDialog, setShowUploadSkillDialog] = useState(false)
  const [isDragOver, setIsDragOver] = useState(false)

  const [showUploadConfirmDialog, setShowUploadConfirmDialog] = useState(false)
  const [pendingUploadFiles, setPendingUploadFiles] = useState<
    Array<{ file: File; path: string }>
  >([])
  const [uploadConfirmPending, setUploadConfirmPending] = useState(false)
  const uploadConfirmPendingRef = useRef(false)

  const handleCreateSkill = useCallback(async () => {
    const name = slugify(newSkillName.trim(), "-") || "skill"
    try {
      const created = await createSkill({
        name,
        description: newSkillDescription.trim() || null,
      })
      setShowNewSkillDialog(false)
      setNewSkillName("")
      setNewSkillDescription("")
      router.push(`/workspaces/${workspaceId}/skills/${created.id}`)
    } catch {
      // toast handled in mutation onError
    }
  }, [createSkill, newSkillDescription, newSkillName, router, workspaceId])

  const handleDragOver = useCallback((event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDragOver(true)
  }, [])
  const handleDragLeave = useCallback(() => setIsDragOver(false), [])

  const handleDrop = useCallback(async (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault()
    setIsDragOver(false)
    try {
      const droppedFiles = await extractDroppedFiles(event)
      setPendingUploadFiles(droppedFiles)
      setShowUploadSkillDialog(false)
      setShowUploadConfirmDialog(true)
    } catch (error) {
      toast({
        title: "Drop upload failed",
        description:
          getApiErrorDetail(error) ?? "Failed to read dropped files.",
        variant: "destructive",
      })
    }
  }, [])

  const handleDirectoryInput = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? [])
      if (files.length === 0) {
        return
      }
      const mapped = files.map((file) => ({
        file,
        path: file.webkitRelativePath || file.name,
      }))
      setPendingUploadFiles(mapped)
      setShowUploadSkillDialog(false)
      setShowUploadConfirmDialog(true)
      event.target.value = ""
    },
    []
  )

  const handleConfirmUpload = useCallback(async () => {
    if (
      pendingUploadFiles.length === 0 ||
      uploadSkillPending ||
      uploadConfirmPendingRef.current
    ) {
      return
    }
    uploadConfirmPendingRef.current = true
    setUploadConfirmPending(true)
    try {
      const paths = pendingUploadFiles.map(({ path }) =>
        path.replace(/^\/+/, "")
      )
      const rootName = getUploadRootName(paths)
      const normalizedFiles = await Promise.all(
        pendingUploadFiles.map(async ({ file, path }) => {
          const normalizedPath = path.replace(/^\/+/, "")
          const relativePath = rootName
            ? normalizedPath.slice(rootName.length + 1)
            : normalizedPath
          return await fileToUploadEntry(file, relativePath || file.name)
        })
      )
      const name = slugify(
        rootName ?? pendingUploadFiles[0]?.file.name.replace(/\.[^.]+$/, ""),
        "-"
      )
      const created = await uploadSkill({
        name: name || "skill",
        files: normalizedFiles,
      })
      setShowUploadConfirmDialog(false)
      setPendingUploadFiles([])
      router.push(`/workspaces/${workspaceId}/skills/${created.id}`)
    } catch (error) {
      toast({
        title: "Upload failed",
        description: getApiErrorDetail(error) ?? "Failed to upload skill.",
        variant: "destructive",
      })
    } finally {
      uploadConfirmPendingRef.current = false
      setUploadConfirmPending(false)
    }
  }, [pendingUploadFiles, router, uploadSkill, uploadSkillPending, workspaceId])

  const handleCancelUpload = useCallback(() => {
    setShowUploadConfirmDialog(false)
    setPendingUploadFiles([])
  }, [])

  const uploadPending = uploadSkillPending || uploadConfirmPending

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-7 bg-white">
            <Plus className="mr-1 h-3.5 w-3.5" />
            Create new
            <ChevronDownIcon className="ml-1 h-3.5 w-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          align="end"
          className="
            [&_[data-radix-collection-item]]:flex
            [&_[data-radix-collection-item]]:items-center
            [&_[data-radix-collection-item]]:gap-2
          "
        >
          <DropdownMenuItem onSelect={() => setShowNewSkillDialog(true)}>
            <FilePlus className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Skill</span>
              <span className="text-xs text-muted-foreground">
                Start from scratch
              </span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setShowUploadSkillDialog(true)}>
            <Upload className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Upload</span>
              <span className="text-xs text-muted-foreground">
                Upload an existing skill directory
              </span>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <CreateSkillDialog
        open={showNewSkillDialog}
        onOpenChange={setShowNewSkillDialog}
        name={newSkillName}
        onNameChange={setNewSkillName}
        description={newSkillDescription}
        onDescriptionChange={setNewSkillDescription}
        pending={createSkillPending}
        onCreate={handleCreateSkill}
      />

      <UploadSkillDialog
        open={showUploadSkillDialog}
        onOpenChange={setShowUploadSkillDialog}
        isDragOver={isDragOver}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
        onDirectoryInput={handleDirectoryInput}
        uploadSkillPending={uploadPending}
      />

      <UploadSkillConfirmDialog
        open={showUploadConfirmDialog}
        files={pendingUploadFiles}
        pending={uploadPending}
        onConfirm={handleConfirmUpload}
        onCancel={handleCancelUpload}
      />
    </>
  )
}
