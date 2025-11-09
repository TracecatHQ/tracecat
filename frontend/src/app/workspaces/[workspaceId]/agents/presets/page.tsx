import { redirect } from "next/navigation"

export default async function AgentsPresetsPage({
  params,
}: {
  params: Promise<{ workspaceId: string; presetId?: string }>
}) {
  const { workspaceId, presetId } = await params
  const basePath = `/workspaces/${workspaceId}/agents/presets`
  if (presetId) {
    return redirect(`${basePath}/${presetId}`)
  }
  return redirect(`${basePath}/new`)
}
