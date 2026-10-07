"use client"

import {
  ChevronDown,
  ChevronRight,
  CornerDownRight,
  ListTree,
  Plus,
  SearchIcon,
  X,
} from "lucide-react"
import Link from "next/link"
import { useMemo, useState } from "react"
import {
  type CaseParentRead,
  type CaseRead,
  type CaseReadMinimal,
  casesBatchClearParent,
  casesBatchSetParent,
  casesSearchCases,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CaseBadge } from "@/components/cases/case-badge"
import { PRIORITIES, STATUSES } from "@/components/cases/case-categories"
import {
  CASE_PANEL_ACTION_ROW_CLASS,
  CASE_PANEL_BOX_CLASS,
} from "@/components/cases/case-task-fields"
import { chunkCaseIds } from "@/components/cases/cases-layout"
import { Spinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { useToast } from "@/components/ui/use-toast"
import { useDebounce } from "@/hooks/use-debounce"
import { type QueryClient, useInfiniteQuery, useQueryClient } from "@/lib/query"
import { cn } from "@/lib/utils"

const SUB_CASES_PAGE_SIZE = 25
const PICKER_PAGE_SIZE = 20

export interface CaseParentBatchResult {
  succeeded: number
  failed: number
  errors: string[]
}

/**
 * Sets or clears the parent of many cases, chunked to the server batch cap.
 * Pass `parentId: null` to return the cases to the top level.
 */
export async function changeCasesParent({
  workspaceId,
  caseIds,
  parentId,
}: {
  workspaceId: string
  caseIds: string[]
  parentId: string | null
}): Promise<CaseParentBatchResult> {
  const result: CaseParentBatchResult = { succeeded: 0, failed: 0, errors: [] }
  for (const chunk of chunkCaseIds(caseIds)) {
    const response = parentId
      ? await casesBatchSetParent({
          workspaceId,
          requestBody: { case_ids: chunk, parent_id: parentId },
        })
      : await casesBatchClearParent({
          workspaceId,
          requestBody: { case_ids: chunk },
        })
    result.succeeded += response.succeeded
    result.failed += response.failed
    for (const item of response.results) {
      if (!item.success && item.error) {
        result.errors.push(item.error)
      }
    }
  }
  return result
}

/** Refetches every view that shows parent/sub-case relationships. */
export async function invalidateCaseHierarchy(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: ["cases"] }),
    queryClient.invalidateQueries({ queryKey: ["case"] }),
    queryClient.invalidateQueries({ queryKey: ["case-events"] }),
  ])
}

function caseHref(workspaceId: string, caseId: string) {
  return `/workspaces/${workspaceId}/cases/${caseId}`
}

/** "Sub-case of CASE-0001" link shown above a sub-case's title. */
export function CaseParentBreadcrumb({
  parent,
  workspaceId,
}: {
  parent: CaseParentRead
  workspaceId: string
}) {
  return (
    <Link
      href={caseHref(workspaceId, parent.id)}
      className="mb-1 flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
    >
      <CornerDownRight className="size-3 shrink-0" />
      <span className="shrink-0">Sub-case of</span>
      <span className="shrink-0 font-medium">{parent.short_id}</span>
      <span className="truncate">{parent.summary}</span>
    </Link>
  )
}

function CaseSummaryRow({
  caseItem,
  workspaceId,
  trailing,
}: {
  caseItem: CaseReadMinimal
  workspaceId: string
  trailing?: React.ReactNode
}) {
  const status = STATUSES[caseItem.status]
  const priority = PRIORITIES[caseItem.priority]
  return (
    <div className="group flex h-9 items-center gap-2 rounded-md px-2 hover:bg-muted/50">
      <Link
        href={caseHref(workspaceId, caseItem.id)}
        className="flex min-w-0 flex-1 items-center gap-2"
      >
        <span className="shrink-0 text-xs font-medium text-muted-foreground">
          {caseItem.short_id}
        </span>
        <span className="truncate text-sm">{caseItem.summary}</span>
      </Link>
      {status && (
        <CaseBadge
          value={caseItem.status}
          label={status.label}
          icon={status.icon}
          color={status.color}
          className="h-5 shrink-0 px-1.5 py-0 text-[10px]"
        />
      )}
      {priority && (
        <CaseBadge
          value={caseItem.priority}
          label={priority.label}
          icon={priority.icon}
          color={priority.color}
          className="h-5 shrink-0 px-1.5 py-0 text-[10px]"
        />
      )}
      {trailing}
    </div>
  )
}

/**
 * The case page's Sub-cases panel. A top-level case lists its sub-cases in a
 * searchable, cursor-paginated, scrollable section; a sub-case links back to
 * its parent instead, since sub-cases are one level deep.
 */
export function CaseSubCasesPanel({
  caseData,
  workspaceId,
}: {
  caseData: CaseRead
  workspaceId: string
}) {
  const canUpdate = useScopeCheck("case:update") === true
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [isOpen, setIsOpen] = useState(true)
  const [search, setSearch] = useState("")
  const [debouncedSearch] = useDebounce(search.trim(), 300)
  const [addDialogOpen, setAddDialogOpen] = useState(false)
  const [removingId, setRemovingId] = useState<string | null>(null)

  const {
    data,
    isLoading,
    error,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useInfiniteQuery({
    queryKey: ["cases", "sub-cases", workspaceId, caseData.id, debouncedSearch],
    queryFn: ({ pageParam }) =>
      casesSearchCases({
        workspaceId,
        parentId: caseData.id,
        searchTerm: debouncedSearch || undefined,
        limit: SUB_CASES_PAGE_SIZE,
        cursor: (pageParam as string | null) ?? undefined,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage) =>
      lastPage.has_more && lastPage.next_cursor
        ? lastPage.next_cursor
        : undefined,
    enabled: !caseData.parent_id,
  })
  const subCases = useMemo(
    () => data?.pages.flatMap((page) => page.items) ?? [],
    [data]
  )

  const handleRemove = async (subCase: CaseReadMinimal) => {
    setRemovingId(subCase.id)
    try {
      const result = await changeCasesParent({
        workspaceId,
        caseIds: [subCase.id],
        parentId: null,
      })
      if (result.failed > 0) {
        toast({
          variant: "destructive",
          title: "Failed to remove sub-case",
          description: result.errors[0],
        })
      } else {
        toast({ title: `Removed ${subCase.short_id} from this case` })
      }
    } catch (err) {
      console.error("Failed to remove sub-case:", err)
      toast({ variant: "destructive", title: "Failed to remove sub-case" })
    } finally {
      setRemovingId(null)
      await invalidateCaseHierarchy(queryClient)
    }
  }

  if (caseData.parent) {
    return (
      <div className={CASE_PANEL_BOX_CLASS}>
        <div className="flex flex-col gap-1 px-2 py-1 text-sm">
          <CaseParentBreadcrumb
            parent={caseData.parent}
            workspaceId={workspaceId}
          />
          <p className="text-xs text-muted-foreground">
            Sub-cases are one level deep, so this case can't have its own
            sub-cases.
          </p>
        </div>
      </div>
    )
  }

  const total = caseData.num_sub_cases ?? 0

  return (
    <>
      <Collapsible open={isOpen} onOpenChange={setIsOpen}>
        {/* The action sits beside the trigger, not inside it: a button
            nested in the trigger button is invalid HTML. */}
        <div className="flex items-center justify-between gap-2">
          <CollapsibleTrigger asChild>
            <Button
              variant="ghost"
              className="h-auto flex-1 justify-start gap-1.5 p-0 text-sm font-medium hover:bg-transparent"
            >
              {isOpen ? (
                <ChevronDown className="size-3.5" />
              ) : (
                <ChevronRight className="size-3.5" />
              )}
              Sub-cases{total > 0 ? ` (${total})` : ""}
            </Button>
          </CollapsibleTrigger>
          {canUpdate && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => setAddDialogOpen(true)}
            >
              <Plus className="mr-1 size-3.5" />
              Add sub-cases
            </Button>
          )}
        </div>
        <CollapsibleContent className="mt-4">
          <div className={cn(CASE_PANEL_BOX_CLASS, "flex flex-col gap-1")}>
            <div className="flex items-center gap-2 px-2">
              <SearchIcon className="size-3.5 shrink-0 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search sub-cases..."
                aria-label="Search sub-cases"
                className="h-8 border-none bg-transparent p-0 text-sm shadow-none focus-visible:ring-0"
              />
            </div>
            <div className="max-h-96 overflow-y-auto">
              {isLoading &&
                [...Array(3)].map((_, index) => (
                  <Skeleton key={index} className="mx-2 my-1 h-7 rounded-md" />
                ))}
              {error && (
                <p className="px-2 py-2 text-sm text-muted-foreground">
                  Failed to load sub-cases
                </p>
              )}
              {!isLoading && !error && subCases.length === 0 && (
                <p className="px-2 py-2 text-sm text-muted-foreground">
                  {debouncedSearch
                    ? "No sub-cases match your search"
                    : "No sub-cases yet. Group related cases here from the cases list or with Add sub-cases."}
                </p>
              )}
              {subCases.map((subCase) => (
                <CaseSummaryRow
                  key={subCase.id}
                  caseItem={subCase}
                  workspaceId={workspaceId}
                  trailing={
                    canUpdate ? (
                      <button
                        type="button"
                        onClick={() => handleRemove(subCase)}
                        disabled={removingId !== null}
                        className="flex size-6 shrink-0 items-center justify-center rounded-md text-muted-foreground opacity-0 transition hover:bg-muted hover:text-foreground focus-visible:opacity-100 group-hover:opacity-100 disabled:opacity-50"
                        aria-label={`Remove ${subCase.short_id} from this case`}
                        title="Remove from this case"
                      >
                        {removingId === subCase.id ? (
                          <Spinner className="size-3" />
                        ) : (
                          <X className="size-3.5" />
                        )}
                      </button>
                    ) : null
                  }
                />
              ))}
              {hasNextPage && (
                <button
                  type="button"
                  onClick={() => fetchNextPage()}
                  disabled={isFetchingNextPage}
                  className={cn(
                    CASE_PANEL_ACTION_ROW_CLASS,
                    "flex w-full items-center justify-center gap-2 text-xs text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                  )}
                >
                  {isFetchingNextPage && <Spinner className="size-3" />}
                  Load more
                </button>
              )}
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
      <AddSubCasesDialog
        open={addDialogOpen}
        onOpenChange={setAddDialogOpen}
        parent={caseData}
        workspaceId={workspaceId}
      />
    </>
  )
}

function useCasePickerSearch({
  workspaceId,
  enabled,
  includeSubCases,
}: {
  workspaceId: string
  enabled: boolean
  includeSubCases: boolean
}) {
  const [search, setSearch] = useState("")
  const [debouncedSearch] = useDebounce(search.trim(), 300)
  const { data, isLoading, hasNextPage, isFetchingNextPage, fetchNextPage } =
    useInfiniteQuery({
      queryKey: [
        "cases",
        "picker",
        workspaceId,
        includeSubCases,
        debouncedSearch,
      ],
      queryFn: ({ pageParam }) =>
        casesSearchCases({
          workspaceId,
          searchTerm: debouncedSearch || undefined,
          includeSubCases,
          limit: PICKER_PAGE_SIZE,
          cursor: (pageParam as string | null) ?? undefined,
        }),
      initialPageParam: null as string | null,
      getNextPageParam: (lastPage) =>
        lastPage.has_more && lastPage.next_cursor
          ? lastPage.next_cursor
          : undefined,
      enabled,
    })
  const items = useMemo(
    () => data?.pages.flatMap((page) => page.items) ?? [],
    [data]
  )
  return {
    search,
    setSearch,
    items,
    isLoading,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  }
}

/** Single-select dialog for choosing a top-level parent case. */
export function CaseParentPickerDialog({
  open,
  onOpenChange,
  workspaceId,
  excludeIds,
  title,
  description,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
  excludeIds: ReadonlySet<string>
  title: string
  description: string
  onConfirm: (parent: CaseReadMinimal) => Promise<void>
}) {
  const [selected, setSelected] = useState<CaseReadMinimal | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const { items, ...picker } = useCasePickerSearch({
    workspaceId,
    enabled: open,
    // Parents must be top-level cases.
    includeSubCases: false,
  })
  const candidates = items.filter((item) => !excludeIds.has(item.id))

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setSelected(null)
      picker.setSearch("")
    }
    onOpenChange(next)
  }

  const handleConfirm = async () => {
    if (!selected) return
    setIsSubmitting(true)
    try {
      await onConfirm(selected)
      handleOpenChange(false)
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader className="text-left">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <CasePickerList
          search={picker.search}
          onSearchChange={picker.setSearch}
          isLoading={picker.isLoading}
          candidates={candidates}
          hasNextPage={picker.hasNextPage}
          isFetchingNextPage={picker.isFetchingNextPage}
          onLoadMore={() => picker.fetchNextPage()}
          isSelected={(item) => selected?.id === item.id}
          onToggle={(item) => setSelected(item)}
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleConfirm} disabled={!selected || isSubmitting}>
            {isSubmitting && <Spinner className="mr-2 size-3" />}
            {selected ? `Group under ${selected.short_id}` : "Select a case"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AddSubCasesDialog({
  open,
  onOpenChange,
  parent,
  workspaceId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  parent: CaseRead
  workspaceId: string
}) {
  const queryClient = useQueryClient()
  const { toast } = useToast()
  const [selected, setSelected] = useState<Map<string, CaseReadMinimal>>(
    new Map()
  )
  const [isSubmitting, setIsSubmitting] = useState(false)
  const { items, ...picker } = useCasePickerSearch({
    workspaceId,
    enabled: open,
    // Sub-cases of other parents can be moved here.
    includeSubCases: true,
  })
  const candidates = items.filter(
    (item) => item.id !== parent.id && item.parent_id !== parent.id
  )

  const handleOpenChange = (next: boolean) => {
    if (!next) {
      setSelected(new Map())
      picker.setSearch("")
    }
    onOpenChange(next)
  }

  const toggle = (item: CaseReadMinimal) => {
    setSelected((prev) => {
      const next = new Map(prev)
      if (next.has(item.id)) {
        next.delete(item.id)
      } else {
        next.set(item.id, item)
      }
      return next
    })
  }

  const handleConfirm = async () => {
    setIsSubmitting(true)
    try {
      const result = await changeCasesParent({
        workspaceId,
        caseIds: [...selected.keys()],
        parentId: parent.id,
      })
      if (result.failed > 0) {
        toast({
          variant: "destructive",
          title: `${result.succeeded} added, ${result.failed} failed`,
          description: result.errors[0],
        })
      } else {
        toast({
          title: `Added ${result.succeeded} sub-case${result.succeeded === 1 ? "" : "s"}`,
        })
        handleOpenChange(false)
      }
    } catch (err) {
      console.error("Failed to add sub-cases:", err)
      toast({ variant: "destructive", title: "Failed to add sub-cases" })
    } finally {
      setIsSubmitting(false)
      await invalidateCaseHierarchy(queryClient)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader className="text-left">
          <DialogTitle>Add sub-cases to {parent.short_id}</DialogTitle>
          <DialogDescription>
            Selected cases are hidden from the cases list by default and listed
            under this case. Cases with their own sub-cases can't be added.
          </DialogDescription>
        </DialogHeader>
        <CasePickerList
          search={picker.search}
          onSearchChange={picker.setSearch}
          isLoading={picker.isLoading}
          candidates={candidates}
          hasNextPage={picker.hasNextPage}
          isFetchingNextPage={picker.isFetchingNextPage}
          onLoadMore={() => picker.fetchNextPage()}
          isSelected={(item) => selected.has(item.id)}
          isDisabled={(item) => (item.num_sub_cases ?? 0) > 0}
          onToggle={toggle}
          multiple
        />
        <DialogFooter>
          <Button variant="outline" onClick={() => handleOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleConfirm}
            disabled={selected.size === 0 || isSubmitting}
          >
            {isSubmitting && <Spinner className="mr-2 size-3" />}
            Add{selected.size > 0 ? ` ${selected.size}` : ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function CasePickerList({
  search,
  onSearchChange,
  isLoading,
  candidates,
  isSelected,
  isDisabled,
  onToggle,
  multiple = false,
  hasNextPage = false,
  isFetchingNextPage = false,
  onLoadMore,
}: {
  search: string
  onSearchChange: (value: string) => void
  isLoading: boolean
  candidates: CaseReadMinimal[]
  isSelected: (item: CaseReadMinimal) => boolean
  isDisabled?: (item: CaseReadMinimal) => boolean
  onToggle: (item: CaseReadMinimal) => void
  multiple?: boolean
  hasNextPage?: boolean
  isFetchingNextPage?: boolean
  onLoadMore?: () => void
}) {
  return (
    <div className="flex flex-col gap-2">
      <Input
        value={search}
        onChange={(e) => onSearchChange(e.target.value)}
        placeholder="Search cases by ID or summary..."
        aria-label="Search cases"
        autoFocus
      />
      <div
        role="listbox"
        aria-multiselectable={multiple}
        className="max-h-80 overflow-y-auto rounded-md border"
      >
        {isLoading && (
          <div className="flex justify-center p-4">
            <Spinner className="size-4" />
          </div>
        )}
        {!isLoading && candidates.length === 0 && (
          <p className="p-3 text-sm text-muted-foreground">No cases found</p>
        )}
        {candidates.map((item) => {
          const selected = isSelected(item)
          const disabled = isDisabled?.(item) ?? false
          return (
            <button
              key={item.id}
              type="button"
              role="option"
              aria-selected={selected}
              disabled={disabled}
              onClick={() => onToggle(item)}
              className={cn(
                "flex w-full items-center gap-2 border-b px-3 py-2 text-left text-sm last:border-b-0 hover:bg-muted/50 disabled:cursor-not-allowed disabled:opacity-50",
                selected && "bg-muted"
              )}
            >
              <span className="shrink-0 text-xs font-medium text-muted-foreground">
                {item.short_id}
              </span>
              <span className="min-w-0 flex-1 truncate">{item.summary}</span>
              {item.parent && (
                <span className="shrink-0 text-[10px] text-muted-foreground">
                  ↳ {item.parent.short_id}
                </span>
              )}
              {(item.num_sub_cases ?? 0) > 0 && (
                <span className="flex shrink-0 items-center gap-1 text-[10px] text-muted-foreground">
                  <ListTree className="size-3" />
                  {item.num_sub_cases}
                </span>
              )}
            </button>
          )
        })}
        {hasNextPage && onLoadMore && (
          <button
            type="button"
            onClick={onLoadMore}
            disabled={isFetchingNextPage}
            className="flex w-full items-center justify-center gap-2 px-3 py-2 text-xs text-muted-foreground hover:bg-muted/50 hover:text-foreground"
          >
            {isFetchingNextPage && <Spinner className="size-3" />}
            Load more
          </button>
        )}
      </div>
    </div>
  )
}
