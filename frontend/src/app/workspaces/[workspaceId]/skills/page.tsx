import { SkillsStudio } from "@/components/skills/skills-studio"

/**
 * Workspace skills index page.
 *
 * @param props Route params.
 * @returns The skills studio with the full list visible.
 */
export default async function SkillsPage({
  params,
}: {
  params: Promise<{ workspaceId: string }>
}) {
  const { workspaceId } = await params
  return <SkillsStudio workspaceId={workspaceId} />
}
