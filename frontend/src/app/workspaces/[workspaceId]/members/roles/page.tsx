import { redirect } from "next/navigation"

/** Workspace role management moved to organization settings. */
export default async function WorkspaceRolesPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>
}) {
  const { workspaceId } = await params
  redirect(`/workspaces/${workspaceId}/members`)
}
