import { redirect } from "next/navigation"

export default async function WorkspacePage({
  params,
}: {
  params: { workspaceId: string }
}) {
  return redirect(`/workspaces/${params.workspaceId}/workflows`)
}
