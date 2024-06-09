import { client } from "@/lib/api"

export async function updateWebhook(
  workflowId: string,
  params: {
    entrypointRef?: string | null
    method?: "GET" | "POST"
    status?: "online" | "offline"
  }
) {
  const response = await client.patch(
    `/workflows/${workflowId}/webhook`,
    params
  )
  return response.data
}
