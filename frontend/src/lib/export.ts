import { WorkflowsExportWorkflowData } from "@/client"

import { client } from "@/lib/api"

export async function exportWorkflowJson({
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

  let filename = `${workflowId}.json`
  if (contentDisposition) {
    const filenameMatch = (contentDisposition as string).match(
      /filename="?(.+)"?/
    )
    if (filenameMatch && filenameMatch.length > 1) {
      filename = filenameMatch[1]
    } else {
      console.warn("Failed to extract filename from Content-Disposition")
    }
  }

  console.log("Downloading workflow definition:", filename)
  const jsonData = JSON.stringify(response.data, null, 2)
  const blob = new Blob([jsonData], { type: "application/json" })
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
