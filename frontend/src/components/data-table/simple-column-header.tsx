import {
  ArrowDownIcon,
  ArrowUpIcon,
  CaretSortIcon,
} from "@radix-ui/react-icons"
import type { Column } from "@tanstack/react-table"
import { cn } from "@/lib/utils"

interface SimpleColumnHeaderProps<TData, TValue>
  extends React.HTMLAttributes<HTMLDivElement> {
  column: Column<TData, TValue>
  title: string
  icon?: React.ReactNode
}

export function SimpleColumnHeader<TData, TValue>({
  column,
  title,
  className,
  icon,
}: SimpleColumnHeaderProps<TData, TValue>) {
  if (!column.getCanSort()) {
    return (
      <div className={cn("flex items-center space-x-2", className)}>
        {icon && <span className="mr-2">{icon}</span>}
        <span>{title}</span>
      </div>
    )
  }

  return (
    <button
      type="button"
      className={cn(
        "flex items-center space-x-2 text-left font-medium transition-colors hover:text-foreground",
        column.getIsSorted() ? "text-foreground" : "text-muted-foreground",
        className
      )}
      onClick={column.getToggleSortingHandler()}
      aria-label={
        column.getIsSorted() === "desc"
          ? `Sorted descending. Click to sort ascending.`
          : column.getIsSorted() === "asc"
            ? `Sorted ascending. Click to remove sorting.`
            : `Click to sort ascending.`
      }
    >
      {icon && <span className="mr-2">{icon}</span>}
      <span>{title}</span>
      {column.getIsSorted() === "desc" ? (
        <ArrowDownIcon className="ml-2 size-4" />
      ) : column.getIsSorted() === "asc" ? (
        <ArrowUpIcon className="ml-2 size-4" />
      ) : (
        <CaretSortIcon className="ml-2 size-4 opacity-50" />
      )}
    </button>
  )
}
