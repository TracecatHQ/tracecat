import { AxiosError } from "axios"
import type { WorkflowsExportWorkflowData } from "@/client"

import { client } from "@/lib/api"

export async function exportWorkflow({
  workspaceId,
  workflowId,
  format,
  version,
}: WorkflowsExportWorkflowData) {
  const response = await client.get(`/workflows/${workflowId}/export`, {
    params: { version, format, workspace_id: workspaceId },
  })

  // Extract the filename from the Content-Disposition header
  const contentDisposition = response.headers["content-disposition"]

  const filename =
    filenameFromHeader(contentDisposition) || `${workflowId}.${format}`
  const contentType = response.headers["content-type"]

  console.log("Downloading workflow definition:", filename)
  // If the format is YAML, make sure the conversion doesn't introduce issues
  let data: string
  if (format === "yaml") {
    // YAML is already a string, so no need to stringify
    data = response.data
  } else {
    data = JSON.stringify(response.data, null, 2)
  }
  const blob = new Blob([data], { type: contentType })
  const downloadUrl = window.URL.createObjectURL(blob)
  const a = document.createElement("a")
  try {
    a.href = downloadUrl
    a.download = filename
    document.body.appendChild(a) // Required for Firefox
    a.click()
  } finally {
    a.remove() // Clean up
    window.URL.revokeObjectURL(downloadUrl)
  }
}

function filenameFromHeader(contentDisposition: string): string | null {
  const filenameMatch = contentDisposition.match(/filename="?(.+)"?/)
  if (filenameMatch && filenameMatch.length > 1) {
    return filenameMatch[1]
  }
  return null
}

export function handleExportError(error: Error) {
  console.error("Failed to download workflow YAML / JSON:", error)
  const toastData = {
    title: "Error exporting workflow",
    description: "Could not export workflow. Please try again.",
  }

  if (error instanceof AxiosError && error.response?.status === 404) {
    toastData.title = "No workflow version found"
    toastData.description =
      "Cannot export uncommitted workflow. Please commit changes to create a versioned workflow."
  } else {
    toastData.description += ` ${error.message}`
  }
  return toastData
}
