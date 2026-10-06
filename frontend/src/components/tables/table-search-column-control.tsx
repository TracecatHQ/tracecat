"use client"

import { SearchXIcon, TextSearchIcon } from "lucide-react"
import type { MouseEvent } from "react"
import type { TableColumnRead } from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { useTableSearchContext } from "@/components/tables/table-search-context"
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
import { DropdownMenuItem } from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"

function isTextColumn(column: TableColumnRead) {
  return column.type.toUpperCase() === "TEXT"
}

/** Column menu item that opens the enable or disable vector search confirm. */
export function TableSearchColumnMenuItem({
  column,
  onSelectAction,
}: {
  column: TableColumnRead
  onSelectAction: (enabled: boolean) => void
}) {
  const search = useTableSearchContext()
  if (!search?.canRead || !isTextColumn(column)) return null
  const { configuration, provider, selection, retry, canUpdate } = search
  const selected =
    configuration.data?.selected_column_ids?.includes(column.id) ?? false
  const unavailable =
    !provider.data?.available || provider.data.state === "paused"
  const Icon = selected ? SearchXIcon : TextSearchIcon
  return (
    <DropdownMenuItem
      className="py-1 text-xs text-foreground/80"
      disabled={
        !canUpdate ||
        selection.isPending ||
        retry.isPending ||
        configuration.isFetching ||
        !configuration.data ||
        Boolean(configuration.error) ||
        ((unavailable || Boolean(provider.error)) && !selected)
      }
      onSelect={() => onSelectAction(!selected)}
    >
      <Icon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
      {selected ? "Disable vector search" : "Enable vector search"}
    </DropdownMenuItem>
  )
}

/** Confirm adding a TEXT column to, or removing it from, vector search. */
export function TableSearchColumnDialog({
  column,
  enabled,
  open,
  onOpenChange,
}: {
  column: TableColumnRead
  enabled: boolean
  open: boolean
  onOpenChange: () => void
}) {
  const search = useTableSearchContext()
  if (!search?.canRead || !isTextColumn(column)) return null
  const { provider, selection } = search
  const destination = provider.data?.configuration
  const title = enabled ? "Enable vector search" : "Disable vector search"
  const Icon = enabled ? TextSearchIcon : SearchXIcon

  const handleConfirm = async (event: MouseEvent) => {
    // Keep the dialog open until the write is confirmed.
    event.preventDefault()
    try {
      await selection.mutateAsync({ columnId: column.id, enabled })
      toast({
        title: enabled ? "Enabled vector search" : "Disabled vector search",
        description: enabled
          ? "Indexing has started for this column."
          : "Column is no longer in vector search.",
      })
      onOpenChange()
    } catch {
      // The mutation's own error handler shows the failure toast.
    }
  }

  return (
    <AlertDialog
      open={open}
      onOpenChange={() => {
        if (!selection.isPending) {
          onOpenChange()
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>
            {enabled
              ? `Text in column ${column.name} will be indexed for vector search.`
              : `Column ${column.name} is removed from vector search and the index is rebuilt for the remaining columns.`}
            {enabled &&
              destination &&
              ` Text is sent to ${destination.provider} / ${destination.model}.`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={selection.isPending}>
            Cancel
          </AlertDialogCancel>
          <AlertDialogAction
            onClick={handleConfirm}
            disabled={selection.isPending}
            variant={enabled ? undefined : "destructive"}
          >
            {selection.isPending ? (
              <>
                <Spinner />
                {enabled ? "Enabling..." : "Disabling..."}
              </>
            ) : (
              <>
                <Icon className="mr-2 size-4" />
                {title}
              </>
            )}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
