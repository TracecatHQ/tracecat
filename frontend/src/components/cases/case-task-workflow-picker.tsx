"use client"

import { Check, CircleSlash, Workflow } from "lucide-react"
import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useState,
} from "react"
import type { WorkflowReadMinimal } from "@/client"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

/** Props for {@link CaseTaskWorkflowCommand}. */
export interface CaseTaskWorkflowCommandProps {
  workflows: readonly WorkflowReadMinimal[]
  /** The task's workflow; `null` selects "No workflow". */
  value: string | null
  onSelect: (workflowId: string | null) => void
  /**
   * Handles Escape inside the palette. Leave unset to let Escape reach the
   * surrounding menu, which closes itself.
   */
  onEscape?: () => void
}

/**
 * The task workflow palette: a search input over "No workflow" and every
 * workspace workflow, with the alias beside the title and a trailing check on
 * the current one. Hosted by {@link CaseTaskWorkflowPicker} in the composer
 * and inline in the task row's "Set workflow" submenu.
 */
export function CaseTaskWorkflowCommand({
  workflows,
  value,
  onSelect,
  onEscape,
}: CaseTaskWorkflowCommandProps) {
  function handleKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      if (onEscape) {
        event.preventDefault()
        event.stopPropagation()
        onEscape()
      }
      return
    }
    // Keep typing in the palette: a parent menu's typeahead would otherwise
    // move focus off the search input, and a parent composer would see Enter.
    event.stopPropagation()
  }

  return (
    <Command aria-label="Set workflow" onKeyDown={handleKeyDown}>
      <CommandInput placeholder="Search workflows…" className="text-xs" />
      <CommandList className="max-h-[min(300px,calc(var(--radix-popper-available-height,100vh)-3rem))]">
        <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
          No workflows found.
        </CommandEmpty>
        <CommandGroup>
          <WorkflowCommandItem
            value="No workflow"
            selected={value === null}
            onSelect={() => onSelect(null)}
            icon={
              <CircleSlash className="size-3.5 shrink-0 text-muted-foreground/70" />
            }
            label="No workflow"
          />
        </CommandGroup>
        {workflows.length > 0 && (
          <>
            <CommandSeparator />
            <CommandGroup heading="Workflows">
              {workflows.map((workflow) => (
                <WorkflowCommandItem
                  key={workflow.id}
                  // Title and alias are what the user searches; the ID keeps
                  // identical titles unique for cmdk.
                  value={[workflow.title, workflow.alias, workflow.id]
                    .filter(Boolean)
                    .join(" ")}
                  selected={value === workflow.id}
                  onSelect={() => onSelect(workflow.id)}
                  icon={
                    <Workflow className="size-3.5 shrink-0 text-muted-foreground" />
                  }
                  label={workflow.title}
                  alias={workflow.alias}
                />
              ))}
            </CommandGroup>
          </>
        )}
      </CommandList>
    </Command>
  )
}

function WorkflowCommandItem({
  value,
  selected,
  onSelect,
  icon,
  label,
  alias,
}: {
  value: string
  selected: boolean
  onSelect: () => void
  icon: ReactNode
  label: string
  alias?: string | null
}) {
  return (
    <CommandItem value={value} onSelect={onSelect} className="gap-2 text-xs">
      {icon}
      <span className="min-w-0 flex-1 truncate" title={label}>
        {label}
      </span>
      {alias && (
        <span className="max-w-24 shrink-0 truncate font-mono text-[10px] text-muted-foreground">
          {alias}
        </span>
      )}
      <Check
        className={cn(
          "size-3.5 shrink-0",
          selected ? "opacity-100" : "opacity-0"
        )}
      />
      {/* cmdk owns `aria-selected` for highlight, so current state rides on
          the accessible name instead. */}
      {selected && <span className="sr-only">{", current"}</span>}
    </CommandItem>
  )
}

/** Props for {@link CaseTaskWorkflowPicker}. */
export interface CaseTaskWorkflowPickerProps
  extends Omit<CaseTaskWorkflowCommandProps, "onEscape"> {
  /** The trigger element. */
  children: ReactNode
}

/** {@link CaseTaskWorkflowCommand} in a popover opened by `children`. */
export function CaseTaskWorkflowPicker({
  workflows,
  value,
  onSelect,
  children,
}: CaseTaskWorkflowPickerProps) {
  const [open, setOpen] = useState(false)

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>{children}</PopoverTrigger>
      <PopoverContent
        align="start"
        className="w-72 p-0"
        onClick={(e) => e.stopPropagation()}
      >
        <CaseTaskWorkflowCommand
          workflows={workflows}
          value={value}
          onSelect={(workflowId) => {
            setOpen(false)
            onSelect(workflowId)
          }}
          onEscape={() => setOpen(false)}
        />
      </PopoverContent>
    </Popover>
  )
}
