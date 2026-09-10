import { redirect } from "next/navigation"

/** Workspace group management moved to organization settings. */
export default async function WorkspaceGroupsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>
}) {
  const { workspaceId } = await params
  redirect(`/workspaces/${workspaceId}/members`)
}
