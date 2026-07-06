import type { ApiError } from "@/client"

interface ApiErrorDetail {
  error?: string
  message?: string
  allowed_extensions?: string[]
  current_count?: number
  max_count?: number
  current_size_mb?: number
  new_file_size_mb?: number
  max_size_mb?: number
}

interface ApiErrorBody {
  detail?: ApiErrorDetail | string
}

interface AttachmentUploadErrorToast {
  title: string
  description: string
}

function isApiError(error: unknown): error is ApiError {
  return (
    typeof error === "object" &&
    error !== null &&
    "status" in error &&
    typeof (error as { status: unknown }).status === "number"
  )
}

function getErrorDetail(error: ApiError): ApiErrorDetail | string | undefined {
  if (error.body && typeof error.body === "object") {
    return (error.body as ApiErrorBody).detail
  }
  return undefined
}

/**
 * Map a case attachment upload failure to a human-readable toast payload.
 *
 * Reuses the structured backend error codes for unsupported types, file size,
 * validation failures, and storage/attachment limits surfaced by the API.
 *
 * @param error - The error thrown by the upload mutation.
 * @param fileName - The name of the file the user tried to upload.
 * @returns A `{ title, description }` payload for the toast helper.
 */
export function describeAttachmentUploadError(
  error: unknown,
  fileName: string
): AttachmentUploadErrorToast {
  if (!isApiError(error)) {
    const message = error instanceof Error ? error.message : "Unknown error"
    return {
      title: "Upload failed",
      description: `Failed to upload ${fileName}. ${message}`,
    }
  }

  const detail = getErrorDetail(error)
  const detailObject = typeof detail === "object" ? detail : undefined

  switch (detailObject?.error) {
    case "unsupported_file_extension": {
      if (detailObject.allowed_extensions?.length === 0) {
        return {
          title: "Attachment uploads disabled",
          description: `${fileName} cannot be uploaded because uploads are disabled for this workspace.`,
        }
      }

      return {
        title: "File type not supported",
        description: `${fileName} cannot be uploaded. Allowed file types: ${
          detailObject.allowed_extensions?.join(", ") ||
          "txt, pdf, png, jpeg, gif, csv"
        }`,
      }
    }
    case "unsupported_content_type":
      return {
        title: "Content type not supported",
        description: `${fileName} has an unsupported content type. Please try a different file type.`,
      }
    case "file_too_large":
      return {
        title: "File too large",
        description: `${fileName} is too large to upload. ${
          detailObject.message || "Please choose a smaller file."
        }`,
      }
    case "storage_limit_exceeded": {
      let description = `Adding ${fileName} would exceed the case storage limit.`
      if (
        detailObject.current_size_mb &&
        detailObject.new_file_size_mb &&
        detailObject.max_size_mb
      ) {
        description = `Adding ${fileName} (${detailObject.new_file_size_mb}MB) would exceed the case storage limit. Current usage: ${detailObject.current_size_mb}MB of ${detailObject.max_size_mb}MB allowed.`
      }
      return {
        title: "Case storage limit exceeded",
        description: `${description} Please remove some attachments or choose a smaller file.`,
      }
    }
    case "max_attachments_exceeded": {
      let description =
        "This case already has the maximum number of attachments allowed."
      if (detailObject.current_count && detailObject.max_count) {
        description = `This case already has ${detailObject.current_count} of ${detailObject.max_count} attachments allowed.`
      }
      return {
        title: "Too many attachments",
        description: `${description} Please remove some attachments before adding new ones.`,
      }
    }
    case "file_validation_failed":
      return {
        title: "File validation failed",
        description: `${fileName} failed validation. Please check the file and try again.`,
      }
    default:
      break
  }

  let message = error.message || "Unknown error"
  if (detail) {
    message =
      typeof detail === "string"
        ? detail
        : detail.message || JSON.stringify(detail)
  }
  return {
    title: "Upload failed",
    description: `Failed to upload ${fileName}. ${message}`,
  }
}
