"use client"

import type { Row } from "@tanstack/react-table"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type {
  CasePriority,
  CaseReadMinimal,
  CaseSeverity,
  CaseStatus,
  CaseUpdate,
} from "@/client"
import { casesUpdateCase } from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UNASSIGNED } from "@/components/cases/case-panel-selectors"
import { useCaseSelection } from "@/components/cases/case-selection-context"
import { createColumns } from "@/components/cases/case-table-columns"
import {
  CaseTableFilters,
  type FilterMode,
} from "@/components/cases/case-table-filters"
import { DeleteCaseAlertDialog } from "@/components/cases/delete-case-dialog"
import { DataTable } from "@/components/data-table"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import { useCasesPagination } from "@/hooks"
import { useAuth } from "@/hooks/use-auth"
import { useDebounce } from "@/hooks/use-debounce"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { useDeleteCase } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

type StoredFilters = {
  searchTerm: string
  statusFilter: CaseStatus[]
  statusMode: FilterMode
  priorityFilter: CasePriority[]
  priorityMode: FilterMode
  severityFilter: CaseSeverity[]
  severityMode: FilterMode
  assigneeFilter: string[]
  assigneeMode: FilterMode
}

const FILTER_STORAGE_PREFIX = "tracecat.caseTableFilters"

const DEFAULT_FILTERS: StoredFilters = {
  searchTerm: "",
  statusFilter: [],
  statusMode: "include",
  priorityFilter: [],
  priorityMode: "include",
  severityFilter: [],
  severityMode: "include",
  assigneeFilter: [],
  assigneeMode: "include",
}

const STATUS_VALUE_SET = new Set<CaseStatus>(
  Object.values(STATUSES).map((status) => status.value)
)
const PRIORITY_VALUE_SET = new Set<CasePriority>(
  Object.values(PRIORITIES).map((priority) => priority.value)
)
const SEVERITY_VALUE_SET = new Set<CaseSeverity>(
  Object.values(SEVERITIES).map((severity) => severity.value)
)

const ensureFilterMode = (value: unknown): FilterMode =>
  value === "exclude" ? "exclude" : "include"

const ensureEnumArray = <T extends string>(
  value: unknown,
  allowedValues: Set<T>
): T[] => {
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter(
    (item): item is T =>
      typeof item === "string" && allowedValues.has(item as T)
  )
}

const ensureStringArray = (value: unknown): string[] => {
  if (!Array.isArray(value)) {
    return []
  }
  return value.filter((item): item is string => typeof item === "string")
}

const getFilterStorageKey = (
  workspaceId: string | undefined,
  userId: string | undefined
) => `${FILTER_STORAGE_PREFIX}:${workspaceId ?? "unknown"}:${userId ?? "guest"}`

const loadFiltersFromStorage = (key: string): StoredFilters | null => {
  if (typeof window === "undefined") {
    return null
  }

  try {
    const raw = window.localStorage.getItem(key)
    if (!raw) {
      return null
    }

    const parsed = JSON.parse(raw) as Partial<StoredFilters>

    return {
      searchTerm:
        typeof parsed.searchTerm === "string"
          ? parsed.searchTerm
          : DEFAULT_FILTERS.searchTerm,
      statusFilter: ensureEnumArray(parsed.statusFilter, STATUS_VALUE_SET),
      statusMode: ensureFilterMode(parsed.statusMode),
      priorityFilter: ensureEnumArray(
        parsed.priorityFilter,
        PRIORITY_VALUE_SET
      ),
      priorityMode: ensureFilterMode(parsed.priorityMode),
      severityFilter: ensureEnumArray(
        parsed.severityFilter,
        SEVERITY_VALUE_SET
      ),
      severityMode: ensureFilterMode(parsed.severityMode),
      assigneeFilter: ensureStringArray(parsed.assigneeFilter),
      assigneeMode: ensureFilterMode(parsed.assigneeMode),
    }
  } catch (error) {
    console.warn("Failed to parse saved case filters", error)
    return null
  }
}

const saveFiltersToStorage = (key: string, filters: StoredFilters) => {
  if (typeof window === "undefined") {
    return
  }

  try {
    window.localStorage.setItem(key, JSON.stringify(filters))
  } catch (error) {
    console.warn("Failed to persist case filters", error)
  }
}

const arraysEqual = <T extends string>(a: T[], b: T[]) =>
  a.length === b.length && a.every((value, index) => value === b[index])

export default function CaseTable() {
  const { user } = useAuth()
  const workspaceId = useWorkspaceId()
  const searchParams = useSearchParams()
  const tagFilters = searchParams?.getAll("tag") ?? []
  const [pageSize, setPageSize] = useState(20)
  const [selectedCase, setSelectedCase] = useState<CaseReadMinimal | null>(null)
  const [selectedRows, setSelectedRows] = useState<Row<CaseReadMinimal>[]>([])
  const [clearSelectionTrigger, setClearSelectionTrigger] = useState(0)
  const router = useRouter()
  const { updateSelection, resetSelection } = useCaseSelection()
  const storageKey = getFilterStorageKey(workspaceId, user?.id)
  const storedFilters = useMemo(
    () => loadFiltersFromStorage(storageKey),
    [storageKey]
  )
  const initialFilters = storedFilters ?? DEFAULT_FILTERS

  // Server-side filter states
  const [searchTerm, setSearchTerm] = useState<string>(
    () => initialFilters.searchTerm
  )
  const [statusFilter, setStatusFilter] = useState<CaseStatus[]>(
    () => initialFilters.statusFilter
  )
  const [priorityFilter, setPriorityFilter] = useState<CasePriority[]>(
    () => initialFilters.priorityFilter
  )
  const [severityFilter, setSeverityFilter] = useState<CaseSeverity[]>(
    () => initialFilters.severityFilter
  )
  const [assigneeFilter, setAssigneeFilter] = useState<string[]>(
    () => initialFilters.assigneeFilter
  )
  const [statusMode, setStatusMode] = useState<FilterMode>(
    () => initialFilters.statusMode
  )
  const [priorityMode, setPriorityMode] = useState<FilterMode>(
    () => initialFilters.priorityMode
  )
  const [severityMode, setSeverityMode] = useState<FilterMode>(
    () => initialFilters.severityMode
  )
  const [assigneeMode, setAssigneeMode] = useState<FilterMode>(
    () => initialFilters.assigneeMode
  )
  // Debounce search term for better performance
  const [debouncedSearchTerm] = useDebounce(searchTerm, 300)
  const { members } = useWorkspaceMembers(workspaceId)
  const filtersHydratedRef = useRef(false)

  const statusValues = useMemo(
    () => Object.values(STATUSES).map((status) => status.value),
    []
  )
  const priorityValues = useMemo(
    () => Object.values(PRIORITIES).map((priority) => priority.value),
    []
  )
  const severityValues = useMemo(
    () => Object.values(SEVERITIES).map((severity) => severity.value),
    []
  )
  const assigneeValues = useMemo(() => {
    const workspaceMembers = members?.map((member) => member.user_id) ?? []
    return [UNASSIGNED, ...workspaceMembers]
  }, [members])

  const deriveFilterValues = useCallback(
    <T extends string>(
      selected: T[],
      mode: FilterMode,
      allValues: readonly T[]
    ) => {
      if (selected.length === 0) {
        return { values: null as T[] | null, forceEmpty: false }
      }

      if (mode === "include") {
        return { values: selected, forceEmpty: false }
      }

      const exclusionSet = new Set(selected)
      const complement = allValues.filter((value) => !exclusionSet.has(value))

      if (complement.length === 0) {
        return { values: null as T[] | null, forceEmpty: true }
      }

      return { values: complement, forceEmpty: false }
    },
    []
  )

  const { values: statusFilterForQuery, forceEmpty: statusForceEmpty } =
    deriveFilterValues(statusFilter, statusMode, statusValues)
  const { values: priorityFilterForQuery, forceEmpty: priorityForceEmpty } =
    deriveFilterValues(priorityFilter, priorityMode, priorityValues)
  const { values: severityFilterForQuery, forceEmpty: severityForceEmpty } =
    deriveFilterValues(severityFilter, severityMode, severityValues)
  const { values: assigneeFilterRaw, forceEmpty: assigneeForceEmpty } =
    deriveFilterValues(assigneeFilter, assigneeMode, assigneeValues)

  const assigneeFilterForQuery = assigneeFilterRaw
    ? assigneeFilterRaw.map((value) =>
        value === UNASSIGNED ? "unassigned" : value
      )
    : null

  const forceEmptyResults =
    statusForceEmpty ||
    priorityForceEmpty ||
    severityForceEmpty ||
    assigneeForceEmpty

  const {
    data: cases,
    isLoading: casesIsLoading,
    error: casesError,
    goToNextPage,
    goToPreviousPage,
    goToFirstPage,
    hasNextPage,
    hasPreviousPage,
    currentPage,
    totalEstimate,
    startItem,
    endItem,
    refetch,
  } = useCasesPagination({
    workspaceId,
    limit: pageSize,
    searchTerm: debouncedSearchTerm || null,
    status: statusFilterForQuery,
    priority: priorityFilterForQuery,
    severity: severityFilterForQuery,
    assigneeIds: assigneeFilterForQuery,
    tags: tagFilters.length > 0 ? tagFilters : null,
  })
  const { toast } = useToast()
  const [isDeleting, setIsDeleting] = useState(false)
  const [isBulkUpdating, setIsBulkUpdating] = useState(false)
  const { deleteCase } = useDeleteCase({
    workspaceId,
  })

  const goToFirstPageRef = useRef(goToFirstPage)

  useEffect(() => {
    goToFirstPageRef.current = goToFirstPage
  }, [goToFirstPage])

  useEffect(() => {
    if (typeof window === "undefined") {
      return
    }

    const savedFilters = loadFiltersFromStorage(storageKey)
    const nextFilters = savedFilters ?? DEFAULT_FILTERS
    let filtersChanged = false

    setSearchTerm((previous) => {
      if (previous !== nextFilters.searchTerm) {
        filtersChanged = true
        return nextFilters.searchTerm
      }
      return previous
    })
    setStatusFilter((previous) => {
      if (!arraysEqual(previous, nextFilters.statusFilter)) {
        filtersChanged = true
        return nextFilters.statusFilter
      }
      return previous
    })
    setPriorityFilter((previous) => {
      if (!arraysEqual(previous, nextFilters.priorityFilter)) {
        filtersChanged = true
        return nextFilters.priorityFilter
      }
      return previous
    })
    setSeverityFilter((previous) => {
      if (!arraysEqual(previous, nextFilters.severityFilter)) {
        filtersChanged = true
        return nextFilters.severityFilter
      }
      return previous
    })
    setAssigneeFilter((previous) => {
      if (!arraysEqual(previous, nextFilters.assigneeFilter)) {
        filtersChanged = true
        return nextFilters.assigneeFilter
      }
      return previous
    })
    setStatusMode((previous) => {
      if (previous !== nextFilters.statusMode) {
        filtersChanged = true
        return nextFilters.statusMode
      }
      return previous
    })
    setPriorityMode((previous) => {
      if (previous !== nextFilters.priorityMode) {
        filtersChanged = true
        return nextFilters.priorityMode
      }
      return previous
    })
    setSeverityMode((previous) => {
      if (previous !== nextFilters.severityMode) {
        filtersChanged = true
        return nextFilters.severityMode
      }
      return previous
    })
    setAssigneeMode((previous) => {
      if (previous !== nextFilters.assigneeMode) {
        filtersChanged = true
        return nextFilters.assigneeMode
      }
      return previous
    })

    filtersHydratedRef.current = true

    if (filtersChanged) {
      goToFirstPageRef.current()
    }
  }, [storageKey])

  useEffect(() => {
    if (typeof window === "undefined") {
      return
    }

    if (!filtersHydratedRef.current) {
      return
    }

    saveFiltersToStorage(storageKey, {
      searchTerm,
      statusFilter,
      statusMode,
      priorityFilter,
      priorityMode,
      severityFilter,
      severityMode,
      assigneeFilter,
      assigneeMode,
    })
  }, [
    assigneeFilter,
    assigneeMode,
    priorityFilter,
    priorityMode,
    searchTerm,
    severityFilter,
    severityMode,
    statusFilter,
    statusMode,
    storageKey,
  ])

  const memoizedColumns = useMemo(
    () => createColumns(setSelectedCase),
    [setSelectedCase]
  )

  function handleClickRow(row: Row<CaseReadMinimal>) {
    return () =>
      router.push(`/workspaces/${workspaceId}/cases/${row.original.id}`)
  }

  const handleDeleteRows = useCallback(
    async (selectedRows: Row<CaseReadMinimal>[]) => {
      if (selectedRows.length === 0) return

      try {
        setIsDeleting(true)
        // Get IDs of selected cases
        const caseIds = selectedRows.map((row) => row.original.id)

        // Call the delete operation
        await Promise.all(caseIds.map((caseId) => deleteCase(caseId)))

        // Show success toast
        toast({
          title: `${caseIds.length} case(s) deleted`,
          description: "The selected cases have been deleted successfully.",
        })

        // Refresh the cases list
        await refetch()
        setSelectedRows([])
        setClearSelectionTrigger((value) => value + 1)
      } catch (error) {
        console.error("Failed to delete cases:", error)
        toast({
          variant: "destructive",
          title: "Failed to delete cases",
          description: "Please try again.",
        })
      } finally {
        setIsDeleting(false)
      }
    },
    [deleteCase, refetch, toast]
  )

  const handleBulkDelete = useCallback(async () => {
    await handleDeleteRows(selectedRows)
  }, [handleDeleteRows, selectedRows])

  const handleBulkUpdate = useCallback(
    async (
      updates: Partial<CaseUpdate>,
      options?: { successTitle?: string; successDescription?: string }
    ) => {
      if (selectedRows.length === 0) {
        return
      }

      const caseIds = selectedRows.map((row) => row.original.id)

      try {
        setIsBulkUpdating(true)

        await Promise.all(
          caseIds.map((caseId) =>
            casesUpdateCase({
              workspaceId,
              caseId,
              requestBody: updates,
            })
          )
        )

        toast({
          title:
            options?.successTitle ||
            `Updated ${caseIds.length} case${caseIds.length > 1 ? "s" : ""}`,
          description:
            options?.successDescription ||
            "The selected cases have been updated successfully.",
        })

        await refetch()
      } catch (error) {
        console.error("Failed to update cases:", error)
        toast({
          variant: "destructive",
          title: "Failed to update cases",
          description: "Please try again.",
        })
      } finally {
        setIsBulkUpdating(false)
      }
    },
    [refetch, selectedRows, toast, workspaceId]
  )

  const handleClearSelection = useCallback(() => {
    setSelectedRows([])
    setClearSelectionTrigger((value) => value + 1)
  }, [setClearSelectionTrigger, setSelectedRows])

  useEffect(() => {
    if (selectedRows.length > 0) {
      updateSelection({
        selectedCount: selectedRows.length,
        selectedCaseIds: selectedRows.map((row) => row.original.id),
        clearSelection: handleClearSelection,
        deleteSelected: handleBulkDelete,
        bulkUpdateSelectedCases: handleBulkUpdate,
        isDeleting,
        isUpdating: isBulkUpdating,
      })
    } else {
      resetSelection()
    }
  }, [
    selectedRows,
    handleClearSelection,
    handleBulkDelete,
    handleBulkUpdate,
    isDeleting,
    isBulkUpdating,
    resetSelection,
    updateSelection,
  ])

  useEffect(() => () => resetSelection(), [resetSelection])

  // Handle filter changes
  const handleSearchChange = useCallback(
    (value: string) => {
      setSearchTerm(value)
      if (value !== searchTerm) {
        goToFirstPage()
      }
    },
    [searchTerm, goToFirstPage]
  )

  const handleStatusChange = useCallback(
    (value: CaseStatus[]) => {
      setStatusFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handleStatusModeChange = useCallback(
    (mode: FilterMode) => {
      setStatusMode((current) => {
        if (current === mode) {
          return current
        }
        goToFirstPage()
        return mode
      })
    },
    [goToFirstPage]
  )

  const handlePriorityChange = useCallback(
    (value: CasePriority[]) => {
      setPriorityFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handlePriorityModeChange = useCallback(
    (mode: FilterMode) => {
      setPriorityMode((current) => {
        if (current === mode) {
          return current
        }
        goToFirstPage()
        return mode
      })
    },
    [goToFirstPage]
  )

  const handleSeverityChange = useCallback(
    (value: CaseSeverity[]) => {
      setSeverityFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handleSeverityModeChange = useCallback(
    (mode: FilterMode) => {
      setSeverityMode((current) => {
        if (current === mode) {
          return current
        }
        goToFirstPage()
        return mode
      })
    },
    [goToFirstPage]
  )

  const handleAssigneeChange = useCallback(
    (value: string[]) => {
      setAssigneeFilter(value)
      goToFirstPage()
    },
    [goToFirstPage]
  )

  const handleAssigneeModeChange = useCallback(
    (mode: FilterMode) => {
      setAssigneeMode((current) => {
        if (current === mode) {
          return current
        }
        goToFirstPage()
        return mode
      })
    },
    [goToFirstPage]
  )

  const tableData = forceEmptyResults ? [] : cases
  const displayedTotalEstimate = forceEmptyResults ? 0 : totalEstimate
  const displayedStartItem = forceEmptyResults ? 0 : startItem
  const displayedEndItem = forceEmptyResults ? 0 : endItem
  const displayedHasNextPage = forceEmptyResults ? false : hasNextPage
  const displayedHasPreviousPage = forceEmptyResults ? false : hasPreviousPage

  return (
    <DeleteCaseAlertDialog
      selectedCase={selectedCase}
      setSelectedCase={setSelectedCase}
    >
      <TooltipProvider>
        <div className="space-y-4">
          <CaseTableFilters
            searchTerm={searchTerm}
            onSearchChange={handleSearchChange}
            statusFilter={statusFilter}
            onStatusChange={handleStatusChange}
            statusMode={statusMode}
            onStatusModeChange={handleStatusModeChange}
            priorityFilter={priorityFilter}
            onPriorityChange={handlePriorityChange}
            priorityMode={priorityMode}
            onPriorityModeChange={handlePriorityModeChange}
            severityFilter={severityFilter}
            onSeverityChange={handleSeverityChange}
            severityMode={severityMode}
            onSeverityModeChange={handleSeverityModeChange}
            assigneeFilter={assigneeFilter}
            onAssigneeChange={handleAssigneeChange}
            assigneeMode={assigneeMode}
            onAssigneeModeChange={handleAssigneeModeChange}
            members={members}
          />
          <DataTable
            data={tableData}
            isLoading={casesIsLoading || isDeleting}
            error={(casesError as Error) || undefined}
            columns={memoizedColumns}
            onClickRow={handleClickRow}
            getRowHref={(row) =>
              `/workspaces/${workspaceId}/cases/${row.original.id}`
            }
            tableId={`${user?.id}-${workspaceId}-cases`}
            onSelectionChange={setSelectedRows}
            clearSelectionTrigger={clearSelectionTrigger}
            serverSidePagination={{
              currentPage,
              hasNextPage: displayedHasNextPage,
              hasPreviousPage: displayedHasPreviousPage,
              pageSize,
              totalEstimate: displayedTotalEstimate,
              startItem: displayedStartItem,
              endItem: displayedEndItem,
              onNextPage: goToNextPage,
              onPreviousPage: goToPreviousPage,
              onFirstPage: goToFirstPage,
              onPageSizeChange: setPageSize,
              isLoading: casesIsLoading || isDeleting,
            }}
          />
        </div>
      </TooltipProvider>
    </DeleteCaseAlertDialog>
  )
}
