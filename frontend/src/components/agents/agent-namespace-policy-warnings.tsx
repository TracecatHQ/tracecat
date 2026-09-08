"use client"

import { AlertCircle } from "lucide-react"
import type { SkillReadMinimal } from "@/client"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { useSkills, useSkillVersion } from "@/hooks/use-skills"

interface NamespacePolicyWarningsProps {
  workspaceId: string
  namespaces: string[]
  skills: Array<{ skillId: string }>
  actions?: string[]
}

/** Show the registry tools excluded by the preset's current namespace policy. */
export function AgentNamespacePolicyWarnings({
  workspaceId,
  namespaces,
  skills,
  actions = [],
}: NamespacePolicyWarningsProps) {
  if (namespaces.length === 0) {
    return null
  }
  const blockedActions = blockedTools(actions, namespaces)
  return (
    <div className="flex flex-col gap-3" aria-live="polite">
      {blockedActions.length > 0 ? (
        <BlockedToolsWarning
          namespaces={namespaces}
          source="the preset's action list"
          tools={blockedActions}
        />
      ) : null}
      {skills.length > 0 ? (
        <AttachedSkillPolicyWarnings
          workspaceId={workspaceId}
          namespaces={namespaces}
          skills={skills}
        />
      ) : null}
    </div>
  )
}

function AttachedSkillPolicyWarnings({
  workspaceId,
  namespaces,
  skills: bindings,
}: NamespacePolicyWarningsProps) {
  const { skills, skillsLoading, skillsError } = useSkills(workspaceId)
  if (skillsError) {
    return <p>Unable to check skill tools against the namespace policy.</p>
  }
  if (skillsLoading) {
    return <p>Checking skill tools against the namespace policy…</p>
  }
  return bindings.map((binding) => {
    const skill = skills?.find((item) => item.id === binding.skillId)
    if (!skill?.current_version_id) {
      return (
        <p key={binding.skillId}>
          Unable to check an attached skill against the namespace policy: its
          published version is unavailable.
        </p>
      )
    }
    return (
      <SkillNamespacePolicyWarning
        key={skill.id}
        workspaceId={workspaceId}
        skill={skill}
        namespaces={namespaces}
      />
    )
  })
}

function SkillNamespacePolicyWarning({
  workspaceId,
  skill,
  namespaces,
}: {
  workspaceId: string
  skill: SkillReadMinimal
  namespaces: string[]
}) {
  const { version, versionLoading, versionError } = useSkillVersion(
    workspaceId,
    skill.id,
    skill.current_version_id
  )
  if (versionError) {
    return <p>Unable to check tools from skill “{skill.name}”.</p>
  }
  if (versionLoading || !version) {
    return <p>Checking tools from skill “{skill.name}”…</p>
  }
  const blocked = blockedTools(version.registry_tool_ids ?? [], namespaces)
  if (blocked.length === 0) {
    return null
  }
  return (
    <BlockedToolsWarning
      namespaces={namespaces}
      source={`skill “${version.name}”`}
      tools={blocked}
    />
  )
}

function blockedTools(tools: string[], namespaces: string[]) {
  // Match the prefix policy enforced by build_agent_tools.
  return tools.filter(
    (tool) => !namespaces.some((namespace) => tool.startsWith(namespace))
  )
}

function BlockedToolsWarning({
  namespaces,
  source,
  tools,
}: {
  namespaces: string[]
  source: string
  tools: string[]
}) {
  return (
    <Alert variant="warning">
      <AlertCircle />
      <AlertTitle>Tools blocked by namespace policy</AlertTitle>
      <AlertDescription>
        <p>
          The namespace policy ({namespaces.join(", ")}) blocks these tools from{" "}
          {source}:
        </p>
        <ul className="list-disc pl-5">
          {tools.map((tool) => (
            <li key={tool} className="break-all">
              <code>{tool}</code>
            </li>
          ))}
        </ul>
        <p>The agent can run, but these tools will be unavailable.</p>
      </AlertDescription>
    </Alert>
  )
}
