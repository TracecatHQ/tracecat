import type { LucideIcon } from "lucide-react"
import {
  BlocksIcon,
  BotIcon,
  BoxIcon,
  DatabaseIcon,
  LayersIcon,
  SparklesIcon,
  Table2Icon,
  WorkflowIcon,
} from "lucide-react"
import { useCallback, useMemo, useState } from "react"
import type { RegistryActionReadMinimal } from "@/client"
import { getIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useBuilderRegistryActions } from "@/lib/hooks"
import { cn } from "@/lib/utils"

interface ActionCategory {
  id: string
  label: string
  namespace: string
  icon: LucideIcon
  hasSearch?: boolean
  /** Dropdown alignment - "start" aligns left, "center" centers, "end" aligns right */
  align?: "start" | "center" | "end"
}

const ACTION_CATEGORIES: ActionCategory[] = [
  {
    id: "core",
    label: "Core",
    namespace: "core",
    icon: BoxIcon,
    align: "center",
  },
  {
    id: "ai",
    label: "AI",
    namespace: "ai",
    icon: SparklesIcon,
    align: "center",
  },
  {
    id: "agent",
    label: "Agent",
    namespace: "ai",
    icon: BotIcon,
    align: "center",
  },
  {
    id: "core.workflow",
    label: "Workflow",
    namespace: "core.workflow",
    icon: WorkflowIcon,
    align: "center",
  },
  {
    id: "core.cases",
    label: "Cases",
    namespace: "core.cases",
    icon: LayersIcon,
    align: "center",
  },
  {
    id: "core.table",
    label: "Tables",
    namespace: "core.table",
    icon: Table2Icon,
    align: "center",
  },
  {
    id: "core.sql",
    label: "SQL",
    namespace: "core.sql",
    icon: DatabaseIcon,
    align: "center",
  },
  {
    id: "tools",
    label: "Tools",
    namespace: "tools",
    icon: BlocksIcon,
    hasSearch: true,
    align: "center",
  },
]

// Custom sort orders for specific namespaces
const CORE_TOP = [
  "core.transform.reshape",
  "core.script.run_python",
  "core.http_request",
  "core.http_paginate",
  "core.http_poll",
  "core.send_email_smtp",
  "core.grpc.request",
]
const WORKFLOW_TOP = [
  "core.workflow.execute",
  "core.workflow.get_status",
  "core.transform.scatter",
  "core.transform.gather",
]
const SQL_TOP = ["core.duckdb.execute_sql", "core.sql.execute_query"]
const AI_TOP = ["ai.action", "ai.ranker", "ai.slackbot"]
const AGENT_TOP = ["ai.agent", "ai.preset_agent"]

const WORKFLOW_EXTRA_ACTIONS = new Set([
  "core.transform.scatter",
  "core.transform.gather",
])
const SQL_NAMESPACES = ["core.sql", "core.duckdb"]
const CATEGORY_STYLES: Record<string, { buttonClass: string }> = {
  core: {
    buttonClass:
      "text-muted-foreground hover:bg-zinc-100/70 hover:text-foreground data-[state=open]:bg-zinc-100/70",
  },
  ai: {
    buttonClass:
      "text-muted-foreground hover:bg-sky-50/70 hover:text-foreground data-[state=open]:bg-sky-50/70",
  },
  agent: {
    buttonClass:
      "text-muted-foreground hover:bg-emerald-50/70 hover:text-foreground data-[state=open]:bg-emerald-50/70",
  },
  "core.workflow": {
    buttonClass:
      "text-muted-foreground hover:bg-slate-100/75 hover:text-foreground data-[state=open]:bg-slate-100/75",
  },
  "core.cases": {
    buttonClass:
      "text-muted-foreground hover:bg-slate-100/75 hover:text-foreground data-[state=open]:bg-slate-100/75",
  },
  "core.table": {
    buttonClass:
      "text-muted-foreground hover:bg-slate-100/75 hover:text-foreground data-[state=open]:bg-slate-100/75",
  },
  "core.sql": {
    buttonClass:
      "text-muted-foreground hover:bg-slate-100/75 hover:text-foreground data-[state=open]:bg-slate-100/75",
  },
  tools: {
    buttonClass:
      "text-muted-foreground hover:bg-orange-50/70 hover:text-foreground data-[state=open]:bg-orange-50/70",
  },
}

function matchesNamespace(
  actionNamespace: string | undefined,
  namespace: string
): boolean {
  return (
    actionNamespace === namespace ||
    actionNamespace?.startsWith(`${namespace}.`) === true
  )
}

function isCoreAction(action: RegistryActionReadMinimal): boolean {
  if (action.action === "core.transform.reshape") return true
  if (action.action === "core.send_email_smtp") return true
  if (action.action.startsWith("core.http_")) return true
  if (matchesNamespace(action.namespace, "core.script")) return true
  if (matchesNamespace(action.namespace, "core.grpc")) return true
  return false
}

function isAgentAction(action: RegistryActionReadMinimal): boolean {
  return (
    action.action.startsWith("ai.") &&
    action.action.toLowerCase().includes("agent")
  )
}

function isAiAction(action: RegistryActionReadMinimal): boolean {
  return matchesNamespace(action.namespace, "ai") && !isAgentAction(action)
}

function sortWithTop(
  actions: RegistryActionReadMinimal[],
  topActions: string[]
): RegistryActionReadMinimal[] {
  return [...actions].sort((a, b) => {
    const aTopIndex = topActions.indexOf(a.action)
    const bTopIndex = topActions.indexOf(b.action)
    const aIsTop = aTopIndex !== -1
    const bIsTop = bTopIndex !== -1

    if (aIsTop && bIsTop) return aTopIndex - bTopIndex
    if (aIsTop) return -1
    if (bIsTop) return 1
    return a.action.localeCompare(b.action)
  })
}

function sortActions(
  actions: RegistryActionReadMinimal[],
  categoryId: string
): RegistryActionReadMinimal[] {
  if (categoryId === "core") {
    return sortWithTop(actions, CORE_TOP)
  } else if (categoryId === "core.workflow") {
    return sortWithTop(actions, WORKFLOW_TOP)
  } else if (categoryId === "core.sql") {
    return sortWithTop(actions, SQL_TOP)
  } else if (categoryId === "ai") {
    return sortWithTop(actions, AI_TOP)
  } else if (categoryId === "agent") {
    return sortWithTop(actions, AGENT_TOP)
  }

  return [...actions].sort((a, b) => a.action.localeCompare(b.action))
}

export interface CanvasToolbarProps {
  onAddAction: (action: RegistryActionReadMinimal) => void
}

export function CanvasToolbar({ onAddAction }: CanvasToolbarProps) {
  const { registryActions, registryActionsIsLoading } =
    useBuilderRegistryActions()

  const actionsByCategory = useMemo(() => {
    if (!registryActions) return new Map<string, RegistryActionReadMinimal[]>()

    const grouped = new Map<string, RegistryActionReadMinimal[]>()

    for (const category of ACTION_CATEGORIES) {
      const actions = registryActions.filter((action) => {
        if (category.id === "core") {
          return isCoreAction(action)
        }
        if (category.id === "ai") {
          return isAiAction(action)
        }
        if (category.id === "agent") {
          return isAgentAction(action)
        }
        if (category.id === "core.workflow") {
          return (
            matchesNamespace(action.namespace, "core.workflow") ||
            WORKFLOW_EXTRA_ACTIONS.has(action.action)
          )
        }
        if (category.id === "core.sql") {
          return SQL_NAMESPACES.some((namespace) =>
            matchesNamespace(action.namespace, namespace)
          )
        }
        // For "tools", match anything starting with "tools."
        if (category.namespace === "tools") {
          return action.namespace?.startsWith("tools.") ?? false
        }
        // For others, match the exact namespace or starts with namespace.
        return matchesNamespace(action.namespace, category.namespace)
      })
      grouped.set(category.id, sortActions(actions, category.id))
    }

    return grouped
  }, [registryActions])

  return (
    <TooltipProvider delayDuration={300}>
      <div className="flex items-center gap-1 rounded-lg border bg-background/95 p-1 shadow-lg backdrop-blur supports-[backdrop-filter]:bg-background/80">
        {ACTION_CATEGORIES.map((category) => {
          const Icon = category.icon
          const actions = actionsByCategory.get(category.id) ?? []

          return (
            <ToolbarCategoryDropdown
              key={category.id}
              category={category}
              actions={actions}
              isLoading={registryActionsIsLoading}
              onAddAction={onAddAction}
              Icon={Icon}
            />
          )
        })}
      </div>
    </TooltipProvider>
  )
}

interface ToolbarCategoryDropdownProps {
  category: ActionCategory
  actions: RegistryActionReadMinimal[]
  isLoading: boolean
  onAddAction: (action: RegistryActionReadMinimal) => void
  Icon: LucideIcon
}

function ToolbarCategoryDropdown({
  category,
  actions,
  isLoading,
  onAddAction,
  Icon,
}: ToolbarCategoryDropdownProps) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const isAiCategory = category.id === "ai"
  const isAgentCategory = category.id === "agent"
  const categoryStyle = CATEGORY_STYLES[category.id]

  const filteredActions = useMemo(() => {
    if (!search) return actions
    const searchLower = search.toLowerCase()
    return actions.filter(
      (action) =>
        action.action.toLowerCase().includes(searchLower) ||
        action.default_title?.toLowerCase().includes(searchLower)
    )
  }, [actions, search])

  const handleSelect = useCallback(
    (action: RegistryActionReadMinimal) => {
      onAddAction(action)
      setOpen(false)
      setSearch("")
    },
    [onAddAction]
  )

  function renderActionIcon(action: RegistryActionReadMinimal) {
    if (isAiCategory) {
      return (
        <div className="flex size-8 items-center justify-center rounded-md border border-sky-100 bg-sky-50/80">
          <SparklesIcon className="size-4 text-zinc-700" />
        </div>
      )
    }
    if (isAgentCategory) {
      return (
        <div className="flex size-8 items-center justify-center rounded-md border border-emerald-100 bg-emerald-50/80">
          <BotIcon className="size-4 text-zinc-700" />
        </div>
      )
    }
    return getIcon(action.action, {
      className: "size-8 rounded-md border p-1.5",
    })
  }

  function renderActionItem(action: RegistryActionReadMinimal) {
    return (
      <CommandItem
        key={action.action}
        value={action.action}
        onSelect={() => handleSelect(action)}
        className="flex cursor-pointer items-center gap-3 py-2"
      >
        {renderActionIcon(action)}
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-xs font-medium">
            {action.default_title ?? action.action}
          </span>
          <span className="truncate text-xs text-muted-foreground">
            {action.action}
          </span>
        </div>
      </CommandItem>
    )
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className={cn("size-9", categoryStyle?.buttonClass)}
              disabled={isLoading || actions.length === 0}
            >
              <Icon className="size-5" />
            </Button>
          </PopoverTrigger>
        </TooltipTrigger>
        <TooltipContent side="top" className="text-xs">
          {category.label}
        </TooltipContent>
      </Tooltip>
      <PopoverContent
        side="top"
        align={category.align ?? "start"}
        className="w-fit min-w-48 max-w-80 p-0"
        sideOffset={8}
      >
        <Command shouldFilter={false}>
          {category.hasSearch && (
            <CommandInput
              placeholder={`Search ${category.label.toLowerCase()}...`}
              value={search}
              onValueChange={setSearch}
              className="text-xs"
            />
          )}
          <CommandList>
            <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
              No actions found
            </CommandEmpty>
            <CommandGroup
              heading={`${category.label} actions`}
              className="[&_[cmdk-group-heading]]:text-xs [&_[cmdk-group-heading]]:font-medium"
            >
              {filteredActions.map((action) => renderActionItem(action))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
