"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  type SpmCreateSpmEndpointData,
  type SpmCreateSpmFindingDecisionData,
  type SpmDeleteSpmEndpointData,
  type SpmListSpmAssetsData,
  type SpmListSpmFindingsData,
  spmCreateSpmEndpoint,
  spmCreateSpmFindingDecision,
  spmDeleteSpmEndpoint,
  spmListSpmAssets,
  spmListSpmControls,
  spmListSpmEndpoints,
  spmListSpmFindings,
} from "@/client"

export const SPM_REFRESH_MS = 10_000

export interface UseSpmAssetsParams {
  assetClass?: SpmListSpmAssetsData["assetClass"]
  assetType?: SpmListSpmAssetsData["assetType"]
  cursor?: SpmListSpmAssetsData["cursor"]
  endpointId?: SpmListSpmAssetsData["endpointId"]
  harness?: SpmListSpmAssetsData["harness"]
  limit?: SpmListSpmAssetsData["limit"]
}

export interface UseSpmFindingsParams {
  controlId?: SpmListSpmFindingsData["controlId"]
  cursor?: SpmListSpmFindingsData["cursor"]
  endpointId?: SpmListSpmFindingsData["endpointId"]
  limit?: SpmListSpmFindingsData["limit"]
}

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
 * Fetch SPM findings with optional filters.
 */
export function useSpmFindings(params: UseSpmFindingsParams = {}) {
  const { controlId, cursor, endpointId, limit = 100 } = params
  return useQuery({
    queryKey: ["spm", "findings", { controlId, cursor, endpointId, limit }],
    queryFn: () =>
      spmListSpmFindings({
        controlId,
        cursor,
        endpointId,
        limit,
      }),
    refetchInterval: SPM_REFRESH_MS,
    staleTime: 2_000,
  })
}

/**
 * Fetch SPM assets with optional filters.
 */
export function useSpmAssets(params: UseSpmAssetsParams = {}) {
  const {
    assetClass,
    assetType,
    cursor,
    endpointId,
    harness,
    limit = 100,
  } = params
  return useQuery({
    queryKey: [
      "spm",
      "assets",
      { assetClass, assetType, cursor, endpointId, harness, limit },
    ],
    queryFn: () =>
      spmListSpmAssets({
        assetClass,
        assetType,
        cursor,
        endpointId,
        harness,
        limit,
      }),
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
    mutationFn: (requestBody: SpmCreateSpmEndpointData["requestBody"]) =>
      spmCreateSpmEndpoint({ requestBody }),
    onSuccess: invalidate,
  })

  const deleteEndpoint = useMutation({
    mutationFn: (params: SpmDeleteSpmEndpointData) =>
      spmDeleteSpmEndpoint(params),
    onSuccess: invalidate,
  })

  const decideFinding = useMutation({
    mutationFn: (params: SpmCreateSpmFindingDecisionData) =>
      spmCreateSpmFindingDecision(params),
    onSuccess: invalidate,
  })

  return {
    createEndpoint,
    deleteEndpoint,
    decideFinding,
  }
}
