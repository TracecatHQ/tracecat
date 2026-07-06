"use client"

import { useQuery } from "@tanstack/react-query"
import { Image, type ImageOptions } from "@tiptap/extension-image"
import {
  type NodeViewProps,
  NodeViewWrapper,
  ReactNodeViewRenderer,
} from "@tiptap/react"
import { ImageOff } from "lucide-react"
import * as React from "react"
import { caseAttachmentsDownloadAttachment } from "@/client"
import { Skeleton } from "@/components/ui/skeleton"
import { parseAttachmentSrc } from "@/lib/cases/use-case-image-upload"
import { cn } from "@/lib/utils"

export interface AttachmentImageOptions extends ImageOptions {
  /** Workspace that owns the attachments referenced by `attachment://` srcs. */
  workspaceId: string | null
}

/**
 * Resolve a case attachment image to a short-lived object URL.
 *
 * The presigned URL returned by the API expires quickly, so we fetch the blob
 * once (cached by React Query) and expose an object URL for the lifetime of the
 * node view, revoking it on unmount.
 */
function useAttachmentObjectUrl(
  workspaceId: string | null,
  caseId: string | undefined,
  attachmentId: string | undefined
): { objectUrl: string | null; isLoading: boolean; isError: boolean } {
  const enabled = Boolean(workspaceId && caseId && attachmentId)

  const {
    data: blob,
    isLoading,
    isError,
  } = useQuery({
    queryKey: ["case-attachment-image", workspaceId, caseId, attachmentId],
    enabled,
    staleTime: Number.POSITIVE_INFINITY,
    gcTime: 5 * 60 * 1000,
    retry: false,
    queryFn: async () => {
      const response = await caseAttachmentsDownloadAttachment({
        caseId: caseId as string,
        workspaceId: workspaceId as string,
        attachmentId: attachmentId as string,
        preview: true,
      })
      const fetched = await fetch(response.download_url)
      if (!fetched.ok) {
        throw new Error(`Failed to load image (${fetched.status})`)
      }
      return await fetched.blob()
    },
  })

  const [objectUrl, setObjectUrl] = React.useState<string | null>(null)

  React.useEffect(() => {
    if (!blob) {
      return
    }
    const url = URL.createObjectURL(blob)
    setObjectUrl(url)
    return () => {
      URL.revokeObjectURL(url)
      setObjectUrl(null)
    }
  }, [blob])

  return {
    objectUrl,
    isLoading: enabled && isLoading,
    isError: enabled && isError,
  }
}

function AttachmentImagePlaceholder({ label }: { label: string }) {
  return (
    <span className="inline-flex items-center gap-2 rounded-md border border-dashed border-border/70 bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
      <ImageOff className="size-3.5" />
      {label}
    </span>
  )
}

function AttachmentImageNodeView({ node, extension, selected }: NodeViewProps) {
  const src = (node.attrs.src as string | null) ?? null
  const alt = (node.attrs.alt as string | null) ?? ""
  const title = (node.attrs.title as string | null) ?? undefined
  const ref = parseAttachmentSrc(src)
  const workspaceId =
    (extension.options as AttachmentImageOptions).workspaceId ?? null

  const { objectUrl, isLoading, isError } = useAttachmentObjectUrl(
    workspaceId,
    ref?.caseId,
    ref?.attachmentId
  )

  const wrapperClass = cn(
    "attachment-image",
    selected && "ProseMirror-selectednode"
  )

  // Non-attachment srcs (plain https URLs) render directly.
  if (!ref) {
    if (!src) {
      return (
        <NodeViewWrapper className={wrapperClass}>
          <AttachmentImagePlaceholder label="Image unavailable" />
        </NodeViewWrapper>
      )
    }
    return (
      <NodeViewWrapper className={wrapperClass}>
        <img src={src} alt={alt} title={title} />
      </NodeViewWrapper>
    )
  }

  if (isError) {
    return (
      <NodeViewWrapper className={wrapperClass}>
        <AttachmentImagePlaceholder label={alt || "Image unavailable"} />
      </NodeViewWrapper>
    )
  }

  if (!objectUrl) {
    return (
      <NodeViewWrapper className={wrapperClass}>
        <Skeleton
          className="h-40 w-full max-w-sm rounded-md"
          aria-label={isLoading ? "Loading image" : undefined}
        />
      </NodeViewWrapper>
    )
  }

  return (
    <NodeViewWrapper className={wrapperClass}>
      <img src={objectUrl} alt={alt} title={title} />
    </NodeViewWrapper>
  )
}

/**
 * Image extension that resolves `attachment://<caseId>/<attachmentId>` srcs to
 * short-lived object URLs at render time while persisting the stable URI in
 * markdown. Falls back to plain `<img>` rendering for regular URLs.
 */
export const AttachmentImage = Image.extend<AttachmentImageOptions>({
  addOptions() {
    return {
      ...this.parent?.(),
      workspaceId: null,
    } as AttachmentImageOptions
  },
  addNodeView() {
    return ReactNodeViewRenderer(AttachmentImageNodeView)
  },
})
