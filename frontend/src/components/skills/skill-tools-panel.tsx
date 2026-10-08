"use client"

import { useCallback, useMemo, useRef, useState } from "react"
import { LockedFeatureModal } from "@/components/locked-feature-modal"
import {
  ToolSelectionList,
  ToolSelectionRow,
} from "@/components/tools/tool-selection-list"
import type { ToolSelectionValue } from "@/lib/agent-preset-tools"
import { useListMcpIntegrations, useRegistryActions } from "@/lib/hooks"
import {
  buildSkillToolIndex,
  MAX_SKILL_TOOLS,
  readSkillFrontmatterTools,
  updateSkillFrontmatterTools,
} from "@/lib/skill-tools"

const EMPTY_KEYS: string[] = []

interface SkillToolsPanelProps {
  workspaceId: string
  frontmatter: string
  onChange: (frontmatter: string) => void
}

/**
 * Tools list and picker backed by `metadata.tools` in the root SKILL.md
 * frontmatter, shown beside the frontmatter and instructions editors.
 */
export function SkillToolsPanel({
  workspaceId,
  frontmatter,
  onChange,
}: SkillToolsPanelProps) {
  const { registryActions, registryActionsIsLoading, registryActionsError } =
    useRegistryActions()
  const { mcpIntegrations, mcpIntegrationsIsLoading, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId)
  const loadError = Boolean(registryActionsError || mcpIntegrationsError)
  // Keyed on the frontmatter string, so typing in the body does not re-parse.
  const toolsState = useMemo(
    () =>
      readSkillFrontmatterTools(
        frontmatter,
        mcpIntegrationsIsLoading || mcpIntegrationsError
          ? undefined
          : mcpIntegrations,
        registryActionsIsLoading || registryActionsError
          ? undefined
          : registryActions
      ),
    [
      frontmatter,
      mcpIntegrations,
      mcpIntegrationsIsLoading,
      mcpIntegrationsError,
      registryActions,
      registryActionsIsLoading,
      registryActionsError,
    ]
  )
  const index = useMemo(
    () => buildSkillToolIndex(registryActions ?? [], mcpIntegrations ?? []),
    [registryActions, mcpIntegrations]
  )
  const value = useMemo<ToolSelectionValue>(
    () => ({ actions: toolsState.tools, mcpIntegrations: EMPTY_KEYS }),
    [toolsState]
  )
  const [lockedFeatureOpen, setLockedFeatureOpen] = useState(false)
  const openLockedFeature = useCallback(() => setLockedFeatureOpen(true), [])
  // The parent passes a new callback on every body keystroke; read it through
  // a ref so the memoised list is not re-rendered for those.
  const onChangeRef = useRef(onChange)
  onChangeRef.current = onChange
  // Malformed frontmatter cannot be rewritten without losing the user's YAML.
  const editable = toolsState.valid || Boolean(toolsState.canRemove)
  const write = useCallback(
    (next: ToolSelectionValue) => {
      if (!editable) return
      const current = toolsState.tools
      const tools = next.actions
      if (
        tools.length === current.length &&
        tools.every((tool, i) => tool === current[i])
      ) {
        return
      }
      onChangeRef.current(updateSkillFrontmatterTools(frontmatter, tools))
    },
    [editable, frontmatter, toolsState]
  )

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {/* The list has its own "Tools N" header; without it, show no count. */}
      {!editable && <h3 className="text-xs font-medium">Tools</h3>}
      {!toolsState.valid && (
        <p className="text-xs text-destructive">{toolsState.message}</p>
      )}
      {editable && (
        <ToolSelectionList
          value={value}
          onChange={write}
          index={index}
          maxTools={MAX_SKILL_TOOLS}
          registryLoading={registryActionsIsLoading}
          mcpLoading={mcpIntegrationsIsLoading}
          toolsLoadError={loadError}
          addDisabled={!toolsState.valid}
          approvalsEnabled={false}
          onLockedSelect={openLockedFeature}
        />
      )}
      {editable && loadError && toolsState.tools.length > 0 && (
        // Without a catalogue the list hides its rows. Keep declared IDs
        // visible and removable, without calling them unavailable.
        <div>
          <h4 className="py-3 text-xs text-muted-foreground">
            Existing tool IDs are preserved.
          </h4>
          {toolsState.tools.map((tool) => (
            <ToolSelectionRow
              key={tool}
              tool={tool}
              disabled={false}
              onRemove={() =>
                write({
                  ...value,
                  actions: toolsState.tools.filter((key) => key !== tool),
                })
              }
            />
          ))}
        </div>
      )}
      <LockedFeatureModal
        open={lockedFeatureOpen}
        onOpenChange={setLockedFeatureOpen}
      />
    </div>
  )
}
