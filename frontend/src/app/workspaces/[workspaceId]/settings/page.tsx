import { redirect } from "next/navigation"

export default async function SettingsPage({
  params,
}: {
  params: { workspaceId: string }
}) {
  return redirect(`/workspaces/${params.workspaceId}/settings/general`)
}
