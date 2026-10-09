"use client"

import { useCallback, useMemo, useState } from "react"
import { useFormContext, useWatch } from "react-hook-form"
import { YamlStyledEditor } from "@/components/editor/codemirror/yaml-editor"
import { ExpressionInput } from "@/components/editor/expression-input"
import { LockedFeatureModal } from "@/components/locked-feature-modal"
import { ToolSelectionList } from "@/components/tools/tool-selection-list"
import type { ToolSelectionValue } from "@/lib/agent-preset-tools"
import { getAgentToolsFolding, isStringArray } from "@/lib/agent-tools-folding"
import { useBuilderRegistryActions, useListMcpIntegrations } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const EMPTY_KEYS: string[] = []
const NO_INPUTS: Record<string, unknown> = {}

function approvalArray(value: Record<string, boolean> | null | undefined) {
  return Object.entries(value ?? {}).map(([tool, allow]) => ({ tool, allow }))
}

function approvalMap(value: ToolSelectionValue["toolApprovals"]) {
  if (!value?.length) return undefined
  return Object.fromEntries(value.map(({ tool, allow }) => [tool, allow]))
}

/** Unified workflow action field for registry tools, MCP servers, and approvals. */
export function AgentToolsField({
  fieldName,
  properties,
}: {
  fieldName: string
  properties: Record<string, unknown>
}) {
  const actionName = fieldName.replace(/^inputs\./, "")
  // Which siblings exist depends only on the schema; watch just those args so
  // edits to unrelated fields do not touch the list.
  const owned = useMemo(() => {
    const { mcpField, approvalsField } = getAgentToolsFolding(
      properties,
      NO_INPUTS
    )
    return [actionName, mcpField, approvalsField].filter(
      (name) => name !== null
    )
  }, [actionName, properties])
  return (
    <OwnedToolsField
      // Remount when the watched args change so no stale values are read.
      key={owned.join(",")}
      fieldName={fieldName}
      actionName={actionName}
      owned={owned}
      properties={properties}
    />
  )
}

function OwnedToolsField({
  fieldName,
  actionName,
  owned,
  properties,
}: {
  fieldName: string
  actionName: string
  owned: string[]
  properties: Record<string, unknown>
}) {
  // The context object is new on every parent render; its members are stable.
  const { control, setValue, watch } = useFormContext()
  const workspaceId = useWorkspaceId()
  const names = useMemo(() => owned.map((name) => `inputs.${name}`), [owned])
  const watched = useWatch({ control, name: names })
  const inputs = useMemo(
    () => Object.fromEntries(owned.map((name, i) => [name, watched[i]])),
    [owned, watched]
  )
  const actionsValue: unknown = inputs[actionName]
  const { mcpField, approvalsField } = useMemo(
    () => getAgentToolsFolding(properties, inputs),
    [properties, inputs]
  )
  const { registryActions, registryActionsIsLoading, registryActionsError } =
    useBuilderRegistryActions({ includeLocked: true })
  const mcpEnabled = mcpField !== null
  const { mcpIntegrations, mcpIntegrationsIsLoading, mcpIntegrationsError } =
    useListMcpIntegrations(workspaceId ?? "", undefined, {
      enabled: mcpEnabled,
    })
  const [lockedFeatureOpen, setLockedFeatureOpen] = useState(false)
  const openLockedFeature = useCallback(() => setLockedFeatureOpen(true), [])

  // Folded args are string arrays, approval maps, null or unset here; anything
  // else turned folding off above.
  const mcpValue = mcpField ? inputs[mcpField] : undefined
  const approvalsValue = approvalsField ? inputs[approvalsField] : undefined
  const value = useMemo<ToolSelectionValue>(
    () => ({
      actions: isStringArray(actionsValue) ? actionsValue : EMPTY_KEYS,
      mcpIntegrations: isStringArray(mcpValue) ? mcpValue : EMPTY_KEYS,
      toolApprovals: approvalArray(
        approvalsValue as Record<string, boolean> | null | undefined
      ),
    }),
    [actionsValue, mcpValue, approvalsValue]
  )

  // Write only the args whose reference changed, so mounting and a no-op Done
  // leave the form clean.
  const write = useCallback(
    (next: ToolSelectionValue) => {
      if (next.actions !== value.actions) {
        setValue(fieldName, next.actions, { shouldDirty: true })
      }
      if (mcpField && next.mcpIntegrations !== value.mcpIntegrations) {
        setValue(
          `inputs.${mcpField}`,
          next.mcpIntegrations.length ? next.mcpIntegrations : undefined,
          { shouldDirty: true }
        )
      }
      if (approvalsField && next.toolApprovals !== value.toolApprovals) {
        setValue(`inputs.${approvalsField}`, approvalMap(next.toolApprovals), {
          shouldDirty: true,
        })
      }
    },
    [approvalsField, fieldName, mcpField, setValue, value]
  )

  // Any string stays in the expression editor, so editing an expression into
  // an invalid one does not swap editors mid-keystroke.
  if (typeof actionsValue === "string") {
    return (
      <ExpressionInput
        value={actionsValue}
        onChange={(next) => setValue(fieldName, next, { shouldDirty: true })}
      />
    )
  }
  if (actionsValue != null && !isStringArray(actionsValue)) {
    return (
      <YamlStyledEditor
        name={fieldName}
        control={control}
        forEachExpressions={watch("for_each")}
      />
    )
  }

  return (
    <>
      <LockedFeatureModal
        open={lockedFeatureOpen}
        onOpenChange={setLockedFeatureOpen}
      />
      <ToolSelectionList
        value={value}
        onChange={write}
        registryActions={registryActions}
        mcpIntegrations={mcpIntegrations}
        registryLoading={registryActionsIsLoading}
        mcpLoading={mcpEnabled && mcpIntegrationsIsLoading}
        toolsLoadError={Boolean(
          registryActionsError || (mcpEnabled && mcpIntegrationsError)
        )}
        approvalsEnabled={approvalsField !== null}
        mcpEnabled={mcpEnabled}
        hideTitle
        onLockedSelect={openLockedFeature}
      />
    </>
  )
}
