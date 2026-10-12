import {
  type FormFieldConfig,
  formsDownloadFormFile,
  formsUploadFormFile,
} from "@/client"
import type { FormFileUploader } from "@/components/forms/widgets/types"
import { getFormErrorDetail } from "@/lib/forms/errors"
import { formatFileSize } from "@/lib/utils"

function getExtension(name: string): string {
  const dot = name.lastIndexOf(".")
  return dot > 0 ? name.slice(dot).toLowerCase() : ""
}

function matchesAccept(
  accept: string[],
  file: Pick<File, "name" | "type">
): boolean {
  const type = file.type.toLowerCase()
  return accept.some((entry) => {
    const pattern = entry.trim().toLowerCase()
    if (pattern.startsWith(".")) {
      return getExtension(file.name) === pattern
    }
    if (pattern.endsWith("/*")) {
      return type.startsWith(pattern.slice(0, -1))
    }
    return type === pattern
  })
}

/**
 * Check a picked file against a `file` field's config before it is uploaded.
 *
 * Mirrors the server's `accept` and size rules so an obvious mistake fails
 * without a request. The server checks again, and also checks the bytes and
 * the workspace's allowed types.
 *
 * @returns Why the file cannot be used, or null if it can.
 */
export function checkFormFile(
  file: Pick<File, "name" | "size" | "type">,
  config: FormFieldConfig | null | undefined
): string | null {
  const maxSize = config?.max_file_size
  if (typeof maxSize === "number" && file.size > maxSize) {
    return `Files can be at most ${formatFileSize(maxSize)}`
  }
  if (file.size === 0) {
    return "The file is empty"
  }
  const accept = config?.accept ?? []
  if (accept.length > 0 && !matchesAccept(accept, file)) {
    return `This field takes ${accept.join(", ")}`
  }
  return null
}

/**
 * Upload one file of a `file` field on a live form.
 *
 * Rejects with the server's message, which the widget shows on the file.
 */
export const uploadFormFile: FormFileUploader = async ({
  workspaceId,
  formId,
  field,
  file,
}) => {
  try {
    const uploaded = await formsUploadFormFile({
      workspaceId,
      formId,
      field,
      formData: { file },
    })
    return { id: uploaded.id }
  } catch (error) {
    throw new Error(getFormErrorDetail(error)?.message ?? "Upload failed")
  }
}

/** A file in a submission's answers, as the server expands a file ID. */
export interface FormFileAnswer {
  id: string
  name: string
  size: number
}

function isFileAnswer(value: unknown): value is FormFileAnswer {
  if (typeof value !== "object" || value === null) {
    return false
  }
  const record = value as Record<string, unknown>
  return (
    typeof record.id === "string" &&
    typeof record.name === "string" &&
    typeof record.size === "number" &&
    typeof record.sha256 === "string"
  )
}

/**
 * Read the files of an answer.
 *
 * @returns The files if the answer is a file or a non-empty list of files,
 * otherwise null.
 */
export function getFileAnswers(value: unknown): FormFileAnswer[] | null {
  const items = Array.isArray(value) ? value : [value]
  return items.length > 0 && items.every(isFileAnswer) ? items : null
}

/**
 * Download a submitted file through a short-lived URL.
 *
 * The URL forces a download, so the browser saves the file and stays on the
 * page.
 */
export async function downloadFormFile(args: {
  workspaceId: string
  formId: string
  fileId: string
}): Promise<void> {
  const { download_url: downloadUrl } = await formsDownloadFormFile(args)
  const link = document.createElement("a")
  link.href = downloadUrl
  link.rel = "noopener noreferrer"
  document.body.appendChild(link)
  link.click()
  link.remove()
}
