import { redirect } from "next/navigation"

export default async function AgentsPage({
  params,
}: {
  params: Promise<{ workspaceId: string; presetId?: string }>
}) {
  const { workspaceId, presetId } = await params
  const basePath = `/workspaces/${workspaceId}/agents`
  if (presetId) {
    return redirect(`${basePath}/${presetId}`)
  }
  return redirect(`${basePath}/new`)
}
