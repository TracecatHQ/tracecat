"use client"

import { DownloadIcon, EyeIcon, LoaderIcon } from "lucide-react"
import { useEffect, useState } from "react"
import {
  type WorkflowExecutionObjectPreviewResponse,
  workflowExecutionsGetWorkflowExecutionObjectDownload,
  workflowExecutionsGetWorkflowExecutionObjectPreview,
} from "@/client"
import { CodeBlock } from "@/components/code-block"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import type { ExternalStoredObject } from "@/lib/stored-object"
import { useWorkspaceId } from "@/providers/workspace-id"

function formatSize(sizeBytes: number): string {
  if (sizeBytes === 0) {
    return "0 Bytes"
  }
  const units = ["Bytes", "KB", "MB", "GB", "TB"]
  const exponent = Math.min(
    Math.floor(Math.log(sizeBytes) / Math.log(1024)),
    units.length - 1
  )
  const value = sizeBytes / 1024 ** exponent
  return `${value.toFixed(exponent === 0 ? 0 : 1)} ${units[exponent]}`
}

function shouldProbeDownloadUrl(downloadUrl: string): boolean {
  try {
    const parsed = new URL(downloadUrl, window.location.href)
    return parsed.origin === window.location.origin
  } catch {
    return false
  }
}

export function ExternalObjectResult({
  executionId,
  eventId,
  external,
}: {
  executionId: string
  eventId: number
  external: ExternalStoredObject
}) {
  const workspaceId = useWorkspaceId()
  const [isDownloading, setIsDownloading] = useState(false)
  const [isPreviewing, setIsPreviewing] = useState(false)
  const [preview, setPreview] =
    useState<WorkflowExecutionObjectPreviewResponse | null>(null)

  const sizeLabel = formatSize(external.ref.size_bytes)

  useEffect(() => {
    // Clear preview when switching to a different event/object to avoid
    // showing stale content from a previous selection.
    setPreview(null)
  }, [eventId, executionId, external.ref.key, external.ref.sha256])

  const handleDownload = async () => {
    setIsDownloading(true)
    try {
      const response =
        await workflowExecutionsGetWorkflowExecutionObjectDownload({
          executionId,
          workspaceId,
          requestBody: {
            event_id: eventId,
          },
        })

      // Probe only for same-origin URLs. Cross-origin probes can fail due to
      // CORS/preflight while the browser download would still succeed.
      if (shouldProbeDownloadUrl(response.download_url)) {
        const probe = await fetch(response.download_url, {
          method: "GET",
          headers: {
            Range: "bytes=0-0",
          },
        })
        if (!probe.ok) {
          throw new Error(
            `Object download probe failed (${probe.status} ${probe.statusText})`
          )
        }
      }

      const link = document.createElement("a")
      link.href = response.download_url
      link.download = response.file_name
      link.rel = "noopener"
      link.style.display = "none"

      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to download object"
      toast({
        title: "Download failed",
        description: message,
      })
    } finally {
      setIsDownloading(false)
    }
  }

  const handlePreview = async () => {
    setIsPreviewing(true)
    try {
      const response =
        await workflowExecutionsGetWorkflowExecutionObjectPreview({
          executionId,
          workspaceId,
          requestBody: {
            event_id: eventId,
          },
        })
      setPreview(response)
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to preview object"
      toast({
        title: "Preview failed",
        description: message,
      })
    } finally {
      setIsPreviewing(false)
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-md border bg-muted-foreground/5 p-4 text-xs">
      <div className="flex items-start justify-between gap-2">
        <div className="space-y-1">
          <p className="text-foreground/80">
            This object is too large to load ({sizeLabel}).
          </p>
          <p className="text-muted-foreground">
            Download to inspect it, or fetch a truncated preview.
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() => void handleDownload()}
          disabled={isDownloading}
        >
          {isDownloading ? (
            <LoaderIcon className="mr-2 size-3 animate-spin" />
          ) : (
            <DownloadIcon className="mr-2 size-3" />
          )}
          Download file
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() => void handlePreview()}
          disabled={isPreviewing}
        >
          {isPreviewing ? (
            <LoaderIcon className="mr-2 size-3 animate-spin" />
          ) : (
            <EyeIcon className="mr-2 size-3" />
          )}
          Try preview
        </Button>
      </div>
      {preview && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Badge variant="secondary" className="text-[10px]">
              {preview.content_type}
            </Badge>
            {preview.truncated && (
              <Badge variant="secondary" className="text-[10px]">
                Preview truncated ({formatSize(preview.preview_bytes)})
              </Badge>
            )}
          </div>
          <CodeBlock title="Preview">{preview.content}</CodeBlock>
        </div>
      )}
    </div>
  )
}
