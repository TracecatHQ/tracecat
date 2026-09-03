import { SkillsDashboard } from "@/components/skills/skills-dashboard"

/**
 * Workspace skills index page.
 *
 * @param props Route params.
 * @returns The skills dashboard list view.
 */
export default async function SkillsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>
}) {
  const { workspaceId } = await params
  return <SkillsDashboard workspaceId={workspaceId} />
}
