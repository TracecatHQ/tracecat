"use client"

import { useMutation, useQuery } from "@tanstack/react-query"
import { CheckIcon, LinkIcon } from "lucide-react"
import { useMemo, useState } from "react"
import type { CaseReadMinimal } from "@/client"
import { Spinner } from "@/components/loading/spinner"
import { useTableSelection } from "@/components/tables/table-selection-context"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { toast } from "@/components/ui/use-toast"
import { useDebounce } from "@/hooks"
import { client as apiClient } from "@/lib/api"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

type SearchCasesResponse = {
  items: CaseReadMinimal[]
}

export function TableLinkRowsToCaseCommand() {
  const workspaceId = useWorkspaceId()
  const { selectedCount, selectedRowIds, tableId, gridApi } =
    useTableSelection()
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState("")
  const [selectedCaseIds, setSelectedCaseIds] = useState<Set<string>>(new Set())
  const [debouncedSearch] = useDebounce(search, 300)

  const normalizedShortId = debouncedSearch.trim().toUpperCase()

  const {
    data: cases = [],
    isLoading: casesIsLoading,
    isFetching: casesIsFetching,
  } = useQuery({
    queryKey: ["cases", "search", "short-id", workspaceId, normalizedShortId],
    queryFn: async () => {
      const response = await apiClient.get<SearchCasesResponse>(
        "/cases/search",
        {
          params: {
            workspace_id: workspaceId,
            short_id: normalizedShortId,
            limit: 20,
          },
        }
      )
      return response.data.items
    },
    enabled: open && normalizedShortId.length > 0,
  })

  const { mutateAsync: linkRowsToCases, isPending: isLinking } = useMutation({
    mutationFn: async () => {
      const caseIds = Array.from(selectedCaseIds)
      if (caseIds.length === 0 || selectedRowIds.length === 0) {
        return
      }

      await Promise.all(
        caseIds.flatMap((caseId) =>
          selectedRowIds.map((rowId) =>
            apiClient.post(
              `/cases/${caseId}/rows`,
              {
                table_id: tableId,
                row_id: rowId,
              },
              {
                params: {
                  workspace_id: workspaceId,
                },
              }
            )
          )
        )
      )
    },
    onSuccess: () => {
      const caseCount = selectedCaseIds.size
      toast({
        title: "Rows linked",
        description: `Linked ${selectedCount} row${selectedCount === 1 ? "" : "s"} to ${caseCount} case${caseCount === 1 ? "" : "s"}.`,
      })
      gridApi?.deselectAll()
      setOpen(false)
      setSearch("")
      setSelectedCaseIds(new Set())
    },
    onError: (error) => {
      toast({
        title: "Could not link rows",
        description: error instanceof Error ? error.message : "Try again.",
        variant: "destructive",
      })
    },
  })

  const commandHint = useMemo(() => {
    if (!normalizedShortId) {
      return "Search case ID contains (e.g. 42 or CASE-0042)"
    }
    return "No case found with that ID fragment"
  }, [normalizedShortId])

  if (selectedCount === 0) {
    return null
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        className="h-7 px-2 text-xs text-muted-foreground"
        onClick={() => setOpen(true)}
      >
        <LinkIcon className="mr-1 size-3" />
        Link to case
      </Button>

      <CommandDialog
        open={open}
        onOpenChange={(nextOpen) => {
          setOpen(nextOpen)
          if (!nextOpen) {
            setSearch("")
            setSelectedCaseIds(new Set())
          }
        }}
      >
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search by case ID fragment..."
            value={search}
            onValueChange={setSearch}
          />
          <CommandList>
            {!casesIsLoading && !casesIsFetching && (
              <CommandEmpty>{commandHint}</CommandEmpty>
            )}
            {cases.map((caseItem) => {
              const isSelected = selectedCaseIds.has(caseItem.id)
              return (
                <CommandItem
                  key={caseItem.id}
                  value={caseItem.short_id}
                  onSelect={() => {
                    setSelectedCaseIds((prev) => {
                      const next = new Set(prev)
                      if (next.has(caseItem.id)) {
                        next.delete(caseItem.id)
                      } else {
                        next.add(caseItem.id)
                      }
                      return next
                    })
                  }}
                >
                  <CheckIcon
                    className={cn(
                      "size-4",
                      isSelected ? "opacity-100" : "opacity-0"
                    )}
                  />
                  <div className="flex min-w-0 flex-col">
                    <span className="text-sm font-medium">
                      {caseItem.short_id}
                    </span>
                    <span className="truncate text-xs text-muted-foreground">
                      {caseItem.summary}
                    </span>
                  </div>
                </CommandItem>
              )
            })}
          </CommandList>
        </Command>
        <div className="flex items-center justify-between border-t px-4 py-3">
          <span className="text-xs text-muted-foreground">
            {selectedCaseIds.size} case{selectedCaseIds.size === 1 ? "" : "s"}{" "}
            selected
          </span>
          <Button
            size="sm"
            onClick={() => linkRowsToCases()}
            disabled={selectedCaseIds.size === 0 || isLinking}
          >
            {isLinking ? (
              <span className="flex items-center gap-2">
                <Spinner className="size-3" />
                Linking...
              </span>
            ) : (
              "Link selected rows"
            )}
          </Button>
        </div>
      </CommandDialog>
    </>
  )
}
