"use client"

import { useEffect, useState } from "react"
import { caseAttachmentsDownloadAttachment } from "@/client"
import { useQuery } from "@/lib/query"

/**
 * Resolve a case attachment image to a short-lived object URL.
 *
 * The presigned URL returned by the API expires quickly, so we fetch the blob
 * once (cached by React Query) and expose an object URL for the lifetime of
 * the caller, revoking it on unmount.
 *
 * @param workspaceId - Workspace that owns the attachment.
 * @param caseId - Case the attachment belongs to.
 * @param attachmentId - Attachment to resolve; `undefined` disables the fetch.
 * @returns The object URL once resolved, plus loading and error flags.
 */
export function useAttachmentObjectUrl(
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

  const [objectUrl, setObjectUrl] = useState<string | null>(null)

  useEffect(() => {
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
