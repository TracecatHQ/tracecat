"use client"

import { useQueryClient } from "@tanstack/react-query"
import { useCallback } from "react"
import { caseAttachmentsCreateAttachment } from "@/client"
import { toast } from "@/components/ui/use-toast"
import { describeAttachmentUploadError } from "@/lib/cases/attachment-errors"
import { invalidateCaseActivityQueries } from "@/lib/cases/invalidation"

/** Stable URI persisted in markdown for case attachment images. */
export const ATTACHMENT_URI_SCHEME = "attachment://"

interface CaseImageUploadResult {
  /** Stable `attachment://<caseId>/<attachmentId>` reference. */
  src: string
  /** Server-recorded file name for the uploaded attachment. */
  fileName: string
}

interface ParsedAttachmentRef {
  caseId: string
  attachmentId: string
}

/**
 * Build the stable attachment URI persisted in saved markdown.
 *
 * @param caseId - The owning case id.
 * @param attachmentId - The attachment id.
 * @returns A `attachment://<caseId>/<attachmentId>` URI.
 */
export function buildAttachmentSrc(
  caseId: string,
  attachmentId: string
): string {
  return `${ATTACHMENT_URI_SCHEME}${caseId}/${attachmentId}`
}

/**
 * Parse an `attachment://<caseId>/<attachmentId>` URI into its parts.
 *
 * @param src - The image src to parse.
 * @returns The parsed ids, or null when the src is not an attachment URI.
 */
export function parseAttachmentSrc(
  src: string | null | undefined
): ParsedAttachmentRef | null {
  if (!src || !src.startsWith(ATTACHMENT_URI_SCHEME)) {
    return null
  }
  const [caseId, attachmentId] = src
    .slice(ATTACHMENT_URI_SCHEME.length)
    .split("/")
  if (!caseId || !attachmentId) {
    return null
  }
  return { caseId, attachmentId }
}

/**
 * Extract image files from clipboard or drag data, preferring `files` over
 * `items` (some browsers expose pasted images only via `items`).
 *
 * @param data - The `DataTransfer` from a paste or drop event.
 * @returns The image files found, or an empty array.
 */
export function extractImageFiles(data: DataTransfer | null): File[] {
  if (!data) {
    return []
  }
  const fromFiles = Array.from(data.files).filter((file) =>
    file.type.startsWith("image/")
  )
  if (fromFiles.length > 0) {
    return fromFiles
  }
  const fromItems: File[] = []
  for (const item of Array.from(data.items)) {
    if (item.kind === "file" && item.type.startsWith("image/")) {
      const file = item.getAsFile()
      if (file) {
        fromItems.push(file)
      }
    }
  }
  return fromItems
}

/**
 * Wrap a pasted image blob in a File with a stable, timestamped name.
 *
 * Clipboard blobs often lack a meaningful name/extension, so we derive one
 * from the MIME type (e.g. `pasted-image-2026-07-06T12-30-00.png`).
 *
 * @param blob - The pasted image blob.
 * @returns A named `File` ready for upload.
 */
export function createPastedImageFile(blob: Blob): File {
  const extension = blob.type.split("/")[1]?.split(";")[0] || "png"
  const timestamp = new Date().toISOString().slice(0, 19).replace(/:/g, "-")
  return new File([blob], `pasted-image-${timestamp}.${extension}`, {
    type: blob.type,
  })
}

/**
 * Upload case images as attachments and produce stable markdown references.
 *
 * Used by both the description editor (TipTap) and the comment composers
 * (plain textareas) so paste/drop behaviour stays consistent. On success the
 * case attachments list and activity queries are invalidated; on failure a
 * descriptive toast is shown and the error is rethrown for the caller.
 *
 * @param caseId - The case to attach images to.
 * @param workspaceId - The workspace that owns the case.
 * @returns An `uploadImage` callback returning the stable src and file name.
 */
export function useCaseImageUpload(caseId: string, workspaceId: string) {
  const queryClient = useQueryClient()

  const uploadImage = useCallback(
    async (file: File): Promise<CaseImageUploadResult> => {
      try {
        const created = await caseAttachmentsCreateAttachment({
          caseId,
          workspaceId,
          formData: { file },
        })
        queryClient.invalidateQueries({
          queryKey: ["case-attachments", caseId, workspaceId],
        })
        invalidateCaseActivityQueries(queryClient, caseId, workspaceId)
        return {
          src: buildAttachmentSrc(caseId, created.id),
          fileName: created.file_name,
        }
      } catch (error) {
        toast(describeAttachmentUploadError(error, file.name))
        throw error
      }
    },
    [caseId, workspaceId, queryClient]
  )

  return { uploadImage }
}
