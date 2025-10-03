"use client"

import { Cross2Icon } from "@radix-ui/react-icons"
import { Check, ChevronsUpDown, Trash2Icon } from "lucide-react"
import { type ComponentType, useMemo, useState } from "react"
import type { CasePriority, CaseSeverity, CaseStatus } from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { Spinner } from "@/components/loading/spinner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { getDisplayName } from "@/lib/auth"
import { cn } from "@/lib/utils"

const getIconTextClass = (color?: string) =>
  color?.split(" ").find((token) => token.startsWith("text-")) ||
  "text-muted-foreground"

interface FilterOption<T extends string = string> {
  value: T
  label: string
  icon?: ComponentType<{ className?: string }>
  iconClassName?: string
}

interface FilterMultiSelectProps<T extends string> {
  placeholder: string
  value: T[]
  options: FilterOption<T>[]
  onChange: (value: T[]) => void
  className?: string
  emptyMessage?: string
}

function FilterMultiSelect<T extends string>({
  placeholder,
  value,
  options,
  onChange,
  className,
  emptyMessage = "No results found.",
}: FilterMultiSelectProps<T>) {
  const [open, setOpen] = useState(false)
  const valueSet = useMemo(() => new Set(value), [value])
  const optionMap = useMemo(() => {
    const map = new Map<T, FilterOption<T>>()
    for (const option of options) {
      map.set(option.value, option)
    }
    return map
  }, [options])

  const selectedCount = value.length
  const triggerLabel =
    selectedCount === 0
      ? placeholder
      : selectedCount === 1
        ? (optionMap.get(value[0])?.label ?? placeholder)
        : `${placeholder} (${selectedCount})`

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          role="combobox"
          className={cn("h-8 justify-between px-2 text-xs", className)}
        >
          <span className="truncate text-left">{triggerLabel}</span>
          <ChevronsUpDown className="ml-2 size-3 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[220px] p-0" align="start">
        <Command>
          <CommandInput
            placeholder={`Search ${placeholder.toLowerCase()}...`}
            className="text-xs"
          />
          <CommandList>
            <CommandEmpty>{emptyMessage}</CommandEmpty>
            <CommandGroup>
              {options.map((option) => {
                const isSelected = valueSet.has(option.value)
                return (
                  <CommandItem
                    key={option.value}
                    value={`${option.label} ${option.value}`}
                    onSelect={() => {
                      const nextValue = isSelected
                        ? value.filter((item) => item !== option.value)
                        : [...value, option.value]
                      onChange(nextValue)
                      setOpen(true)
                    }}
                    className="flex items-center gap-2 text-xs"
                  >
                    <div
                      className={cn(
                        "mr-2 flex size-4 items-center justify-center rounded-sm border",
                        isSelected
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-muted text-muted-foreground"
                      )}
                    >
                      <Check
                        className={cn("size-3", !isSelected && "opacity-0")}
                      />
                    </div>
                    {option.icon && (
                      <option.icon
                        className={cn(
                          "size-3 text-muted-foreground",
                          option.iconClassName
                        )}
                      />
                    )}
                    <span className="truncate">{option.label}</span>
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
        {selectedCount > 0 && (
          <div className="border-t p-1">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 w-full text-xs"
              onClick={() => onChange([])}
            >
              Clear selection
            </Button>
          </div>
        )}
      </PopoverContent>
    </Popover>
  )
}

interface CaseTableFiltersProps {
  workspaceId: string
  searchTerm: string
  onSearchChange: (value: string) => void
  statusFilter: CaseStatus[]
  onStatusChange: (value: CaseStatus[]) => void
  priorityFilter: CasePriority[]
  onPriorityChange: (value: CasePriority[]) => void
  severityFilter: CaseSeverity[]
  onSeverityChange: (value: CaseSeverity[]) => void
  assigneeFilter: string[]
  onAssigneeChange: (value: string[]) => void
  selectedCount: number
  onDeleteSelected?: () => Promise<void>
  isDeleting?: boolean
}

export function CaseTableFilters({
  workspaceId,
  searchTerm,
  onSearchChange,
  statusFilter,
  onStatusChange,
  priorityFilter,
  onPriorityChange,
  severityFilter,
  onSeverityChange,
  assigneeFilter,
  onAssigneeChange,
  selectedCount,
  onDeleteSelected,
  isDeleting,
}: CaseTableFiltersProps) {
  const { members } = useWorkspaceMembers(workspaceId)

  const statusOptions = useMemo<FilterOption<CaseStatus>[]>(() => {
    return Object.values(STATUSES).map((status) => ({
      value: status.value,
      label: status.label,
      icon: status.icon,
      iconClassName: getIconTextClass(status.color),
    }))
  }, [])

  const priorityOptions = useMemo<FilterOption<CasePriority>[]>(() => {
    return Object.values(PRIORITIES).map((priority) => ({
      value: priority.value,
      label: priority.label,
      icon: priority.icon,
      iconClassName: getIconTextClass(priority.color),
    }))
  }, [])

  const severityOptions = useMemo<FilterOption<CaseSeverity>[]>(() => {
    return Object.values(SEVERITIES).map((severity) => ({
      value: severity.value,
      label: severity.label,
      icon: severity.icon,
      iconClassName: getIconTextClass(severity.color),
    }))
  }, [])

  const assigneeOptions = useMemo<FilterOption<string>[]>(() => {
    const workspaceMembers = members?.map((member) => ({
      value: member.user_id,
      label: getDisplayName({
        first_name: member.first_name,
        last_name: member.last_name,
        email: member.email,
      }),
    }))

    return [
      { value: UNASSIGNED, label: "Not assigned" },
      ...(workspaceMembers || []),
    ]
  }, [members])

  const hasFilters =
    statusFilter.length > 0 ||
    priorityFilter.length > 0 ||
    severityFilter.length > 0 ||
    assigneeFilter.length > 0 ||
    Boolean(searchTerm)

  const handleReset = () => {
    onSearchChange("")
    onStatusChange([])
    onPriorityChange([])
    onSeverityChange([])
    onAssigneeChange([])
  }

  const showDeleteButton = onDeleteSelected && selectedCount > 0

  return (
    <div className="flex flex-wrap items-center gap-2">
      <Input
        placeholder="Filter cases..."
        value={searchTerm}
        onChange={(e) => onSearchChange(e.target.value)}
        className="h-8 w-[250px] text-xs"
      />

      <FilterMultiSelect
        placeholder="Status"
        value={statusFilter}
        onChange={onStatusChange}
        options={statusOptions}
        className="w-[140px]"
      />

      <FilterMultiSelect
        placeholder="Priority"
        value={priorityFilter}
        onChange={onPriorityChange}
        options={priorityOptions}
        className="w-[140px]"
      />

      <FilterMultiSelect
        placeholder="Severity"
        value={severityFilter}
        onChange={onSeverityChange}
        options={severityOptions}
        className="w-[140px]"
      />

      <FilterMultiSelect
        placeholder="Assignee"
        value={assigneeFilter}
        onChange={onAssigneeChange}
        options={assigneeOptions}
        className="w-[160px]"
        emptyMessage={
          members && members.length > 0
            ? "No matching assignees."
            : "No assignees found."
        }
      />

      {hasFilters && (
        <Button
          variant="ghost"
          onClick={handleReset}
          className="h-8 px-2 text-xs text-foreground/80 lg:px-3"
        >
          Reset
          <Cross2Icon className="ml-2 size-4" />
        </Button>
      )}
      {showDeleteButton && (
        <AlertDialog>
          <AlertDialogTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-8 px-2 text-foreground/70 lg:px-3"
            >
              <span className="flex items-center">
                <Trash2Icon className="size-4" />
                <span className="ml-2">Delete</span>
              </span>
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Confirm deletion</AlertDialogTitle>
              <AlertDialogDescription>
                Are you sure you want to delete {selectedCount} selected case
                {selectedCount > 1 ? "s" : ""}? This action cannot be undone.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={isDeleting}
                onClick={async () => {
                  await onDeleteSelected()
                }}
              >
                {isDeleting ? (
                  <span className="flex items-center">
                    <Spinner className="size-4" />
                    <span className="ml-2">Deleting...</span>
                  </span>
                ) : (
                  "Delete"
                )}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </div>
  )
}
