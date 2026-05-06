import { SkillsStudio } from "@/components/skills/skills-studio"

/**
 * Workspace skill detail page.
 *
 * @param props Route params.
 * @returns The skills studio editor for the requested skill.
 */
export default async function SkillDetailPage({
  params,
}: {
  params: Promise<{ workspaceId: string; skillId: string }>
}) {
  const { workspaceId } = await params
  return <SkillsStudio workspaceId={workspaceId} />
}
