import { client } from "@/lib/api"

export async function updateWebhook(
  workflowId: string,
  webhookId: string,
  params: {
    entrypointRef?: string | null
    method?: "GET" | "POST"
    status?: "online" | "offline"
  }
) {
  const response = await client.patch(
    `/workflows/${workflowId}/webhooks/${webhookId}`,
    params
  )
  return response.data
}
