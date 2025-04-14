import { redirect } from "next/navigation"

export default async function WorkspacePage({
  params,
}: {
  params: Promise<{ workspaceId: string }>
}) {
  const { workspaceId } = await params
  return redirect(`/workspaces/${workspaceId}/workflows`)
}
