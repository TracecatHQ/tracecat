"use client"

import {
  Copy,
  Download,
  Eye,
  FileIcon,
  FileSpreadsheet,
  FileText,
  ImageIcon,
  Music,
  Plus,
  Presentation,
  Trash2,
  Video,
  XIcon,
} from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import type {
  ApiError,
  CaseAttachmentDownloadResponse,
  CaseAttachmentRead,
} from "@/client"
import {
  caseAttachmentsCreateAttachment,
  caseAttachmentsDeleteAttachment,
  caseAttachmentsDownloadAttachment,
  caseAttachmentsListAttachments,
} from "@/client"
import {
  CASE_PANEL_BOX_CLASS,
  CASE_TASK_ROW_CLASS,
  TASK_HOVER_REVEAL_CLASS,
  TASK_ICON_TRIGGER_CLASS,
} from "@/components/cases/case-task-fields"
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
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { Spinner } from "@/components/ui/spinner"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { describeAttachmentUploadError } from "@/lib/cases/attachment-errors"
import { invalidateCaseActivityQueries } from "@/lib/cases/invalidation"
import { useAttachmentObjectUrl } from "@/lib/cases/use-attachment-object-url"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"
import { cn, copyToClipboard, formatFileSize, shortTimeAgo } from "@/lib/utils"

interface CaseAttachmentsSectionProps {
  caseId: string
  workspaceId: string
}

/**
 * The list's box: the same recessed card the tasks panel and comment threads
 * use, so all three read as boxes on one column.
 */
const CASE_ATTACHMENTS_CONTAINER_CLASS = CASE_PANEL_BOX_CLASS

const MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024 // 20MB

function getFileIcon(contentType: string) {
  if (contentType.startsWith("image/")) return <ImageIcon className="size-4" />
  if (contentType === "application/pdf") return <FileText className="size-4" />
  if (contentType.includes("spreadsheet"))
    return <FileSpreadsheet className="size-4" />
  if (contentType.includes("presentation"))
    return <Presentation className="size-4" />
  if (contentType.startsWith("audio/")) return <Music className="size-4" />
  if (contentType.startsWith("video/")) return <Video className="size-4" />
  return <FileIcon className="size-4" />
}

function truncateHash(hash: string): string {
  return hash.substring(0, 8)
}

/** Props for {@link AddAttachmentRow}. */
interface AddAttachmentRowProps {
  isUploading: boolean
  onClick: () => void
}

/**
 * Muted ghost row that opens the file picker. Built to the task row's
 * geometry, so the plus sits exactly where a row's leading glyph does. Also
 * the panel's only empty state, like the tasks panel's `+ Add task` row.
 */
function AddAttachmentRow({ isUploading, onClick }: AddAttachmentRowProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isUploading}
      className={cn(
        CASE_TASK_ROW_CLASS,
        "flex h-11 w-full items-center gap-2 text-sm font-medium text-muted-foreground hover:bg-muted/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring disabled:pointer-events-none"
      )}
    >
      <span className="flex size-6 shrink-0 items-center justify-center">
        {isUploading ? <Spinner /> : <Plus className="size-5" />}
      </span>
      {isUploading ? "Uploading…" : "Add attachment"}
    </button>
  )
}

/** Props for {@link AttachmentRow}. */
interface AttachmentRowProps {
  attachment: CaseAttachmentRead
  onPreview: (attachment: CaseAttachmentRead) => void
  onDownload: (attachment: CaseAttachmentRead) => void
  onDelete: (attachment: CaseAttachmentRead) => void
}

/**
 * One attachment, as a single dense line: the file-type glyph, the name and
 * size, then a right-aligned cluster of hover-revealed actions (the SHA-256
 * pill, preview for images, download, delete) and the always-visible upload
 * time. Right-click is the row's menu, mirroring the task rows above.
 */
function AttachmentRow({
  attachment,
  onPreview,
  onDownload,
  onDelete,
}: AttachmentRowProps) {
  const [contextMenuOpen, setContextMenuOpen] = useState(false)
  const [shaCopied, setShaCopied] = useState(false)
  const shaCopiedResetRef = useRef<number | null>(null)

  useEffect(() => {
    return () => {
      if (shaCopiedResetRef.current !== null) {
        window.clearTimeout(shaCopiedResetRef.current)
      }
    }
  }, [])

  const isImage = attachment.content_type.startsWith("image/")
  const createdAtDate = new Date(attachment.created_at)

  async function copySha() {
    // Only claim "Copied" once the write actually landed: `copyToClipboard`
    // reports failure (e.g. no clipboard API over plain HTTP) as `false`.
    const copied = await copyToClipboard({
      value: attachment.sha256,
      message: "SHA-256 copied",
    })
    if (!copied) {
      return
    }
    setShaCopied(true)
    if (shaCopiedResetRef.current !== null) {
      window.clearTimeout(shaCopiedResetRef.current)
    }
    shaCopiedResetRef.current = window.setTimeout(
      () => setShaCopied(false),
      1500
    )
  }

  return (
    <ContextMenu onOpenChange={setContextMenuOpen}>
      <ContextMenuTrigger asChild>
        {/* `group/task`, not a new group name: the hover-reveal classes are
            reused verbatim from the task rows and are scoped to it. */}
        <div
          className={cn(
            CASE_TASK_ROW_CLASS,
            "group/task",
            contextMenuOpen ? "bg-muted/50" : "hover:bg-muted/50"
          )}
        >
          <div className="flex h-11 items-center gap-2">
            <span className="flex size-6 shrink-0 items-center justify-center text-muted-foreground">
              {getFileIcon(attachment.content_type)}
            </span>
            <div className="flex min-w-0 flex-1 items-baseline gap-1.5">
              <span className="min-w-0 truncate text-sm font-medium leading-6 text-foreground">
                {attachment.file_name}
              </span>
              <span className="shrink-0 text-xs text-muted-foreground">
                {formatFileSize(attachment.size)}
              </span>
            </div>
            <div className="flex shrink-0 items-center gap-1.5 pl-2">
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label="Copy SHA-256"
                    onClick={copySha}
                    className={cn(
                      TASK_HOVER_REVEAL_CLASS,
                      "flex h-6 shrink-0 items-center rounded px-1.5 font-mono text-[10px] text-muted-foreground transition-colors hover:bg-accent hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
                    )}
                  >
                    {truncateHash(attachment.sha256)}
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" className="font-mono text-xs">
                  {shaCopied ? "Copied" : attachment.sha256}
                </TooltipContent>
              </Tooltip>
              {isImage && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      type="button"
                      aria-label={`Preview ${attachment.file_name}`}
                      onClick={() => onPreview(attachment)}
                      className={cn(
                        TASK_ICON_TRIGGER_CLASS,
                        TASK_HOVER_REVEAL_CLASS,
                        "text-muted-foreground hover:text-foreground"
                      )}
                    >
                      <Eye className="size-4" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="top" className="text-xs">
                    Preview
                  </TooltipContent>
                </Tooltip>
              )}
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={`Download ${attachment.file_name}`}
                    onClick={() => onDownload(attachment)}
                    className={cn(
                      TASK_ICON_TRIGGER_CLASS,
                      TASK_HOVER_REVEAL_CLASS,
                      "text-muted-foreground hover:text-foreground"
                    )}
                  >
                    <Download className="size-4" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" className="text-xs">
                  Download
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <button
                    type="button"
                    aria-label={`Delete ${attachment.file_name}`}
                    onClick={() => onDelete(attachment)}
                    className={cn(
                      TASK_ICON_TRIGGER_CLASS,
                      TASK_HOVER_REVEAL_CLASS,
                      "text-red-600 dark:text-red-400"
                    )}
                  >
                    <Trash2 className="size-4" />
                  </button>
                </TooltipTrigger>
                <TooltipContent side="top" className="text-xs">
                  Delete
                </TooltipContent>
              </Tooltip>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="shrink-0 cursor-default text-xs text-muted-foreground">
                    {shortTimeAgo(createdAtDate)}
                  </span>
                </TooltipTrigger>
                <TooltipContent side="top" className="text-xs">
                  {createdAtDate.toLocaleString()}
                </TooltipContent>
              </Tooltip>
            </div>
          </div>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-48">
        {isImage && (
          <ContextMenuItem
            className="text-xs"
            onClick={() => onPreview(attachment)}
          >
            <Eye className="mr-2 size-3.5" />
            Preview
          </ContextMenuItem>
        )}
        <ContextMenuItem
          className="text-xs"
          onClick={() => onDownload(attachment)}
        >
          <Download className="mr-2 size-3.5" />
          Download
        </ContextMenuItem>
        <ContextMenuItem className="text-xs" onClick={copySha}>
          <Copy className="mr-2 size-3.5" />
          Copy SHA-256
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          className="text-xs text-red-600 focus:text-red-600 dark:text-red-400 dark:focus:text-red-400"
          onClick={() => onDelete(attachment)}
        >
          <Trash2 className="mr-2 size-3.5" />
          Delete
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  )
}

/** Props for {@link AttachmentPreviewBody}. */
interface AttachmentPreviewBodyProps {
  attachment: CaseAttachmentRead
  objectUrl: string | null
  isError: boolean
}

/** The preview dialog's image area: spinner, error text, or the image. */
function AttachmentPreviewBody({
  attachment,
  objectUrl,
  isError,
}: AttachmentPreviewBodyProps) {
  if (isError) {
    return (
      <div className="flex h-40 w-72 items-center justify-center text-xs text-white/70">
        Preview unavailable
      </div>
    )
  }
  if (!objectUrl) {
    return (
      <div className="flex h-40 w-72 items-center justify-center">
        <Spinner className="text-white/70" />
      </div>
    )
  }
  return (
    <img
      src={objectUrl}
      alt={attachment.file_name}
      className="object-contain"
      style={{ maxWidth: "90vw", maxHeight: "85vh" }}
    />
  )
}

/**
 * The Attachments panel: a flush stack of one-line attachment rows in the
 * same recessed box the tasks panel and comment threads use. The whole box is
 * the drop target, the `+ Add attachment` ghost row doubles as the empty
 * state, and delete goes through a confirm dialog.
 */
export function CaseAttachmentsSection({
  caseId,
  workspaceId,
}: CaseAttachmentsSectionProps) {
  const [isUploading, setIsUploading] = useState(false)
  const [isDragOver, setIsDragOver] = useState(false)
  // Depth counter so dragging across child rows doesn't flicker the state:
  // every child the cursor crosses fires its own enter/leave pair.
  const dragDepthRef = useRef(0)
  const [previewAttachment, setPreviewAttachment] =
    useState<CaseAttachmentRead | null>(null)
  // Two-state dialog pattern: the attachment is retained after `open` goes
  // false so the confirm keeps its content through the exit animation.
  const [attachmentPendingDelete, setAttachmentPendingDelete] =
    useState<CaseAttachmentRead | null>(null)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  const { workspace } = useWorkspaceDetails()

  const acceptedExtensions =
    workspace?.settings?.effective_allowed_attachment_extensions
  const acceptAttribute = acceptedExtensions?.join(",") || undefined

  const {
    data: attachments = [],
    isLoading: attachmentsLoading,
    error: attachmentsError,
  } = useQuery<CaseAttachmentRead[], ApiError>({
    queryKey: ["case-attachments", caseId, workspaceId],
    queryFn: async () =>
      await caseAttachmentsListAttachments({ caseId, workspaceId }),
  })

  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      return await caseAttachmentsCreateAttachment({
        caseId,
        workspaceId,
        formData: {
          file,
        },
      })
    },
    onSuccess: (_, file) => {
      queryClient.invalidateQueries({
        queryKey: ["case-attachments", caseId, workspaceId],
      })
      invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
      toast({
        title: "Attachment uploaded successfully",
        description: `${file.name} has been added to the case`,
      })
    },
    onError: (error: unknown, file) => {
      toast(describeAttachmentUploadError(error, file.name))
    },
  })

  const deleteMutation = useMutation({
    mutationFn: async (attachmentId: string) =>
      await caseAttachmentsDeleteAttachment({
        caseId,
        workspaceId,
        attachmentId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-attachments", caseId, workspaceId],
      })
      invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
      setDeleteDialogOpen(false)
      toast({
        title: "Attachment deleted",
        description: "The attachment has been removed from the case",
      })
    },
    onError: (error: ApiError) => {
      toast({
        title: "Delete failed",
        description: `Failed to delete attachment. ${error.message || "Unknown error"}`,
      })
    },
  })

  const validateFile = useCallback((file: File): boolean => {
    if (file.size > MAX_FILE_SIZE_BYTES) {
      toast({
        title: "File too large",
        description: `${file.name} is ${formatFileSize(file.size)}. Maximum file size is 20MB.`,
      })
      return false
    }
    return true
  }, [])

  const uploadFiles = useCallback(
    async (files: File[]) => {
      const validFiles = files.filter(validateFile)
      if (validFiles.length === 0) {
        return
      }
      setIsUploading(true)
      try {
        for (const file of validFiles) {
          try {
            await uploadMutation.mutateAsync(file)
          } catch {
            // Per-file errors are already toasted by the mutation; keep
            // uploading the remaining files.
          }
        }
      } finally {
        setIsUploading(false)
      }
    },
    [uploadMutation, validateFile]
  )

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
    void uploadFiles(files)
  }

  const handleAddAttachment = () => {
    fileInputRef.current?.click()
  }

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes("Files")) return
    e.preventDefault()
    dragDepthRef.current += 1
    setIsDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes("Files")) return
    e.preventDefault()
    dragDepthRef.current = Math.max(0, dragDepthRef.current - 1)
    if (dragDepthRef.current === 0) {
      setIsDragOver(false)
    }
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    if (!e.dataTransfer.types.includes("Files")) return
    e.preventDefault()
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      dragDepthRef.current = 0
      setIsDragOver(false)
      void uploadFiles(Array.from(e.dataTransfer.files))
    },
    [uploadFiles]
  )

  const handleDownload = async (attachment: CaseAttachmentRead) => {
    try {
      const response: CaseAttachmentDownloadResponse =
        await caseAttachmentsDownloadAttachment({
          caseId,
          workspaceId,
          attachmentId: attachment.id,
        })

      const downloadUrl = response.download_url
      if (!downloadUrl) {
        throw new Error("No download URL received from server")
      }

      const link = document.createElement("a")
      link.href = downloadUrl
      link.download = attachment.file_name
      link.rel = "noopener"
      link.style.display = "none"

      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    } catch (error) {
      console.error("Failed to download attachment:", error)
      toast({
        title: "Download failed",
        description: `Failed to download ${attachment.file_name}`,
      })
    }
  }

  const handleRequestDelete = (attachment: CaseAttachmentRead) => {
    setAttachmentPendingDelete(attachment)
    setDeleteDialogOpen(true)
  }

  const handleConfirmDelete = () => {
    if (!attachmentPendingDelete) return
    deleteMutation.mutate(attachmentPendingDelete.id)
  }

  const { objectUrl: previewObjectUrl, isError: previewIsError } =
    useAttachmentObjectUrl(workspaceId, caseId, previewAttachment?.id)

  if (attachmentsLoading) {
    return (
      <div className={CASE_ATTACHMENTS_CONTAINER_CLASS}>
        {[...Array(3)].map((_, index) => (
          <Skeleton key={index} className="mx-2 my-1 h-7 rounded-md" />
        ))}
      </div>
    )
  }

  if (attachmentsError) {
    return (
      <div className={CASE_ATTACHMENTS_CONTAINER_CLASS}>
        <p className="px-3 py-2 text-sm text-muted-foreground">
          Failed to load attachments
        </p>
      </div>
    )
  }

  return (
    <TooltipProvider delayDuration={300}>
      <div
        onDragEnter={handleDragEnter}
        onDragLeave={handleDragLeave}
        onDragOver={handleDragOver}
        onDrop={handleDrop}
        className={cn(
          CASE_ATTACHMENTS_CONTAINER_CLASS,
          "flex flex-col gap-0.5 transition-colors",
          isDragOver && "border-primary/50 bg-primary/5"
        )}
      >
        {attachments.map((attachment) => (
          <AttachmentRow
            key={attachment.id}
            attachment={attachment}
            onPreview={setPreviewAttachment}
            onDownload={handleDownload}
            onDelete={handleRequestDelete}
          />
        ))}
        <AddAttachmentRow
          isUploading={isUploading}
          onClick={handleAddAttachment}
        />
        <input
          ref={fileInputRef}
          type="file"
          multiple
          onChange={handleFileSelect}
          className="hidden"
          accept={acceptAttribute}
        />
      </div>

      <Dialog
        open={!!previewAttachment}
        onOpenChange={(open) => {
          if (!open) {
            setPreviewAttachment(null)
          }
        }}
      >
        <DialogContent
          title={previewAttachment?.file_name ?? "Attachment preview"}
          className="h-fit w-fit max-w-[95vw] overflow-hidden border-0 bg-transparent p-0 shadow-none [&>button]:hidden"
        >
          <div className="group relative inline-flex overflow-hidden rounded-md bg-black/80">
            <div className="absolute left-3 right-3 top-3 z-10 flex items-center justify-between opacity-0 transition-opacity duration-200 group-hover:opacity-100">
              <div className="rounded-full bg-black/70 px-3 py-1.5 backdrop-blur-sm">
                <span className="block max-w-[300px] truncate text-xs font-medium text-white">
                  {previewAttachment?.file_name}
                </span>
              </div>
              <button
                type="button"
                aria-label="Close preview"
                onClick={() => setPreviewAttachment(null)}
                className="rounded-full bg-black/70 p-2 text-white backdrop-blur-sm transition-colors duration-200 hover:bg-black/80"
              >
                <XIcon className="size-4" />
              </button>
            </div>
            {previewAttachment && (
              <AttachmentPreviewBody
                attachment={previewAttachment}
                objectUrl={previewObjectUrl}
                isError={previewIsError}
              />
            )}
          </div>
        </DialogContent>
      </Dialog>

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete attachment</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete "
              {attachmentPendingDelete?.file_name}"? This action cannot be
              undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMutation.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={deleteMutation.isPending}
            >
              {deleteMutation.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </TooltipProvider>
  )
}
