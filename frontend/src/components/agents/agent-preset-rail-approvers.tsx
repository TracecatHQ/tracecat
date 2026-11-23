"use client"

import {
  type LucideIcon,
  ShieldIcon,
  UserIcon,
  UsersIcon,
  X,
} from "lucide-react"
import { useState } from "react"
import type {
  AgentPresetApproverKind,
  AgentPresetApproverRead,
  AgentPresetApproverRef,
} from "@/client"
import {
  LogoTile,
  RAIL_LIST_ROW,
  RAIL_REMOVE_BUTTON,
  RailEmpty,
  RailSectionHeader,
} from "@/components/agents/agent-preset-rail-primitives"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Button } from "@/components/ui/button"
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { DialogDescription } from "@/components/ui/dialog"
import {
  useAgentPresetApproverOptions,
  useAgentPresetApprovers,
  useReplaceAgentPresetApprovers,
} from "@/hooks/use-agent-preset-approvers"

const APPROVER_KINDS: Record<
  AgentPresetApproverKind,
  { heading: string; label: string; Icon: LucideIcon }
> = {
  user: { heading: "People", label: "Person", Icon: UserIcon },
  group: { heading: "Groups", label: "Group", Icon: UsersIcon },
  role: { heading: "Roles", label: "Role", Icon: ShieldIcon },
}

const DEFAULT_RULE =
  "No approvers listed: anyone who can reach an approval, here or in the agent's channel, can decide."

function approverKey(approver: AgentPresetApproverRef): string {
  return `${approver.kind}:${approver.id}`
}

function approverDetail(approver: AgentPresetApproverRead): string {
  if (approver.email && approver.email !== approver.name) return approver.email
  return APPROVER_KINDS[approver.kind].label
}

/**
 * Who may decide this preset's tool approvals. Saved on change and never
 * versioned, so it stays editable on synced presets.
 */
export function AgentPresetApproversPanel({
  workspaceId,
  presetId,
}: {
  workspaceId: string
  presetId: string | null
}) {
  const canEdit = useScopeCheck("agent:update") === true
  const approversQuery = useAgentPresetApprovers(workspaceId, presetId)
  const { mutate: replaceApprovers, isPending } =
    useReplaceAgentPresetApprovers(workspaceId, presetId)
  const [isPickerOpen, setIsPickerOpen] = useState(false)
  const approvers = approversQuery.data ?? []
  const listed = new Set(approvers.map(approverKey))
  // Every save replaces the whole set, so edit only a loaded one.
  const editable = canEdit && approversQuery.isSuccess && !isPending

  function save(next: AgentPresetApproverRead[]) {
    replaceApprovers(next.map(({ kind, id }) => ({ kind, id })))
  }

  let addDisabledReason: string | null = null
  if (!presetId) {
    addDisabledReason = "Save the agent first, then choose its approvers."
  } else if (!canEdit) {
    addDisabledReason = "Only agent editors can change approvers."
  } else if (!approversQuery.isSuccess) {
    addDisabledReason = "Approvers have not loaded yet."
  }

  let body = <RailEmpty>{DEFAULT_RULE}</RailEmpty>
  if (approversQuery.isError) {
    body = <RailEmpty>Approvers could not be loaded.</RailEmpty>
  } else if (presetId && approversQuery.isPending) {
    body = <RailEmpty>Loading approvers...</RailEmpty>
  } else if (approvers.length > 0) {
    body = (
      <>
        {approvers.map((approver) => {
          const { Icon } = APPROVER_KINDS[approver.kind]
          return (
            <div key={approverKey(approver)} className={RAIL_LIST_ROW}>
              <LogoTile>
                <Icon />
              </LogoTile>
              <span className="truncate">{approver.name}</span>
              <span className="truncate text-xs text-muted-foreground">
                {approverDetail(approver)}
              </span>
              <span className="flex-1" />
              {canEdit ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className={RAIL_REMOVE_BUTTON}
                  onClick={() =>
                    save(approvers.filter((other) => other !== approver))
                  }
                  disabled={!editable}
                  aria-label={`Remove ${approver.name}`}
                >
                  <X className="size-3" />
                </Button>
              ) : null}
            </div>
          )
        })}
        <p className="px-2 pt-2 text-[12px] text-muted-foreground/70">
          Approvers also need permission to run agents. Workspace admins can
          always approve.
        </p>
      </>
    )
  }

  return (
    <section>
      <RailSectionHeader
        title="Approvers"
        count={approvers.length}
        addLabel="Add approver"
        onAdd={() => setIsPickerOpen(true)}
        addDisabled={!editable}
        addDisabledReason={addDisabledReason}
      />
      {body}
      <CommandDialog open={isPickerOpen} onOpenChange={setIsPickerOpen}>
        {isPickerOpen ? (
          <ApproverPicker
            workspaceId={workspaceId}
            listed={listed}
            onPick={(option) => {
              save([...approvers, option])
              setIsPickerOpen(false)
            }}
          />
        ) : null}
      </CommandDialog>
    </section>
  )
}

/** Searchable people, groups, and roles; fetched only while open. */
function ApproverPicker({
  workspaceId,
  listed,
  onPick,
}: {
  workspaceId: string
  listed: Set<string>
  onPick: (option: AgentPresetApproverRead) => void
}) {
  const {
    data: options = [],
    isError,
    isPending,
  } = useAgentPresetApproverOptions(workspaceId)
  const kinds = Object.keys(APPROVER_KINDS) as AgentPresetApproverKind[]
  return (
    <>
      <DialogDescription className="sr-only">
        Choose a person, group, or role that can approve this agent&apos;s tool
        calls.
      </DialogDescription>
      <CommandInput placeholder="Search people, groups, and roles..." />
      <CommandList>
        <CommandEmpty>
          {isError
            ? "People, groups, and roles could not be loaded."
            : isPending
              ? "Loading..."
              : "No matches."}
        </CommandEmpty>
        {kinds.map((kind) => {
          const { heading, Icon } = APPROVER_KINDS[kind]
          const group = options.filter((option) => option.kind === kind)
          if (group.length === 0) return null
          return (
            <CommandGroup key={kind} heading={heading}>
              {group.map((option) => {
                const added = listed.has(approverKey(option))
                return (
                  <CommandItem
                    key={approverKey(option)}
                    value={approverKey(option)}
                    keywords={[option.name, option.email ?? ""]}
                    disabled={added}
                    onSelect={() => onPick(option)}
                    className="flex min-w-0 items-center gap-2"
                  >
                    <Icon className="!size-4 shrink-0 text-muted-foreground" />
                    <span className="min-w-0 truncate">{option.name}</span>
                    {option.email ? (
                      <span className="truncate text-xs text-muted-foreground">
                        {option.email}
                      </span>
                    ) : null}
                    {added ? (
                      <span className="ml-auto shrink-0 text-xs text-muted-foreground">
                        Added
                      </span>
                    ) : null}
                  </CommandItem>
                )
              })}
            </CommandGroup>
          )
        })}
      </CommandList>
    </>
  )
}
