import type { LucideIcon } from "lucide-react"
import {
  BlocksIcon,
  BoxIcon,
  LayersIcon,
  SparklesIcon,
  SquareFunctionIcon,
  Table2Icon,
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
    id: "core.transform",
    label: "Transform",
    namespace: "core.transform",
    icon: SquareFunctionIcon,
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
    id: "ai",
    label: "AI",
    namespace: "ai",
    icon: SparklesIcon,
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
const CORE_HTTP_ORDER = [
  "core.http_request",
  "core.http_poll",
  "core.http_paginate",
]
const TRANSFORM_TOP = ["core.transform.reshape"]
const AI_TOP = ["ai.action", "ai.agent", "ai.preset_agent", "ai.slackbot"]

function sortActions(
  actions: RegistryActionReadMinimal[],
  categoryId: string
): RegistryActionReadMinimal[] {
  const sorted = [...actions]

  if (categoryId === "core") {
    // Sort HTTP actions: request -> poll -> paginate, then alphabetical
    sorted.sort((a, b) => {
      const aHttpIndex = CORE_HTTP_ORDER.indexOf(a.action)
      const bHttpIndex = CORE_HTTP_ORDER.indexOf(b.action)
      const aIsHttp = aHttpIndex !== -1
      const bIsHttp = bHttpIndex !== -1

      if (aIsHttp && bIsHttp) return aHttpIndex - bHttpIndex
      if (aIsHttp) return -1
      if (bIsHttp) return 1
      return a.action.localeCompare(b.action)
    })
  } else if (categoryId === "core.transform") {
    // Move reshape to top, then alphabetical
    sorted.sort((a, b) => {
      const aTopIndex = TRANSFORM_TOP.indexOf(a.action)
      const bTopIndex = TRANSFORM_TOP.indexOf(b.action)
      const aIsTop = aTopIndex !== -1
      const bIsTop = bTopIndex !== -1

      if (aIsTop && bIsTop) return aTopIndex - bTopIndex
      if (aIsTop) return -1
      if (bIsTop) return 1
      return a.action.localeCompare(b.action)
    })
  } else if (categoryId === "ai") {
    // Sort AI actions with specific order at top
    sorted.sort((a, b) => {
      const aTopIndex = AI_TOP.indexOf(a.action)
      const bTopIndex = AI_TOP.indexOf(b.action)
      const aIsTop = aTopIndex !== -1
      const bIsTop = bTopIndex !== -1

      if (aIsTop && bIsTop) return aTopIndex - bTopIndex
      if (aIsTop) return -1
      if (bIsTop) return 1
      return a.action.localeCompare(b.action)
    })
  } else {
    sorted.sort((a, b) => a.action.localeCompare(b.action))
  }

  return sorted
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
        // For "core", match exactly "core" namespace (not core.*)
        if (category.namespace === "core") {
          return action.namespace === "core"
        }
        // For "tools", match anything starting with "tools."
        if (category.namespace === "tools") {
          return action.namespace?.startsWith("tools.") ?? false
        }
        // For others, match the exact namespace or starts with namespace.
        return (
          action.namespace === category.namespace ||
          action.namespace?.startsWith(`${category.namespace}.`)
        )
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

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <Tooltip>
        <TooltipTrigger asChild>
          <PopoverTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              className="size-9"
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
              {filteredActions.map((action) => (
                <CommandItem
                  key={action.action}
                  value={action.action}
                  onSelect={() => handleSelect(action)}
                  className="flex cursor-pointer items-center gap-3 py-2"
                >
                  {getIcon(action.action, {
                    className: "size-8 rounded-md border p-1.5",
                  })}
                  <div className="flex min-w-0 flex-col">
                    <span className="truncate text-xs font-medium">
                      {action.default_title ?? action.action}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {action.action}
                    </span>
                  </div>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
