"use client"

import { ArrowDownIcon, ArrowUpIcon } from "lucide-react"
import type { ComponentType } from "react"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"

export type ListSortDirection = "asc" | "desc"

export interface ListSortValue<TField extends string = string> {
  field: TField
  direction: ListSortDirection
}

export interface ListSortFieldOption<TField extends string = string> {
  value: TField
  label: string
  icon: ComponentType<{ className?: string }>
}

interface ListSortSelectProps<TField extends string> {
  value: ListSortValue<TField>
  onChange: (next: ListSortValue<TField>) => void
  options: Array<ListSortFieldOption<TField>>
}

/** A "Sort by" field select with an adjacent ascending/descending toggle. */
export function ListSortSelect<TField extends string>({
  value,
  onChange,
  options,
}: ListSortSelectProps<TField>) {
  const selectedLabel =
    options.find((option) => option.value === value.field)?.label ??
    options[0]?.label

  return (
    <div className="inline-flex items-center rounded-md border border-input bg-transparent">
      <Select
        value={value.field}
        onValueChange={(nextField) =>
          onChange({
            field: nextField as TField,
            direction: value.direction,
          })
        }
      >
        <SelectTrigger className="h-6 w-[145px] rounded-r-none border-0 bg-transparent px-2 text-xs font-medium shadow-none focus:ring-0">
          <span className="text-muted-foreground">Sort by</span>
          <span className="text-foreground">{selectedLabel}</span>
        </SelectTrigger>
        <SelectContent align="start">
          {options.map((option) => {
            const Icon = option.icon
            return (
              <SelectItem key={option.value} value={option.value}>
                <span className="flex items-center gap-2">
                  <Icon className="size-3.5 text-muted-foreground" />
                  <span>{option.label}</span>
                </span>
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>
      <button
        type="button"
        className="flex h-6 w-7 items-center justify-center border-l border-input text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
        onClick={() =>
          onChange({
            field: value.field,
            direction: value.direction === "asc" ? "desc" : "asc",
          })
        }
        aria-label={
          value.direction === "asc" ? "Sort ascending" : "Sort descending"
        }
      >
        {value.direction === "asc" ? (
          <ArrowUpIcon className="size-3.5" />
        ) : (
          <ArrowDownIcon className="size-3.5" />
        )}
      </button>
    </div>
  )
}
