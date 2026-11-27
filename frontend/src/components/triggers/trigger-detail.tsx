"use client"

import {
  ChevronLeftIcon,
  MousePointerClickIcon,
  PlusIcon,
  Trash2Icon,
  WorkflowIcon,
  ZapIcon,
} from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useState } from "react"
import type { TriggerBindingRead, TriggerRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AddTargetDialog } from "@/components/triggers/add-target-dialog"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import { getTriggerKind } from "@/components/triggers/trigger-kinds"
import { TriggerRunsSections } from "@/components/triggers/trigger-runs"
import { TriggerStatusLabel } from "@/components/triggers/trigger-status"
import {
  RUN_ACTION_BY_TARGET,
  useCanRunTarget,
  useHasScopes,
} from "@/components/triggers/trigger-target-scopes"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { useTrigger, useTriggerMutations } from "@/hooks/use-triggers"
import { getErrorStatus } from "@/lib/errors"
import { workflowUuidToShortId } from "@/lib/workflow-ids"
import { useWorkspaceId } from "@/providers/workspace-id"

/** One trigger: its status, targets and kind settings. */
export function TriggerDetail({ triggerId }: { triggerId: string }) {
  const workspaceId = useWorkspaceId()
  const { data: trigger, isLoading, error } = useTrigger(workspaceId, triggerId)

  if (isLoading) {
    return <CenteredSpinner />
  }

  let body: React.ReactNode
  if (error || !trigger) {
    body = (
      <p className="text-xs text-destructive">
        {getErrorStatus(error) === 404
          ? "This trigger doesn't exist or was deleted."
          : `Couldn't load the trigger. ${describeTriggerError(error)}`}
      </p>
    )
  } else {
    body = <TriggerDetailBody workspaceId={workspaceId} trigger={trigger} />
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-8">
        <div className="space-y-4">
          <Link
            href={`/workspaces/${workspaceId}/triggers`}
            className="inline-flex items-center text-xs text-muted-foreground hover:text-foreground"
          >
            <ChevronLeftIcon className="mr-1 size-3.5" />
            Triggers
          </Link>
          {body}
        </div>
      </div>
    </div>
  )
}

function TriggerDetailBody({
  workspaceId,
  trigger,
}: {
  workspaceId: string
  trigger: TriggerRead
}) {
  const router = useRouter()
  const canUpdateTriggers = useScopeCheck("trigger:update")
  const canDeleteTriggers = useScopeCheck("trigger:delete")
  const hasScopes = useHasScopes()
  const { updateTrigger, deleteTrigger } = useTriggerMutations(workspaceId)
  const [addTargetOpen, setAddTargetOpen] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const definition = getTriggerKind(trigger)
  // The kind may need its own scopes too, such as schedule:update.
  const canUpdate =
    canUpdateTriggers === true &&
    hasScopes(definition?.scopes.update ?? []) === true
  const canDelete =
    canDeleteTriggers === true &&
    hasScopes(definition?.scopes.delete ?? []) === true
  const Icon = definition?.icon ?? ZapIcon
  const canRunTarget = useCanRunTarget()
  // Putting the trigger online makes every online target live, so the API
  // checks the execute scope of each.
  const blockedTarget =
    trigger.status === "offline"
      ? trigger.bindings.find(
          (binding) =>
            binding.status === "online" &&
            canRunTarget(binding.target_type) !== true
        )
      : undefined
  const onlineBlockedReason = blockedTarget
    ? `You need permission to ${RUN_ACTION_BY_TARGET[blockedTarget.target_type]} to put this trigger online.`
    : undefined

  async function setOnline(online: boolean) {
    try {
      await updateTrigger.mutateAsync({
        triggerId: trigger.id,
        requestBody: { status: online ? "online" : "offline" },
      })
    } catch (error) {
      toast({
        title: online
          ? "Couldn't put the trigger online"
          : "Couldn't take the trigger offline",
        description: describeTriggerError(error),
        variant: "destructive",
      })
    }
  }

  async function handleDelete() {
    try {
      await deleteTrigger.mutateAsync(trigger.id)
      toast({ title: "Trigger deleted" })
      router.push(`/workspaces/${workspaceId}/triggers`)
    } catch (error) {
      toast({
        title: "Couldn't delete the trigger",
        description: describeTriggerError(error),
        variant: "destructive",
      })
    } finally {
      setConfirmDelete(false)
    }
  }

  return (
    <div className="space-y-12">
      <div className="flex w-full items-start justify-between gap-4">
        <div className="items-start space-y-3 text-left">
          <h2 className="flex items-center gap-2 text-2xl font-semibold tracking-tight">
            <Icon className="size-5 text-muted-foreground" />
            {trigger.name}
          </h2>
          <p className="text-base text-muted-foreground">
            {definition?.label ?? trigger.kind} trigger
          </p>
        </div>
        <div className="flex items-center gap-3">
          <TriggerStatusLabel status={trigger.status} />
          <span title={onlineBlockedReason}>
            <Switch
              aria-label={`${trigger.name} online`}
              checked={trigger.status === "online"}
              disabled={
                canUpdate !== true ||
                updateTrigger.isPending ||
                onlineBlockedReason !== undefined
              }
              onCheckedChange={setOnline}
            />
          </span>
          {canDelete === true ? (
            <Button
              variant="ghost"
              size="icon"
              className="size-7 text-destructive"
              aria-label={`Delete ${trigger.name}`}
              onClick={() => setConfirmDelete(true)}
            >
              <Trash2Icon className="size-4" />
            </Button>
          ) : null}
        </div>
      </div>

      <section className="space-y-3">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold">Targets</h3>
            <p className="text-xs text-muted-foreground">
              Workflows and agents this trigger starts. Each runs only while it
              and the trigger are online.
            </p>
          </div>
          {canUpdate === true && !definition?.fixedTargets ? (
            <Button
              variant="outline"
              size="sm"
              className="h-7"
              onClick={() => setAddTargetOpen(true)}
            >
              <PlusIcon className="mr-1 size-3.5" />
              Add target
            </Button>
          ) : null}
        </div>
        {trigger.bindings.length === 0 ? (
          <p className="rounded-md border border-dashed px-4 py-6 text-center text-xs text-muted-foreground">
            No targets yet. Add a workflow or an agent to run on each event.
          </p>
        ) : (
          <div className="divide-y rounded-md border">
            {trigger.bindings.map((binding) => (
              <TargetRow
                key={binding.id}
                workspaceId={workspaceId}
                trigger={trigger}
                binding={binding}
                fixed={Boolean(definition?.fixedTargets)}
              />
            ))}
          </div>
        )}
        {definition?.fixedTargets ? (
          <p className="text-xs text-muted-foreground">
            {definition.fixedTargets}
          </p>
        ) : null}
      </section>

      <section className="space-y-3">
        <h3 className="text-sm font-semibold">Settings</h3>
        {definition ? (
          <definition.Details workspaceId={workspaceId} trigger={trigger} />
        ) : (
          <p className="text-xs text-muted-foreground">
            This version of Tracecat can't show settings for {trigger.kind}{" "}
            triggers.
          </p>
        )}
      </section>

      <TriggerRunsSections workspaceId={workspaceId} trigger={trigger} />

      {addTargetOpen ? (
        <AddTargetDialog
          workspaceId={workspaceId}
          trigger={trigger}
          open={addTargetOpen}
          onOpenChange={setAddTargetOpen}
        />
      ) : null}

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {trigger.name}?</AlertDialogTitle>
            <AlertDialogDescription>
              {definition?.deleteDescription ?? "Its targets are removed too."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteTrigger.isPending}
              onClick={handleDelete}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

function targetHref(workspaceId: string, binding: TriggerBindingRead): string {
  if (binding.target_type === "agent") {
    return `/workspaces/${workspaceId}/agents/${binding.target_id}`
  }
  return `/workspaces/${workspaceId}/workflows/${workflowUuidToShortId(binding.target_id)}`
}

function TargetRow({
  workspaceId,
  trigger,
  binding,
  fixed,
}: {
  workspaceId: string
  trigger: TriggerRead
  binding: TriggerBindingRead
  /** The kind's targets can't change: no switch or remove. */
  fixed: boolean
}) {
  const canUpdate = useScopeCheck("trigger:update")
  const canDelete = useScopeCheck("trigger:delete")
  const { updateBinding, deleteBinding } = useTriggerMutations(workspaceId)
  const [confirmRemove, setConfirmRemove] = useState(false)
  const canRunTarget = useCanRunTarget()
  const isAgent = binding.target_type === "agent"
  const onlineBlockedReason =
    binding.status === "offline" && canRunTarget(binding.target_type) !== true
      ? `You need permission to ${RUN_ACTION_BY_TARGET[binding.target_type]} to put this target online.`
      : undefined
  const Icon = isAgent ? MousePointerClickIcon : WorkflowIcon

  async function setOnline(online: boolean) {
    try {
      await updateBinding.mutateAsync({
        triggerId: trigger.id,
        bindingId: binding.id,
        requestBody: { status: online ? "online" : "offline" },
      })
    } catch (error) {
      toast({
        title: online
          ? "Couldn't put the target online"
          : "Couldn't take the target offline",
        description: describeTriggerError(error),
        variant: "destructive",
      })
    }
  }

  async function handleRemove() {
    try {
      await deleteBinding.mutateAsync({
        triggerId: trigger.id,
        bindingId: binding.id,
      })
      toast({ title: "Target removed" })
    } catch (error) {
      toast({
        title: "Couldn't remove the target",
        description: describeTriggerError(error),
        variant: "destructive",
      })
    } finally {
      setConfirmRemove(false)
    }
  }

  return (
    <div className="flex items-center gap-3 px-4 py-2.5">
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1">
        <Link
          href={targetHref(workspaceId, binding)}
          className="block truncate text-xs font-medium hover:underline"
        >
          {binding.target_name}
        </Link>
        {isAgent && binding.instruction ? (
          <p
            className="truncate text-xs text-muted-foreground"
            title={binding.instruction}
          >
            {binding.instruction}
          </p>
        ) : null}
      </div>
      <span className="w-14 shrink-0 text-xs text-muted-foreground">
        {isAgent ? "Agent" : "Workflow"}
      </span>
      <span className="w-16 shrink-0">
        <TriggerStatusLabel status={binding.status} />
      </span>
      {fixed ? null : (
        <TargetControls
          binding={binding}
          canUpdate={canUpdate}
          canDelete={canDelete}
          pending={updateBinding.isPending}
          onlineBlockedReason={onlineBlockedReason}
          onToggle={setOnline}
          onRemove={() => setConfirmRemove(true)}
        />
      )}
      <AlertDialog open={confirmRemove} onOpenChange={setConfirmRemove}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove {binding.target_name}?</AlertDialogTitle>
            <AlertDialogDescription>
              {trigger.name} stops starting this{" "}
              {isAgent ? "agent" : "workflow"}. You can add it again later.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteBinding.isPending}
              onClick={handleRemove}
            >
              Remove
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

/** A target's online switch and remove button. */
function TargetControls({
  binding,
  canUpdate,
  canDelete,
  pending,
  onlineBlockedReason,
  onToggle,
  onRemove,
}: {
  binding: TriggerBindingRead
  canUpdate: boolean | undefined
  canDelete: boolean | undefined
  pending: boolean
  onlineBlockedReason: string | undefined
  onToggle: (online: boolean) => void
  onRemove: () => void
}) {
  return (
    <>
      <span title={onlineBlockedReason}>
        <Switch
          aria-label={`${binding.target_name} online`}
          checked={binding.status === "online"}
          disabled={
            canUpdate !== true || pending || onlineBlockedReason !== undefined
          }
          onCheckedChange={onToggle}
        />
      </span>
      {canDelete === true ? (
        <Button
          variant="ghost"
          size="icon"
          className="size-7"
          aria-label={`Remove ${binding.target_name}`}
          onClick={onRemove}
        >
          <Trash2Icon className="size-4" />
        </Button>
      ) : null}
    </>
  )
}
