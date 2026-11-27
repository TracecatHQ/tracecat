"use client"

import { Check, MousePointerClickIcon, WorkflowIcon } from "lucide-react"
import { useState } from "react"
import type { TriggerBindingCreate, TriggerRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import {
  RUN_ACTION_BY_TARGET,
  useCanRunTarget,
} from "@/components/triggers/trigger-target-scopes"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { ToggleTabs } from "@/components/ui/toggle-tabs"
import { toast } from "@/components/ui/use-toast"
import { useAgentPresets } from "@/hooks/use-agent-presets"
import { useTriggerMutations } from "@/hooks/use-triggers"
import { useWorkflowManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { workflowUuidToShortId } from "@/lib/workflow-ids"

const ADD_TARGET_FORM_ID = "add-trigger-target-form"
const INSTRUCTION_MAX_LENGTH = 20_000

type TargetType = TriggerBindingCreate["target"]["type"]

interface PickerOption {
  id: string
  label: string
  detail?: string | null
}

/**
 * Attach a workflow or an agent to a trigger. Every matching event then runs
 * it, so the API also needs the target's execute scope.
 */
export function AddTargetDialog({
  workspaceId,
  trigger,
  open,
  onOpenChange,
}: {
  workspaceId: string
  trigger: TriggerRead
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const canReadWorkflows = useScopeCheck("workflow:read")
  const canReadAgents = useScopeCheck("agent:read")
  const canRunTarget = useCanRunTarget()
  const { createBinding } = useTriggerMutations(workspaceId)
  // Every control locks while the request runs, so what is sent is what shows.
  const pending = createBinding.isPending
  const [targetType, setTargetType] = useState<TargetType>("workflow")
  const [targetId, setTargetId] = useState<string | null>(null)
  const [instruction, setInstruction] = useState("")
  const [formError, setFormError] = useState<string | null>(null)

  const { workflows, workflowsLoading } = useWorkflowManager(undefined, {
    listEnabled: open && canReadWorkflows === true,
  })
  const { presets, presetsIsLoading } = useAgentPresets(workspaceId, {
    enabled: open && canReadAgents === true,
  })

  // Bindings carry workflow UUIDs; the workflow list uses short IDs.
  const boundIds = new Set(
    trigger.bindings.map((binding) =>
      binding.target_type === "workflow"
        ? workflowUuidToShortId(binding.target_id)
        : binding.target_id
    )
  )

  let options: PickerOption[]
  let loading: boolean
  let canRead: boolean | undefined
  let canRun: boolean | undefined
  if (targetType === "workflow") {
    options = (workflows ?? []).map((workflow) => ({
      id: workflow.id,
      label: workflow.title,
      detail: workflow.alias,
    }))
    loading = workflowsLoading
    canRead = canReadWorkflows
    canRun = canRunTarget("workflow")
  } else {
    options = (presets ?? []).map((preset) => ({
      id: preset.id,
      label: preset.name,
      detail: preset.description,
    }))
    loading = presetsIsLoading
    canRead = canReadAgents
    canRun = canRunTarget("agent")
  }

  function reset() {
    setTargetType("workflow")
    setTargetId(null)
    setInstruction("")
    setFormError(null)
  }

  function buildTarget(id: string): TriggerBindingCreate["target"] {
    if (targetType === "workflow") {
      return { type: "workflow", workflow_id: id }
    }
    return {
      type: "agent",
      agent_preset_id: id,
      instruction: instruction.trim(),
    }
  }

  async function handleSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!targetId) {
      setFormError(
        targetType === "workflow" ? "Pick a workflow." : "Pick an agent."
      )
      return
    }
    if (targetType === "agent" && !instruction.trim()) {
      setFormError("Tell the agent what to do with each event.")
      return
    }
    setFormError(null)
    try {
      await createBinding.mutateAsync({
        triggerId: trigger.id,
        requestBody: { target: buildTarget(targetId) },
      })
      toast({ title: "Target added" })
      reset()
      onOpenChange(false)
    } catch (error) {
      setFormError(describeTriggerError(error))
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // A request in flight would close a reopened draft when it lands.
        if (!next && pending) return
        if (!next) reset()
        onOpenChange(next)
      }}
    >
      <DialogContent size="md">
        <DialogHeader>
          <DialogTitle>Add target</DialogTitle>
          <DialogDescription>
            Every event that matches {trigger.name} runs this target.
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          <form
            id={ADD_TARGET_FORM_ID}
            className="space-y-4"
            onSubmit={handleSubmit}
          >
            <ToggleTabs<TargetType>
              size="sm"
              disabled={pending}
              value={targetType}
              onValueChange={(next) => {
                setTargetType(next)
                setTargetId(null)
                setFormError(null)
              }}
              options={[
                {
                  value: "workflow",
                  content: (
                    <span className="inline-flex items-center gap-1.5">
                      <WorkflowIcon className="size-3.5" />
                      Workflow
                    </span>
                  ),
                },
                {
                  value: "agent",
                  content: (
                    <span className="inline-flex items-center gap-1.5">
                      <MousePointerClickIcon className="size-3.5" />
                      Agent
                    </span>
                  ),
                },
              ]}
            />
            {canRun === false ? (
              <p className="text-xs text-destructive">
                You don't have permission to {RUN_ACTION_BY_TARGET[targetType]},
                so you can't add one as a target. Ask a workspace admin for
                access.
              </p>
            ) : null}
            {canRead === false ? (
              <p className="text-xs text-muted-foreground">
                You can't list{" "}
                {targetType === "workflow" ? "workflows" : "agents"} in this
                workspace.
              </p>
            ) : (
              <TargetPicker
                label={targetType === "workflow" ? "Workflow" : "Agent"}
                options={options}
                loading={loading}
                boundIds={boundIds}
                disabled={pending}
                value={targetId}
                onSelect={(id) => {
                  setTargetId(id)
                  setFormError(null)
                }}
              />
            )}
            {targetType === "agent" ? (
              <div className="space-y-2">
                <Label htmlFor="trigger-target-instruction" className="text-xs">
                  Instruction
                </Label>
                <Textarea
                  id="trigger-target-instruction"
                  className="min-h-[96px] text-xs"
                  placeholder="Scan each new file for malware indicators and open a case if anything looks suspicious."
                  value={instruction}
                  maxLength={INSTRUCTION_MAX_LENGTH}
                  disabled={pending}
                  onChange={(event) => setInstruction(event.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Starts a new session for each event. The event is passed as
                  data, never as instructions.
                </p>
              </div>
            ) : null}
            {formError ? (
              <p className="whitespace-pre-line text-xs text-destructive">
                {formError}
              </p>
            ) : null}
          </form>
        </DialogBody>
        <DialogFooter>
          <Button
            size="sm"
            type="submit"
            form={ADD_TARGET_FORM_ID}
            disabled={pending || canRun === false}
          >
            Add target
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function TargetPicker({
  label,
  options,
  loading,
  boundIds,
  disabled,
  value,
  onSelect,
}: {
  label: string
  options: PickerOption[]
  loading: boolean
  boundIds: Set<string>
  disabled: boolean
  value: string | null
  onSelect: (id: string) => void
}) {
  return (
    <Command
      aria-label={`Pick ${label.toLowerCase()}`}
      className="rounded-md border"
    >
      <CommandInput
        placeholder={`Search ${label.toLowerCase()}s…`}
        className="text-xs"
      />
      <CommandList className="max-h-56">
        <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
          {loading ? "Loading…" : `No ${label.toLowerCase()}s found.`}
        </CommandEmpty>
        {options.map((option) => {
          const bound = boundIds.has(option.id)
          const selected = value === option.id
          return (
            <CommandItem
              key={option.id}
              // Label and detail are what the user searches; the ID keeps
              // identical names unique for cmdk.
              value={[option.label, option.detail, option.id]
                .filter(Boolean)
                .join(" ")}
              disabled={bound || disabled}
              onSelect={() => onSelect(option.id)}
              className="gap-2 text-xs"
            >
              <span className="min-w-0 flex-1 truncate" title={option.label}>
                {option.label}
              </span>
              {bound ? (
                <span className="shrink-0 text-muted-foreground">
                  Already a target
                </span>
              ) : null}
              <Check
                className={cn(
                  "size-3.5 shrink-0",
                  selected ? "opacity-100" : "opacity-0"
                )}
              />
              {selected ? <span className="sr-only">, selected</span> : null}
            </CommandItem>
          )
        })}
      </CommandList>
    </Command>
  )
}
