"use client"

import { PlugZapIcon } from "lucide-react"
import { useId, useMemo } from "react"
import { getIcon } from "@/components/icons"
import { MultiTagCommandInput, type Suggestion } from "@/components/tags-input"
import { useListMcpIntegrations, useRegistryActions } from "@/lib/hooks"
import {
  buildSkillToolOptions,
  MAX_SKILL_TOOLS,
  readSkillFrontmatterTools,
  updateSkillFrontmatterTools,
} from "@/lib/skill-tools"

const TOOL_SEARCH_KEYS: (keyof Suggestion)[] = [
  "label",
  "value",
  "description",
  "group",
]

interface SkillToolsDropdownProps {
  workspaceId: string
  frontmatter: string
  onChange: (frontmatter: string) => void
}

/**
 * Searchable registry and MCP tool picker backed by `metadata.tools` in the
 * root SKILL.md frontmatter.
 */
export function SkillToolsDropdown({
  workspaceId,
  frontmatter,
  onChange,
}: SkillToolsDropdownProps) {
  const inputId = useId()
  const { registryActions, registryActionsIsLoading, registryActionsError } =
    useRegistryActions()
  const { mcpIntegrations, mcpIntegrationsIsLoading, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId)
  const toolsState = useMemo(
    () => readSkillFrontmatterTools(frontmatter),
    [frontmatter]
  )
  const suggestions = useMemo<Suggestion[]>(
    () =>
      buildSkillToolOptions(registryActions ?? [], mcpIntegrations ?? []).map(
        (option) => ({
          id: option.value,
          value: option.value,
          label: option.label,
          description: option.description,
          group: option.group,
          tagLabel: option.tagLabel,
          tagGroup: option.tagGroup,
          showHoverCard: true,
          icon:
            option.kind === "registry" ? (
              getIcon(option.value, { className: "size-4" })
            ) : (
              <PlugZapIcon className="size-4" />
            ),
        })
      ),
    [mcpIntegrations, registryActions]
  )
  const suggestionsLoading =
    registryActionsIsLoading || mcpIntegrationsIsLoading
  const suggestionsError = registryActionsError || mcpIntegrationsError

  return (
    <div className="flex flex-col gap-1.5">
      <div>
        <label htmlFor={inputId} className="text-xs font-medium">
          Tools
        </label>
        <div className="text-xs text-muted-foreground">
          Add registry and MCP tools to this skill&apos;s portable metadata.
        </div>
      </div>
      <MultiTagCommandInput
        value={toolsState.tools}
        inputId={inputId}
        onChange={(nextTools) => {
          if (!toolsState.valid) {
            return
          }
          onChange(updateSkillFrontmatterTools(frontmatter, nextTools))
        }}
        suggestions={suggestions}
        placeholder={
          suggestionsLoading ? "Loading tools..." : "Search tools..."
        }
        disabled={!toolsState.valid}
        maxTags={MAX_SKILL_TOOLS}
        searchKeys={TOOL_SEARCH_KEYS}
      />
      {!toolsState.valid ? (
        <p className="text-xs text-destructive">{toolsState.message}</p>
      ) : suggestionsError ? (
        <p className="text-xs text-muted-foreground">
          Some available tools could not be loaded. Existing IDs are preserved.
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Selected IDs are written directly to metadata.tools.
        </p>
      )}
    </div>
  )
}
