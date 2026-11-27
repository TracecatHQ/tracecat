"use client"

import { MousePointerClickIcon, WorkflowIcon, ZapIcon } from "lucide-react"
import Link from "next/link"
import type {
  LockedTriggerCount,
  TriggerBindingRead,
  TriggerRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import {
  FormTriggerRows,
  useFormTriggers,
} from "@/components/triggers/form-trigger-rows"
import { describeTriggerError } from "@/components/triggers/trigger-errors"
import {
  getTriggerKind,
  TRIGGER_KINDS,
} from "@/components/triggers/trigger-kinds"
import { TriggerStatusLabel } from "@/components/triggers/trigger-status"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { useTriggers } from "@/hooks/use-triggers"
import { useWorkspaceId } from "@/providers/workspace-id"

const MAX_TARGETS_SHOWN = 3

/** Every trigger in the workspace, with its kind, source, targets and status. */
export function WorkspaceTriggers() {
  const workspaceId = useWorkspaceId()
  const canCreate = useScopeCheck("trigger:create")
  const {
    triggers,
    locked,
    isLoading,
    error,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useTriggers(workspaceId)
  const forms = useFormTriggers(workspaceId)

  let body: React.ReactNode
  if (error && triggers.length === 0) {
    body = (
      <p className="p-6 text-xs text-destructive">
        Couldn't load triggers. {describeTriggerError(error)}
      </p>
    )
  } else if (isLoading || (triggers.length === 0 && forms.isLoading)) {
    body = <CenteredSpinner />
  } else if (triggers.length === 0 && forms.forms.length === 0) {
    body = (
      <div className="flex h-full p-6">
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <ZapIcon className="size-5 text-muted-foreground/60" />
            </EmptyMedia>
            <EmptyTitle>No triggers yet</EmptyTitle>
            <EmptyDescription>
              A trigger starts workflows and agents: on a schedule, on a request
              to a webhook, or when an outside event arrives, such as a new file
              in object storage.
              {canCreate === true ? " Create one with Create trigger." : ""}
            </EmptyDescription>
          </EmptyHeader>
          <LockedTriggersNote locked={locked} />
        </Empty>
      </div>
    )
  } else {
    body = (
      <div className="divide-y">
        {locked.length > 0 ? (
          <div className="px-4 py-2">
            <LockedTriggersNote locked={locked} />
          </div>
        ) : null}
        <div className="flex items-center gap-2 px-4 py-2 text-xs text-muted-foreground">
          <span className="w-4 shrink-0" />
          <span className="w-[220px] shrink-0">Name</span>
          <span className="w-[120px] shrink-0">Kind</span>
          <span className="w-[220px] shrink-0">Source</span>
          <span className="min-w-0 flex-1">Targets</span>
          <span className="w-16 shrink-0">Status</span>
        </div>
        {triggers.map((trigger) => (
          <TriggerRow
            key={trigger.id}
            workspaceId={workspaceId}
            trigger={trigger}
          />
        ))}
        {error ? (
          <p className="px-4 py-2 text-xs text-destructive">
            Couldn't load all triggers. {describeTriggerError(error)}
          </p>
        ) : null}
        {hasNextPage ? (
          <div className="px-4 py-2">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs"
              disabled={isFetchingNextPage}
              onClick={() => fetchNextPage()}
            >
              {isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          </div>
        ) : null}
        <FormTriggerRows
          workspaceId={workspaceId}
          forms={forms.forms}
          error={forms.error}
        />
      </div>
    )
  }

  return (
    <div className="flex size-full flex-col overflow-hidden">
      <div className="min-h-0 flex-1 overflow-auto">{body}</div>
    </div>
  )
}

/**
 * "2 object storage triggers are hidden because your plan doesn't include
 * that kind."
 * The plan keeps such triggers; they come back when it includes the kind.
 */
function LockedTriggersNote({ locked }: { locked: LockedTriggerCount[] }) {
  if (locked.length === 0) {
    return null
  }
  const parts = locked.map(({ kind, count }) => {
    const label = Object.prototype.hasOwnProperty.call(TRIGGER_KINDS, kind)
      ? TRIGGER_KINDS[kind].label.toLowerCase()
      : kind
    return `${count} ${label} ${count === 1 ? "trigger" : "triggers"}`
  })
  const total = locked.reduce((sum, { count }) => sum + count, 0)
  return (
    <p className="text-xs text-muted-foreground">
      {parts.join(" and ")} {total === 1 ? "is" : "are"} hidden because your
      plan doesn&apos;t include {total === 1 ? "that kind" : "those kinds"}.
      Nothing was deleted.
    </p>
  )
}

function TriggerRow({
  workspaceId,
  trigger,
}: {
  workspaceId: string
  trigger: TriggerRead
}) {
  const definition = getTriggerKind(trigger)
  const Icon = definition?.icon ?? ZapIcon
  const source = definition?.describeSource(trigger) ?? ""
  return (
    <Link
      href={`/workspaces/${workspaceId}/triggers/${trigger.id}`}
      className="flex items-center gap-2 px-4 py-2 text-xs transition-colors hover:bg-muted/50"
    >
      <Icon className="size-4 shrink-0 text-muted-foreground" />
      <span className="w-[220px] shrink-0 truncate">{trigger.name}</span>
      <span className="w-[120px] shrink-0 truncate text-muted-foreground">
        {definition?.label ?? trigger.kind}
      </span>
      <span
        className="w-[220px] shrink-0 truncate font-mono text-muted-foreground"
        title={source}
      >
        {source}
      </span>
      <TargetSummary bindings={trigger.bindings} />
      <span className="w-16 shrink-0">
        <TriggerStatusLabel status={trigger.status} />
      </span>
    </Link>
  )
}

function TargetSummary({ bindings }: { bindings: TriggerBindingRead[] }) {
  if (bindings.length === 0) {
    return (
      <span className="min-w-0 flex-1 text-muted-foreground">No targets</span>
    )
  }
  const shown = bindings.slice(0, MAX_TARGETS_SHOWN)
  const hidden = bindings.length - shown.length
  return (
    <span className="flex min-w-0 flex-1 items-center gap-1.5 overflow-hidden">
      {shown.map((binding) => (
        <TargetChip key={binding.id} binding={binding} />
      ))}
      {hidden > 0 ? (
        <span className="shrink-0 text-muted-foreground">+{hidden}</span>
      ) : null}
    </span>
  )
}

/** A workflow or agent target: its icon and name, dimmed while offline. */
export function TargetChip({ binding }: { binding: TriggerBindingRead }) {
  const Icon =
    binding.target_type === "agent" ? MousePointerClickIcon : WorkflowIcon
  const offline = binding.status !== "online"
  return (
    <span
      className={
        offline
          ? "inline-flex min-w-0 items-center gap-1 rounded border px-1.5 py-0.5 text-muted-foreground"
          : "inline-flex min-w-0 items-center gap-1 rounded border px-1.5 py-0.5"
      }
      title={offline ? `${binding.target_name} (offline)` : binding.target_name}
    >
      <Icon className="size-3 shrink-0" />
      <span className="truncate">{binding.target_name}</span>
    </span>
  )
}
