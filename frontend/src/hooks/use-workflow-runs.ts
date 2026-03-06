"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type ApiError,
  type TriggerType,
  type WorkflowExecutionBulkResetResponse,
  type WorkflowExecutionRelationFilter,
  type WorkflowExecutionResetPointRead,
  type WorkflowExecutionResetReapplyType,
  type WorkflowExecutionStatusFilterMode,
  type WorkflowExecutionsSearchWorkflowExecutionsData,
  type WorkflowRunReadMinimal,
  workflowExecutionsBulkResetWorkflowExecutions,
  workflowExecutionsCancelWorkflowExecution,
  workflowExecutionsListWorkflowExecutionResetPoints,
  workflowExecutionsResetWorkflowExecution,
  workflowExecutionsSearchWorkflowExecutions,
  workflowExecutionsTerminateWorkflowExecution,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import {
  type CursorPaginationParams,
  useCursorPagination,
} from "@/hooks/pagination/use-cursor-pagination"
import { useWorkspaceId } from "@/providers/workspace-id"

const WORKFLOW_RUNS_PAGE_SIZE = 50
const WORKFLOW_RUNS_POLL_INTERVAL_MS = 10_000
type WorkflowExecutionStatusFilter = NonNullable<
  WorkflowExecutionsSearchWorkflowExecutionsData["status"]
>[number]

const WORKFLOW_ID_SHORT_PATTERN = /^wf_[0-9A-Za-z]+$/
const LEGACY_WORKFLOW_ID_PATTERN = /^wf-[0-9a-f]{32}$/
const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i

function isWorkflowIdSearchTerm(value: string): boolean {
  return (
    WORKFLOW_ID_SHORT_PATTERN.test(value) ||
    LEGACY_WORKFLOW_ID_PATTERN.test(value) ||
    UUID_PATTERN.test(value)
  )
}

export interface UseWorkflowRunsFilters {
  searchTerm?: string
  relation?: WorkflowExecutionRelationFilter
  status?: WorkflowExecutionStatusFilter[]
  statusMode?: WorkflowExecutionStatusFilterMode
  startTimeFrom?: string | null
  startTimeTo?: string | null
  closeTimeFrom?: string | null
  closeTimeTo?: string | null
  durationGteSeconds?: number | null
  durationLteSeconds?: number | null
  workflowId?: string | null
  trigger?: TriggerType[]
  userId?: string
}

export interface UseWorkflowRunsOptions {
  enabled?: boolean
  limit?: number
}

interface WorkflowRunsPaginationParams extends CursorPaginationParams {
  workflowId?: string | null
  trigger?: TriggerType[] | null
  userId?: string | null
  status?: WorkflowExecutionStatusFilter[] | null
  statusMode?: WorkflowExecutionStatusFilterMode
  startTimeFrom?: string | null
  startTimeTo?: string | null
  closeTimeFrom?: string | null
  closeTimeTo?: string | null
  durationGteSeconds?: number | null
  durationLteSeconds?: number | null
  searchTerm?: string | null
  relation?: WorkflowExecutionRelationFilter
}

function normalizeString(value?: string | null): string | null {
  const normalized = value?.trim() ?? ""
  return normalized.length > 0 ? normalized : null
}

export function useWorkflowRuns(
  filters: UseWorkflowRunsFilters,
  options: UseWorkflowRunsOptions = {}
) {
  const workspaceId = useWorkspaceId()
  const enabled = options.enabled ?? true
  const limit = options.limit ?? WORKFLOW_RUNS_PAGE_SIZE
  const normalizedSearchTerm = filters.searchTerm?.trim() ?? ""
  const normalizedRelation = filters.relation ?? "all"
  const normalizedStatusMode = filters.statusMode ?? "include"
  const normalizedStatus =
    filters.status && filters.status.length > 0
      ? [...filters.status].sort((left, right) => left.localeCompare(right))
      : null
  const normalizedTrigger =
    filters.trigger && filters.trigger.length > 0
      ? [...filters.trigger].sort((left, right) => left.localeCompare(right))
      : null
  const normalizedUserId = normalizeString(filters.userId)
  const parsedSearchWorkflowId =
    filters.workflowId ??
    (normalizedSearchTerm && isWorkflowIdSearchTerm(normalizedSearchTerm)
      ? normalizedSearchTerm
      : null)
  const parsedSearchTerm =
    (filters.workflowId ?? parsedSearchWorkflowId)
      ? null
      : normalizedSearchTerm || null

  const pagination = useCursorPagination<
    WorkflowRunReadMinimal,
    WorkflowRunsPaginationParams
  >({
    workspaceId,
    limit,
    enabled,
    queryKey: [
      "workflow-runs",
      workspaceId,
      parsedSearchTerm,
      parsedSearchWorkflowId,
      normalizedRelation,
      normalizedStatusMode,
      normalizedStatus?.join(",") ?? null,
      normalizedTrigger?.join(",") ?? null,
      normalizedUserId,
      filters.startTimeFrom ?? null,
      filters.startTimeTo ?? null,
      filters.closeTimeFrom ?? null,
      filters.closeTimeTo ?? null,
      filters.durationGteSeconds ?? null,
      filters.durationLteSeconds ?? null,
    ],
    queryFn: async (
      params: WorkflowRunsPaginationParams
    ): Promise<Awaited<ReturnType<typeof adaptedWorkflowRunsSearch>>> =>
      await adaptedWorkflowRunsSearch({
        workspaceId: params.workspaceId,
        limit: params.limit,
        cursor: params.cursor,
        workflowId: params.workflowId,
        trigger: params.trigger,
        userId: params.userId,
        status: params.status,
        statusMode: params.statusMode,
        startTimeFrom: params.startTimeFrom,
        startTimeTo: params.startTimeTo,
        closeTimeFrom: params.closeTimeFrom,
        closeTimeTo: params.closeTimeTo,
        durationGteSeconds: params.durationGteSeconds,
        durationLteSeconds: params.durationLteSeconds,
        searchTerm: params.searchTerm,
        relation: params.relation,
      }),
    additionalParams: {
      workflowId: parsedSearchWorkflowId,
      trigger: normalizedTrigger,
      userId: normalizedUserId,
      status: normalizedStatus,
      statusMode: normalizedStatusMode,
      startTimeFrom: filters.startTimeFrom ?? null,
      startTimeTo: filters.startTimeTo ?? null,
      closeTimeFrom: filters.closeTimeFrom ?? null,
      closeTimeTo: filters.closeTimeTo ?? null,
      durationGteSeconds: filters.durationGteSeconds ?? null,
      durationLteSeconds: filters.durationLteSeconds ?? null,
      searchTerm: parsedSearchTerm,
      relation: normalizedRelation,
    },
    refetchInterval: WORKFLOW_RUNS_POLL_INTERVAL_MS,
    refetchIntervalInBackground: false,
    refetchOnWindowFocus: true,
  })

  const runs = pagination.data
  const startItem = runs.length > 0 ? pagination.startItem : 0
  const endItem = runs.length > 0 ? pagination.endItem : 0

  return {
    runs,
    hasNextPage: pagination.hasNextPage,
    hasPreviousPage: pagination.hasPreviousPage,
    goToNextPage: pagination.goToNextPage,
    goToPreviousPage: pagination.goToPreviousPage,
    goToFirstPage: pagination.goToFirstPage,
    currentPage: pagination.currentPage,
    pageSize: pagination.pageSize,
    startItem,
    endItem,
    totalEstimate: pagination.totalEstimate,
    isLoading: pagination.isLoading,
    error: pagination.error,
    refetch: pagination.refetch,
  }
}

async function adaptedWorkflowRunsSearch(
  params: WorkflowExecutionsSearchWorkflowExecutionsData
) {
  const response = await workflowExecutionsSearchWorkflowExecutions(params)
  return {
    items: response.items,
    next_cursor: response.next_cursor,
    prev_cursor: response.prev_cursor,
    has_more: response.has_more,
    has_previous: response.has_previous,
    total_estimate: response.total_estimate,
  }
}

export function useWorkflowExecutionResetPoints(
  executionId: string | null,
  options: { enabled?: boolean } = {}
) {
  const workspaceId = useWorkspaceId()
  const enabled = options.enabled ?? true

  return useQuery<WorkflowExecutionResetPointRead[], ApiError>({
    enabled: enabled && !!executionId,
    queryKey: ["workflow-run-reset-points", workspaceId, executionId],
    queryFn: async () => {
      if (!executionId) {
        return []
      }
      return await workflowExecutionsListWorkflowExecutionResetPoints({
        workspaceId,
        executionId: encodeURIComponent(executionId),
        limit: 500,
      })
    },
  })
}

export interface WorkflowRunResetInput {
  executionId: string
  eventId?: number | null
  reason?: string | null
  reapplyType?: WorkflowExecutionResetReapplyType
}

export interface WorkflowRunBulkResetInput {
  executionIds: string[]
  eventId?: number | null
  reason?: string | null
  reapplyType?: WorkflowExecutionResetReapplyType
}

export interface WorkflowRunBulkCancelInput {
  executionIds: string[]
}

export interface WorkflowRunBulkTerminateInput {
  executionIds: string[]
  reason?: string
}

interface BulkWorkflowMutationResult {
  total: number
  success: number
  failed: number
}

function toBulkMutationResult<T>(results: PromiseSettledResult<T>[]) {
  const failed = results.filter((result) => result.status === "rejected").length
  return {
    total: results.length,
    success: results.length - failed,
    failed,
  } satisfies BulkWorkflowMutationResult
}

export function useWorkflowRunMutations() {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()

  const invalidateRuns = async () => {
    await queryClient.invalidateQueries({
      queryKey: ["workflow-runs", workspaceId],
    })
  }

  const { mutateAsync: cancelRun, isPending: isCanceling } = useMutation({
    mutationFn: async (executionId: string) =>
      await workflowExecutionsCancelWorkflowExecution({
        workspaceId,
        executionId: encodeURIComponent(executionId),
      }),
    onSuccess: async () => {
      await invalidateRuns()
      toast({
        title: "Cancel requested",
        description: "The workflow run cancellation request was sent.",
      })
    },
  })

  const { mutateAsync: terminateRun, isPending: isTerminating } = useMutation({
    mutationFn: async ({
      executionId,
      reason,
    }: {
      executionId: string
      reason?: string
    }) =>
      await workflowExecutionsTerminateWorkflowExecution({
        workspaceId,
        executionId: encodeURIComponent(executionId),
        requestBody: {
          reason: reason ?? "Terminated from runs page",
        },
      }),
    onSuccess: async () => {
      await invalidateRuns()
      toast({
        title: "Termination requested",
        description: "The workflow run termination request was sent.",
      })
    },
  })

  const { mutateAsync: bulkCancelRuns, isPending: isBulkCanceling } =
    useMutation({
      mutationFn: async (input: WorkflowRunBulkCancelInput) => {
        if (input.executionIds.length === 0) {
          return {
            total: 0,
            success: 0,
            failed: 0,
          } satisfies BulkWorkflowMutationResult
        }
        const results = await Promise.allSettled(
          input.executionIds.map(async (executionId) =>
            workflowExecutionsCancelWorkflowExecution({
              workspaceId,
              executionId: encodeURIComponent(executionId),
            })
          )
        )
        return toBulkMutationResult(results)
      },
      onSuccess: async (result) => {
        await invalidateRuns()
        if (result.total === 0) {
          return
        }
        if (result.failed === 0) {
          toast({
            title: "Bulk cancel requested",
            description: `Cancellation requested for ${result.success} run(s).`,
          })
          return
        }
        toast({
          variant: "destructive",
          title: "Bulk cancel completed with errors",
          description: `${result.success} run(s) canceled, ${result.failed} failed.`,
        })
      },
    })

  const { mutateAsync: bulkTerminateRuns, isPending: isBulkTerminating } =
    useMutation({
      mutationFn: async (input: WorkflowRunBulkTerminateInput) => {
        if (input.executionIds.length === 0) {
          return {
            total: 0,
            success: 0,
            failed: 0,
          } satisfies BulkWorkflowMutationResult
        }
        const results = await Promise.allSettled(
          input.executionIds.map(async (executionId) =>
            workflowExecutionsTerminateWorkflowExecution({
              workspaceId,
              executionId: encodeURIComponent(executionId),
              requestBody: {
                reason: input.reason ?? "Terminated from runs page",
              },
            })
          )
        )
        return toBulkMutationResult(results)
      },
      onSuccess: async (result) => {
        await invalidateRuns()
        if (result.total === 0) {
          return
        }
        if (result.failed === 0) {
          toast({
            title: "Bulk termination requested",
            description: `Termination requested for ${result.success} run(s).`,
          })
          return
        }
        toast({
          variant: "destructive",
          title: "Bulk termination completed with errors",
          description: `${result.success} run(s) terminated, ${result.failed} failed.`,
        })
      },
    })

  const { mutateAsync: resetRun, isPending: isResetting } = useMutation({
    mutationFn: async (input: WorkflowRunResetInput) =>
      await workflowExecutionsResetWorkflowExecution({
        workspaceId,
        executionId: encodeURIComponent(input.executionId),
        requestBody: {
          event_id: input.eventId ?? null,
          reason: input.reason ?? null,
          reapply_type: input.reapplyType ?? "all_eligible",
        },
      }),
    onSuccess: async () => {
      await invalidateRuns()
      toast({
        title: "Reset started",
        description: "The workflow run reset request was sent.",
      })
    },
  })

  const { mutateAsync: bulkResetRuns, isPending: isBulkResetting } =
    useMutation({
      mutationFn: async (input: WorkflowRunBulkResetInput) =>
        await workflowExecutionsBulkResetWorkflowExecutions({
          workspaceId,
          requestBody: {
            execution_ids: input.executionIds,
            event_id: input.eventId ?? null,
            reason: input.reason ?? null,
            reapply_type: input.reapplyType ?? "all_eligible",
          },
        }),
      onSuccess: async (response: WorkflowExecutionBulkResetResponse) => {
        await invalidateRuns()
        const failures = (response.results ?? []).filter((result) => !result.ok)
        if (failures.length === 0) {
          toast({
            title: "Bulk reset started",
            description: "All selected workflow runs were reset.",
          })
        } else {
          toast({
            variant: "destructive",
            title: "Bulk reset completed with errors",
            description: `${failures.length} run(s) failed to reset.`,
          })
        }
      },
    })

  return {
    cancelRun,
    terminateRun,
    bulkCancelRuns,
    bulkTerminateRuns,
    resetRun,
    bulkResetRuns,
    isCanceling,
    isTerminating,
    isBulkCanceling,
    isBulkTerminating,
    isResetting,
    isBulkResetting,
  }
}

export function groupWorkflowRunsByStatus(runs: WorkflowRunReadMinimal[]) {
  const groups: Record<string, WorkflowRunReadMinimal[]> = {
    RUNNING: [],
    FAILED: [],
    CANCELED: [],
    TERMINATED: [],
    TIMED_OUT: [],
    COMPLETED: [],
    CONTINUED_AS_NEW: [],
    UNKNOWN: [],
  }

  for (const run of runs) {
    const key = run.status ?? "UNKNOWN"
    if (key in groups) {
      groups[key].push(run)
    } else {
      groups.UNKNOWN.push(run)
    }
  }

  return groups
}
