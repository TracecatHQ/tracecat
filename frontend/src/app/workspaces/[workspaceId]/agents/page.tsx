import { redirect } from "next/navigation"

export default function AgentsPage({
  params,
}: {
  params: { workspaceId: string }
}) {
  redirect(`/workspaces/${params.workspaceId}/agents/new`)
}
