import { formsDownloadFormFile, formsUploadFormFile } from "@/client"
import { getFormErrorDetail } from "@/lib/forms/errors"

/** Uploads one file of a `file` field and resolves to its file ID. */
export type FormFileUploader = (args: {
  workspaceId: string
  formId: string
  field: string
  file: File
}) => Promise<{ id: string }>

/**
 * Upload one file of a `file` field on a live form. Rejects with the server's
 * message, which the widget shows on the file.
 */
export const uploadFormFile: FormFileUploader = async ({ file, ...scope }) => {
  try {
    const uploaded = await formsUploadFormFile({ ...scope, formData: { file } })
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
  const file = value as Record<string, unknown> | null | undefined
  return (
    typeof file?.id === "string" &&
    typeof file.name === "string" &&
    typeof file.size === "number" &&
    typeof file.sha256 === "string"
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
