"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { formatDistanceToNow } from "date-fns"
import {
  AlertCircle,
  Download,
  Eye,
  FileIcon,
  FileSpreadsheet,
  FileText,
  ImageIcon,
  Music,
  Paperclip,
  Plus,
  Presentation,
  Trash2,
  Video,
  XIcon,
} from "lucide-react"
import Image from "next/image"
import { useCallback, useRef, useState } from "react"
import type { ApiError, CaseAttachmentRead } from "@/client"
import {
  caseAttachmentsCreateAttachment,
  caseAttachmentsDeleteAttachment,
  caseAttachmentsDownloadAttachment,
  caseAttachmentsListAttachments,
} from "@/client"
import { Button } from "@/components/ui/button"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { invalidateCaseActivityQueries } from "@/lib/cases/invalidation"
import { cn } from "@/lib/utils"

interface CaseAttachmentsSectionProps {
  caseId: string
  workspaceId: string
}

interface CaseAttachmentDownloadResponse {
  download_url: string
  file_name: string
  content_type: string
}

// Type definitions for API error responses
interface ApiErrorDetail {
  error?: string
  message?: string
  allowed_extensions?: string[]
  // Max attachments exceeded error fields
  current_count?: number
  max_count?: number
  // Storage limit exceeded error fields
  current_size_mb?: number
  new_file_size_mb?: number
  max_size_mb?: number
}

interface ApiErrorBody {
  detail?: ApiErrorDetail | string
}

function getFileIcon(contentType: string) {
  if (contentType.startsWith("image/")) return <ImageIcon className="h-4 w-4" />
  if (contentType === "application/pdf") return <FileText className="h-4 w-4" />
  if (contentType.includes("spreadsheet"))
    return <FileSpreadsheet className="h-4 w-4" />
  if (contentType.includes("presentation"))
    return <Presentation className="h-4 w-4" />
  if (contentType.startsWith("audio/")) return <Music className="h-4 w-4" />
  if (contentType.startsWith("video/")) return <Video className="h-4 w-4" />
  return <FileIcon className="h-4 w-4" />
}

function getFileColor(contentType: string) {
  if (contentType.startsWith("image/")) return "text-green-600 bg-green-50"
  if (contentType === "application/pdf") return "text-red-600 bg-red-50"
  if (contentType.includes("spreadsheet"))
    return "text-emerald-600 bg-emerald-50"
  if (contentType.includes("presentation"))
    return "text-orange-600 bg-orange-50"
  if (contentType.startsWith("audio/")) return "text-purple-600 bg-purple-50"
  if (contentType.startsWith("video/")) return "text-blue-600 bg-blue-50"
  return "text-gray-600 bg-gray-50"
}

function formatFileSize(bytes: number): string {
  if (bytes === 0) return "0 Bytes"
  const k = 1024
  const sizes = ["Bytes", "KB", "MB", "GB"]
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return Number.parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i]
}

function truncateHash(hash: string): string {
  return hash.substring(0, 8)
}

function getUploaderName(creatorId: string | null | undefined): string {
  if (!creatorId) return "Unknown"
  return "User"
}

// Add constant for max file size (20MB in bytes)
const MAX_FILE_SIZE_BYTES = 20 * 1024 * 1024 // 20MB

export function CaseAttachmentsSection({
  caseId,
  workspaceId,
}: CaseAttachmentsSectionProps) {
  const [isUploading, setIsUploading] = useState(false)
  const [isDragOver, setIsDragOver] = useState(false)
  const [previewAttachment, setPreviewAttachment] =
    useState<CaseAttachmentRead | null>(null)
  const [previewImageUrl, setPreviewImageUrl] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  // Get workspace settings for allowed file extensions
  const { workspace } = useWorkspaceDetails()

  // Create accept attribute from workspace settings
  const acceptedExtensions =
    workspace?.settings?.effective_allowed_attachment_extensions
  const acceptAttribute = acceptedExtensions?.join(",") || undefined

  // Fetch attachments from API
  const {
    data: attachments = [],
    isLoading: attachmentsLoading,
    error: attachmentsError,
  } = useQuery<CaseAttachmentRead[], ApiError>({
    queryKey: ["case-attachments", caseId, workspaceId],
    queryFn: async () =>
      await caseAttachmentsListAttachments({ caseId, workspaceId }),
  })

  // Upload attachment mutation
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
      setIsUploading(false)
      toast({
        title: "Attachment uploaded successfully",
        description: `${file.name} has been added to the case`,
      })
    },
    onError: (error: ApiError, file) => {
      setIsUploading(false)

      // Handle structured error responses with specific HTTP status codes
      if (
        error.status === 415 &&
        error.body &&
        typeof error.body === "object"
      ) {
        const body = error.body as ApiErrorBody
        const detail = body.detail

        if (
          typeof detail === "object" &&
          detail?.error === "unsupported_file_extension"
        ) {
          toast({
            title: "File type not supported",
            description: `${file.name} cannot be uploaded. Allowed file types: ${detail.allowed_extensions?.join(", ") || "txt, pdf, png, jpeg, gif, csv"}`,
          })
          return
        }

        if (
          typeof detail === "object" &&
          detail?.error === "unsupported_content_type"
        ) {
          toast({
            title: "Content type not supported",
            description: `${file.name} has an unsupported content type. Please try a different file type.`,
          })
          return
        }
      }

      if (
        error.status === 413 &&
        error.body &&
        typeof error.body === "object"
      ) {
        const body = error.body as ApiErrorBody
        const detail = body.detail

        if (typeof detail === "object" && detail?.error === "file_too_large") {
          toast({
            title: "File too large",
            description: `${file.name} is too large to upload. ${detail.message || "Please choose a smaller file."}`,
          })
          return
        }

        if (
          typeof detail === "object" &&
          detail?.error === "storage_limit_exceeded"
        ) {
          let description = `Adding ${file.name} would exceed the case storage limit.`

          if (
            detail.current_size_mb &&
            detail.new_file_size_mb &&
            detail.max_size_mb
          ) {
            description = `Adding ${file.name} (${detail.new_file_size_mb}MB) would exceed the case storage limit. Current usage: ${detail.current_size_mb}MB of ${detail.max_size_mb}MB allowed.`
          }

          toast({
            title: "Case storage limit exceeded",
            description: `${description} Please remove some attachments or choose a smaller file.`,
          })
          return
        }
      }

      if (
        error.status === 409 &&
        error.body &&
        typeof error.body === "object"
      ) {
        const body = error.body as ApiErrorBody
        const detail = body.detail

        if (
          typeof detail === "object" &&
          detail?.error === "max_attachments_exceeded"
        ) {
          let description =
            "This case already has the maximum number of attachments allowed."

          if (detail.current_count && detail.max_count) {
            description = `This case already has ${detail.current_count} of ${detail.max_count} attachments allowed.`
          }

          toast({
            title: "Too many attachments",
            description: `${description} Please remove some attachments before adding new ones.`,
          })
          return
        }
      }

      if (
        error.status === 422 &&
        error.body &&
        typeof error.body === "object"
      ) {
        const body = error.body as ApiErrorBody
        const detail = body.detail

        if (
          typeof detail === "object" &&
          detail?.error === "security_threat_detected"
        ) {
          toast({
            title: "Security threat detected",
            description: `${file.name} contains potentially dangerous content and cannot be uploaded.`,
          })
          return
        }
      }

      if (
        error.status === 400 &&
        error.body &&
        typeof error.body === "object"
      ) {
        const body = error.body as ApiErrorBody
        const detail = body.detail

        if (
          typeof detail === "object" &&
          detail?.error === "file_validation_failed"
        ) {
          toast({
            title: "File validation failed",
            description: `${file.name} failed validation. Please check the file and try again.`,
          })
          return
        }
      }

      // Fallback for other errors
      let errorMessage = error.message || "Unknown error"
      if (error.body && typeof error.body === "object") {
        const body = error.body as ApiErrorBody
        if (body.detail) {
          errorMessage =
            typeof body.detail === "string"
              ? body.detail
              : body.detail.message || JSON.stringify(body.detail)
        }
      }

      toast({
        title: "Upload failed",
        description: `Failed to upload ${file.name}. ${errorMessage}`,
      })
    },
  })

  // Delete attachment mutation
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
      toast({
        title: "Attachment deleted",
        description: "The attachment has been removed from the case",
      })
    },
    onError: (error: ApiError) => {
      console.error("Failed to delete attachment:", error)

      // Extract error message from the API error
      let errorMessage = error.message || "Unknown error"
      if (error.body && typeof error.body === "object") {
        const body = error.body as ApiErrorBody
        if (body.detail) {
          errorMessage =
            typeof body.detail === "string"
              ? body.detail
              : body.detail.message || JSON.stringify(body.detail)
        }
      }

      toast({
        title: "Delete failed",
        description: `Failed to delete attachment. ${errorMessage}`,
      })
    },
  })

  // Add file validation function
  const validateFile = (file: File): boolean => {
    if (file.size > MAX_FILE_SIZE_BYTES) {
      toast({
        title: "File too large",
        description: `${file.name} is ${formatFileSize(file.size)}. Maximum file size is 20MB.`,
      })
      return false
    }
    return true
  }

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) {
      // Validate file size before uploading
      if (!validateFile(file)) {
        return
      }
      setIsUploading(true)
      uploadMutation.mutate(file)
    }
    // Reset input
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
  }

  const handleAddAttachment = () => {
    fileInputRef.current?.click()
  }

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDragOver(false)
  }, [])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
  }, [])

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
      setIsDragOver(false)

      const files = Array.from(e.dataTransfer.files)
      const file = files[0] // Only handle the first file

      if (file) {
        // Validate file size before uploading
        if (!validateFile(file)) {
          return
        }
        setIsUploading(true)
        uploadMutation.mutate(file)
      }
    },
    [uploadMutation]
  )

  const handleDownload = async (attachment: CaseAttachmentRead) => {
    try {
      const response = (await caseAttachmentsDownloadAttachment({
        caseId,
        workspaceId,
        attachmentId: attachment.id,
      })) as CaseAttachmentDownloadResponse

      // Response now contains presigned URL, not binary data
      const downloadUrl = response.download_url

      if (!downloadUrl) {
        throw new Error("No download URL received from server")
      }

      // Create a hidden link element and trigger download
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

  const handlePreview = async (attachment: CaseAttachmentRead) => {
    try {
      const response = (await caseAttachmentsDownloadAttachment({
        caseId,
        workspaceId,
        attachmentId: attachment.id,
        preview: true, // Request preview mode for safe inline display
      })) as CaseAttachmentDownloadResponse

      const downloadUrl = response.download_url
      if (!downloadUrl) {
        throw new Error("No download URL received from server")
      }

      console.log("Preview URL:", downloadUrl)

      setPreviewAttachment(attachment)
      setPreviewImageUrl(downloadUrl)
    } catch (error) {
      console.error("Failed to preview attachment:", error)
      toast({
        title: "Preview failed",
        description: `Failed to preview ${attachment.file_name}`,
      })
    }
  }

  const handleDelete = (attachmentId: string) => {
    deleteMutation.mutate(attachmentId)
  }

  if (attachmentsLoading) {
    return (
      <div className="mx-auto w-full">
        <div className="space-y-4 p-4">
          <div className="flex items-center gap-2 p-1.5 rounded-md border border-dashed border-muted-foreground/25">
            <div className="p-1.5 rounded bg-muted">
              <Skeleton className="h-3.5 w-3.5" />
            </div>
            <Skeleton className="h-3 w-32" />
          </div>
          {Array.from({ length: 2 }).map((_, i) => (
            <div
              key={i}
              className="flex items-center gap-4 p-2 px-3.5 rounded-md"
            >
              <Skeleton className="h-6 w-6 rounded" />
              <div className="flex-1 space-y-1">
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (attachmentsError) {
    return (
      <div className="mx-auto w-full">
        <div className="flex items-center justify-center p-8">
          <div className="flex items-center gap-2 text-red-600">
            <AlertCircle className="h-4 w-4" />
            <span className="text-sm">Failed to load attachments</span>
          </div>
        </div>
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="mx-auto w-full">
        <div className="space-y-4 p-4">
          <div
            onClick={handleAddAttachment}
            onDragEnter={handleDragEnter}
            onDragLeave={handleDragLeave}
            onDragOver={handleDragOver}
            onDrop={handleDrop}
            className={cn(
              "flex items-center gap-2 p-1.5 rounded-md border border-dashed transition-all cursor-pointer group",
              isDragOver
                ? "border-blue-500 bg-blue-50 dark:bg-blue-950/20"
                : "border-muted-foreground/25 hover:border-muted-foreground/50 hover:bg-muted/30"
            )}
          >
            <div className="p-1.5 rounded bg-muted group-hover:bg-muted-foreground/10 transition-colors">
              <Plus className="h-3.5 w-3.5 text-muted-foreground" />
            </div>
            <span className="text-xs text-muted-foreground group-hover:text-foreground transition-colors">
              {isUploading || uploadMutation.isPending
                ? "Uploading..."
                : "Add new attachment (max 20MB)"}
            </span>
          </div>

          {/* Attachments list */}
          {attachments.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-4">
              <div className="p-2 rounded-full bg-muted/50 mb-3">
                <Paperclip className="h-6 w-6 text-muted-foreground" />
              </div>
              <h3 className="text-sm font-medium text-muted-foreground mb-1">
                No attachments found
              </h3>
              <p className="text-xs text-muted-foreground/75 text-center max-w-[250px]">
                Add files by clicking the add button above or drag and drop
                files directly.
              </p>
            </div>
          ) : (
            <div className="space-y-1">
              {attachments.map((attachment) => {
                return (
                  <div
                    key={attachment.id}
                    className="flex items-center gap-4 p-2 px-3.5 rounded-md hover:bg-muted/40 transition-colors group"
                  >
                    <div
                      className={`p-1 rounded ${getFileColor(attachment.content_type)}`}
                    >
                      {getFileIcon(attachment.content_type)}
                    </div>

                    <div className="flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="font-medium text-sm truncate">
                          {attachment.file_name}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          ({formatFileSize(attachment.size)})
                        </span>
                      </div>

                      <div className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span>
                          by {getUploaderName(attachment.creator_id)} •{" "}
                          {formatDistanceToNow(
                            new Date(attachment.created_at),
                            {
                              addSuffix: true,
                            }
                          )}
                        </span>

                        {/* Short SHA */}
                        <span className="font-mono text-xs bg-muted px-1 py-0.5 rounded">
                          {truncateHash(attachment.sha256)}
                        </span>
                      </div>
                    </div>

                    <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                      {/* Preview button - only for images */}
                      {attachment.content_type.startsWith("image/") && (
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => handlePreview(attachment)}
                              disabled={deleteMutation.isPending}
                            >
                              <Eye className="size-3.5" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>
                            <div className="text-xs">Preview image</div>
                          </TooltipContent>
                        </Tooltip>
                      )}

                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => handleDownload(attachment)}
                            disabled={deleteMutation.isPending}
                          >
                            <Download className="size-3.5" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <div className="text-xs">Download attachment</div>
                        </TooltipContent>
                      </Tooltip>

                      <Button
                        variant="ghost"
                        size="sm"
                        className="text-red-600 hover:text-red-700 hover:bg-red-50"
                        title="Delete attachment"
                        onClick={() => handleDelete(attachment.id)}
                        disabled={deleteMutation.isPending}
                      >
                        <Trash2 className="size-3.5" />
                      </Button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          <input
            ref={fileInputRef}
            type="file"
            onChange={handleFileSelect}
            className="hidden"
            accept={acceptAttribute}
          />

          {/* Image Preview Modal */}
          <Dialog
            open={!!previewAttachment}
            onOpenChange={(open) => {
              if (!open) {
                setPreviewAttachment(null)
                setPreviewImageUrl(null)
              }
            }}
          >
            <DialogContent className="max-w-[95vw] max-h-[95vh] overflow-hidden p-0 bg-transparent border-0 shadow-2xl w-fit h-fit">
              <div className="relative inline-flex border border-gray-400/25 rounded-sm overflow-hidden bg-gray-900 group">
                {/* Floating header overlay */}
                <div className="absolute top-3 left-3 right-3 z-10 flex items-center justify-between opacity-0 group-hover:opacity-100 transition-opacity duration-200">
                  <div className="bg-black/70 backdrop-blur-sm rounded-full px-3 py-1.5">
                    <span className="text-white text-xs font-medium truncate max-w-[300px] block">
                      {previewAttachment?.file_name}
                    </span>
                  </div>
                  <button
                    onClick={() => {
                      setPreviewAttachment(null)
                      setPreviewImageUrl(null)
                    }}
                    className="bg-black/70 backdrop-blur-sm rounded-full p-2 text-white hover:bg-black/80 transition-colors duration-200"
                  >
                    <XIcon className="w-4 h-4" />
                  </button>
                </div>

                {previewImageUrl && (
                  <Image
                    src={previewImageUrl}
                    alt={previewAttachment?.file_name || "Preview image"}
                    width={1}
                    height={0}
                    sizes="(max-width: 768px) 100vw, (max-width: 1200px) 90vw, 85vw"
                    style={{
                      maxWidth: "90vw",
                      maxHeight: "85vh",
                      width: "auto",
                      height: "auto",
                    }}
                    className="object-contain"
                    unoptimized
                    onError={(e) => {
                      console.error("Image failed to load:", e)
                      console.error("Failed URL:", previewImageUrl)
                      toast({
                        title: "Image preview failed",
                        description:
                          "Try downloading the attachment or checking the original file for issues.",
                      })
                    }}
                    onLoad={() => {
                      console.log("Image loaded successfully:", previewImageUrl)
                    }}
                  />
                )}
              </div>
            </DialogContent>
          </Dialog>
        </div>
      </div>
    </TooltipProvider>
  )
}
