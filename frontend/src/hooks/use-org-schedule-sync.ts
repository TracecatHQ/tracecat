"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import type { ApiError } from "@/client"
import {
  type OrgScheduleRecreateResponse,
  type OrgScheduleTemporalSyncRead,
  organizationGetScheduleTemporalSync,
  organizationRecreateMissingTemporalSchedules,
} from "@/client/services.custom"
import { toast } from "@/components/ui/use-toast"

const TEMPORAL_SYNC_QUERY_KEY = ["organization", "schedules", "temporal-sync"]

export function useOrgScheduleSync() {
  const queryClient = useQueryClient()
  const { data, isLoading, isFetching, error, refetch } = useQuery<
    OrgScheduleTemporalSyncRead,
    ApiError
  >({
    queryKey: TEMPORAL_SYNC_QUERY_KEY,
    queryFn: organizationGetScheduleTemporalSync,
    retry: false,
  })

  const {
    mutateAsync: recreateMissingSchedules,
    isPending: recreateMissingSchedulesIsPending,
  } = useMutation<
    OrgScheduleRecreateResponse,
    ApiError,
    { scheduleIds?: string[] }
  >({
    mutationFn: async ({ scheduleIds }) =>
      await organizationRecreateMissingTemporalSchedules({
        requestBody:
          scheduleIds && scheduleIds.length > 0
            ? { schedule_ids: scheduleIds }
            : {},
      }),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: TEMPORAL_SYNC_QUERY_KEY })
      toast({
        title: "Temporal schedule sync complete",
        description: `${result.created_count} created, ${result.already_present_count} already present, ${result.failed_count} failed.`,
      })
    },
    onError: (err) => {
      const detail =
        typeof err.body === "object" &&
        err.body !== null &&
        "detail" in err.body &&
        typeof err.body.detail === "string"
          ? err.body.detail
          : "Could not recreate missing schedules."
      toast({
        title: "Temporal schedule sync failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  return {
    scheduleSync: data,
    scheduleSyncIsLoading: isLoading,
    scheduleSyncIsFetching: isFetching,
    scheduleSyncError: error,
    refreshScheduleSync: refetch,
    recreateMissingSchedules,
    recreateMissingSchedulesIsPending,
  }
}
