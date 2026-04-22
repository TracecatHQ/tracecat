"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type SpmEndpointCreate,
  type SpmFindingDecisionCreate,
  spmCreateSpmEndpoint,
  spmCreateSpmFindingDecision,
  spmGetSpmEndpoint,
  spmListSpmAssets,
  spmListSpmControls,
  spmListSpmEndpoints,
  spmListSpmFindings,
} from "@/client"

const SPM_REFRESH_MS = 10_000

/**
 * Fetch the first page of SPM endpoints for the operator UI.
 */
export function useSpmEndpoints(limit = 100) {
  return useQuery({
    queryKey: ["spm", "endpoints", { limit }],
    queryFn: () => spmListSpmEndpoints({ limit }),
    refetchInterval: SPM_REFRESH_MS,
    staleTime: 2_000,
  })
}

/**
 * Fetch a single SPM endpoint.
 */
export function useSpmEndpoint(endpointId: string | null) {
  return useQuery({
    queryKey: ["spm", "endpoint", endpointId],
    queryFn: () => {
      if (!endpointId) {
        throw new Error("Missing endpoint ID")
      }
      return spmGetSpmEndpoint({ endpointId })
    },
    enabled: Boolean(endpointId),
    refetchInterval: SPM_REFRESH_MS,
    staleTime: 2_000,
  })
}

/**
 * Fetch the first page of SPM findings.
 */
export function useSpmFindings(limit = 100) {
  return useQuery({
    queryKey: ["spm", "findings", { limit }],
    queryFn: () => spmListSpmFindings({ limit }),
    refetchInterval: SPM_REFRESH_MS,
    staleTime: 2_000,
  })
}

/**
 * Fetch the first page of SPM assets.
 */
export function useSpmAssets(limit = 100) {
  return useQuery({
    queryKey: ["spm", "assets", { limit }],
    queryFn: () => spmListSpmAssets({ limit }),
    refetchInterval: SPM_REFRESH_MS,
    staleTime: 2_000,
  })
}

/**
 * Fetch the static SPM controls catalog.
 */
export function useSpmControls() {
  return useQuery({
    queryKey: ["spm", "controls"],
    queryFn: () => spmListSpmControls(),
    staleTime: 60_000,
  })
}

/**
 * Mutations used across SPM operator pages.
 */
export function useSpmActions() {
  const queryClient = useQueryClient()

  const invalidate = async () => {
    await queryClient.invalidateQueries({ queryKey: ["spm"] })
  }

  const createEndpoint = useMutation({
    mutationFn: (requestBody: SpmEndpointCreate) =>
      spmCreateSpmEndpoint({ requestBody }),
    onSuccess: invalidate,
  })

  const decideFinding = useMutation({
    mutationFn: (params: {
      findingId: string
      requestBody: SpmFindingDecisionCreate
    }) =>
      spmCreateSpmFindingDecision({
        findingId: params.findingId,
        requestBody: params.requestBody,
      }),
    onSuccess: invalidate,
  })

  return {
    createEndpoint,
    decideFinding,
  }
}
