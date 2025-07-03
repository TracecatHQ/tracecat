"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { formatDistanceToNow } from "date-fns"
import {
  AlertCircle,
  Download,
  File,
  FileSpreadsheet,
  FileText,
  ImageIcon,
  Music,
  Plus,
  Presentation,
  Trash2,
  Video,
} from "lucide-react"
import { useRef, useState } from "react"
import type { ApiError, CaseAttachmentRead } from "@/client"
import {
  casesCreateAttachment,
  casesDeleteAttachment,
  casesDownloadAttachment,
  casesListAttachments,
} from "@/client"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"

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
  return <File className="h-4 w-4" />
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

export function CaseAttachmentsSection({
  caseId,
  workspaceId,
}: CaseAttachmentsSectionProps) {
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const queryClient = useQueryClient()

  // Fetch attachments from API
  const {
    data: attachments = [],
    isLoading: attachmentsLoading,
    error: attachmentsError,
  } = useQuery<CaseAttachmentRead[], ApiError>({
    queryKey: ["case-attachments", caseId, workspaceId],
    queryFn: async () => await casesListAttachments({ caseId, workspaceId }),
  })

  // Upload attachment mutation
  const uploadMutation = useMutation({
    mutationFn: async (file: File) => {
      return await casesCreateAttachment({
        caseId,
        workspaceId,
        formData: {
          file,
        },
      })
    },
    onSuccess: (data, file) => {
      queryClient.invalidateQueries({
        queryKey: ["case-attachments", caseId, workspaceId],
      })
      setIsUploading(false)
      toast({
        title: "Attachment uploaded successfully",
        description: `${file.name} has been added to the case`,
      })
    },
    onError: (error: ApiError, file) => {
      console.error("Failed to upload attachment:", error)
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
            description: `${file.name} has an unsupported content type. ${detail.message || "Please try a different file type."}`,
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
            description: `${file.name} failed validation. ${detail.message || "Please check the file and try again."}`,
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
      await casesDeleteAttachment({
        caseId,
        workspaceId,
        attachmentId,
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-attachments", caseId, workspaceId],
      })
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

  const handleFileSelect = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    if (file) {
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

  const handleDownload = async (attachment: CaseAttachmentRead) => {
    try {
      const response = (await casesDownloadAttachment({
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
      link.style.display = "none"

      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    } catch (error) {
      console.error("Failed to download attachment:", error)
      toast.error("Download failed", {
        description: `Failed to download ${attachment.file_name}`,
      })
    }
  }

  const handleDelete = (attachmentId: string) => {
    deleteMutation.mutate(attachmentId)
  }

  if (attachmentsLoading) {
    return (
      <div className="space-y-1">
        <div className="flex items-center gap-2 p-1.5 m-2 rounded-md border border-dashed border-muted-foreground/25">
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
    )
  }

  if (attachmentsError) {
    return (
      <div className="flex items-center justify-center p-8">
        <div className="flex items-center gap-2 text-red-600">
          <AlertCircle className="h-4 w-4" />
          <span className="text-sm">Failed to load attachments</span>
        </div>
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="space-y-1">
        <div
          onClick={handleAddAttachment}
          className="flex items-center gap-2 p-1.5 m-2 rounded-md border border-dashed border-muted-foreground/25 hover:border-muted-foreground/50 hover:bg-muted/30 transition-all cursor-pointer group"
        >
          <div className="p-1.5 rounded bg-muted group-hover:bg-muted-foreground/10 transition-colors">
            <Plus className="h-3.5 w-3.5 text-muted-foreground" />
          </div>
          <span className="text-xs text-muted-foreground group-hover:text-foreground transition-colors">
            {isUploading || uploadMutation.isPending
              ? "Uploading..."
              : "Add new attachment (txt, pdf, png, jpeg, gif, csv)"}
          </span>
        </div>

        {/* Attachments list */}
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
                    {formatDistanceToNow(new Date(attachment.created_at), {
                      addSuffix: true,
                    })}
                  </span>

                  {/* Short SHA */}
                  <span className="font-mono text-xs bg-muted px-1 py-0.5 rounded">
                    {truncateHash(attachment.sha256)}
                  </span>
                </div>
              </div>

              <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
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

        <input
          ref={fileInputRef}
          type="file"
          onChange={handleFileSelect}
          className="hidden"
          accept="*/*"
        />
      </div>
    </TooltipProvider>
  )
}
