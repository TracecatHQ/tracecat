import { SkillsStudio } from "@/components/skills/skills-studio"

/**
 * Workspace skill detail page.
 *
 * @param props Route params.
 * @returns The skills studio with the requested skill preselected.
 */
export default async function SkillDetailPage({
  params,
}: {
  params: Promise<{ workspaceId: string; skillId: string }>
}) {
  const { workspaceId, skillId } = await params
  return <SkillsStudio workspaceId={workspaceId} initialSkillId={skillId} />
}
