"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useMemo, useState } from "react"
import {
  type AdminCreateOrganizationDomainResponse,
  type AdminCreateOrganizationInvitationResponse,
  type AdminCreateOrganizationResponse,
  type AdminCreateTierResponse,
  type AdminCreateUserResponse,
  type AdminListOrganizationDomainsResponse,
  type AdminListOrganizationInvitationsResponse,
  type AdminListOrganizationsResponse,
  type AdminListTiersResponse,
  type AdminListUsersResponse,
  type AdminOrgInvitationCreate,
  type AdminRegistryGetRegistryStatusResponse,
  type AdminRegistryListRegistryVersionsResponse,
  type AdminUserCreate,
  type AdminUserRead,
  adminCreateOrganization,
  adminCreateOrganizationDomain,
  adminCreateOrganizationInvitation,
  adminCreateTier,
  adminCreateUser,
  adminDeleteOrganization,
  adminDeleteOrganizationDomain,
  adminDeleteTier,
  adminDemoteFromSuperuser,
  adminGetOrganization,
  adminGetOrganizationInvitationToken,
  adminGetOrgTier,
  adminGetRegistrySettings,
  adminGetTier,
  adminListOrganizationDomains,
  adminListOrganizationInvitations,
  adminListOrganizations,
  adminListOrgRepositories,
  adminListOrgRepositoryVersions,
  adminListOrgTiers,
  adminListTiers,
  adminListUsers,
  adminPromoteOrgRepositoryVersion,
  adminPromoteToSuperuser,
  adminRegistryGetRegistryStatus,
  adminRegistryListRegistryVersions,
  adminRegistryPromoteRegistryVersion,
  adminRegistrySyncAllRepositories,
  adminRegistrySyncRepository,
  adminRevokeOrganizationInvitation,
  adminSyncOrgRepository,
  adminUpdateOrganization,
  adminUpdateOrganizationDomain,
  adminUpdateOrgTier,
  adminUpdateRegistrySettings,
  adminUpdateTier,
  type OrganizationTierRead,
  type OrganizationTierUpdate,
  type OrgCreate,
  type OrgDomainCreate,
  type tracecat_ee__admin__organizations__schemas__OrgDomainRead as OrgDomainRead,
  type OrgDomainUpdate,
  type tracecat_ee__admin__organizations__schemas__OrgRead as OrgRead,
  type OrgUpdate,
  type PlatformRegistrySettingsUpdate,
  type TierCreate,
  type TierRead,
  type TierUpdate,
} from "@/client"

/* ── ORGANIZATIONS ─────────────────────────────────────────────────────────── */

const ADMIN_ORG_INVITATIONS_PAGE_SIZE = 20

interface AdminOrgInvitationsPaginationState {
  cursor: string | null
  reverse: boolean
  page: number
}

const DEFAULT_ADMIN_ORG_INVITATIONS_PAGINATION: AdminOrgInvitationsPaginationState =
  {
    cursor: null,
    reverse: false,
    page: 0,
  }

export function useAdminOrganizations({
  enabled = true,
}: {
  enabled?: boolean
} = {}) {
  const queryClient = useQueryClient()

  const {
    data: organizations,
    isLoading,
    error,
  } = useQuery<AdminListOrganizationsResponse>({
    queryKey: ["admin", "organizations"],
    queryFn: adminListOrganizations,
    enabled,
  })

  const { mutateAsync: createOrganization, isPending: createPending } =
    useMutation<AdminCreateOrganizationResponse, Error, OrgCreate>({
      mutationFn: (data) => adminCreateOrganization({ requestBody: data }),
      onSuccess: () =>
        queryClient.invalidateQueries({ queryKey: ["admin", "organizations"] }),
    })

  const { mutateAsync: updateOrganization, isPending: updatePending } =
    useMutation<OrgRead, Error, { orgId: string; data: OrgUpdate }>({
      mutationFn: ({ orgId, data }) =>
        adminUpdateOrganization({ orgId, requestBody: data }),
      onSuccess: () =>
        queryClient.invalidateQueries({ queryKey: ["admin", "organizations"] }),
    })

  const { mutateAsync: deleteOrganization, isPending: deletePending } =
    useMutation<void, Error, { orgId: string; confirmation: string }>({
      mutationFn: ({ orgId, confirmation }) =>
        adminDeleteOrganization({
          orgId,
          confirm: confirmation,
        }),
      onSuccess: () =>
        queryClient.invalidateQueries({ queryKey: ["admin", "organizations"] }),
    })

  return {
    organizations,
    isLoading,
    error,
    createOrganization,
    createPending,
    updateOrganization,
    updatePending,
    deleteOrganization,
    deletePending,
  }
}

export function useAdminOrganization(orgId: string) {
  const queryClient = useQueryClient()

  const {
    data: organization,
    isLoading,
    error,
  } = useQuery<OrgRead>({
    queryKey: ["admin", "organizations", orgId],
    queryFn: () => adminGetOrganization({ orgId }),
    enabled: !!orgId,
  })

  const { mutateAsync: updateOrganization, isPending: updatePending } =
    useMutation<OrgRead, Error, OrgUpdate>({
      mutationFn: (data) =>
        adminUpdateOrganization({ orgId, requestBody: data }),
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["admin", "organizations", orgId],
        })
        queryClient.invalidateQueries({ queryKey: ["admin", "organizations"] })
        queryClient.invalidateQueries({ queryKey: ["current-organization"] })
      },
    })

  return {
    organization,
    isLoading,
    error,
    updateOrganization,
    updatePending,
  }
}

export function useAdminOrgDomains(orgId: string) {
  const queryClient = useQueryClient()

  const {
    data: domains,
    isLoading,
    error,
  } = useQuery<AdminListOrganizationDomainsResponse>({
    queryKey: ["admin", "organizations", orgId, "domains"],
    queryFn: () => adminListOrganizationDomains({ orgId }),
    enabled: !!orgId,
  })

  const { mutateAsync: createDomain, isPending: createPending } = useMutation<
    AdminCreateOrganizationDomainResponse,
    Error,
    OrgDomainCreate
  >({
    mutationFn: (data) =>
      adminCreateOrganizationDomain({ orgId, requestBody: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["admin", "organizations", orgId, "domains"],
      })
    },
  })

  const { mutateAsync: updateDomain, isPending: updatePending } = useMutation<
    OrgDomainRead,
    Error,
    { domainId: string; data: OrgDomainUpdate }
  >({
    mutationFn: ({ domainId, data }) =>
      adminUpdateOrganizationDomain({ orgId, domainId, requestBody: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["admin", "organizations", orgId, "domains"],
      })
    },
  })

  const { mutateAsync: deleteDomain, isPending: deletePending } = useMutation<
    void,
    Error,
    string
  >({
    mutationFn: (domainId) =>
      adminDeleteOrganizationDomain({ orgId, domainId }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["admin", "organizations", orgId, "domains"],
      })
    },
  })

  return {
    domains,
    isLoading,
    error,
    createDomain,
    createPending,
    updateDomain,
    updatePending,
    deleteDomain,
    deletePending,
  }
}

/** Fetch and mutate platform-created organization invitations. */
export function useAdminOrgInvitations(orgId: string) {
  const queryClient = useQueryClient()
  const [pagination, setPagination] =
    useState<AdminOrgInvitationsPaginationState>(
      DEFAULT_ADMIN_ORG_INVITATIONS_PAGINATION
    )
  const queryKey = ["admin", "organizations", orgId, "invitations"]

  useEffect(() => {
    setPagination(DEFAULT_ADMIN_ORG_INVITATIONS_PAGINATION)
  }, [orgId])

  const {
    data: invitationsPage,
    isLoading,
    error,
  } = useQuery<AdminListOrganizationInvitationsResponse>({
    queryKey: [...queryKey, pagination.cursor, pagination.reverse],
    queryFn: () =>
      adminListOrganizationInvitations({
        orgId,
        limit: ADMIN_ORG_INVITATIONS_PAGE_SIZE,
        cursor: pagination.cursor,
        reverse: pagination.reverse,
      }),
    enabled: !!orgId,
  })

  const { mutateAsync: createInvitation, isPending: createPending } =
    useMutation<
      AdminCreateOrganizationInvitationResponse,
      Error,
      AdminOrgInvitationCreate
    >({
      mutationFn: (data) =>
        adminCreateOrganizationInvitation({ orgId, requestBody: data }),
      onSuccess: () => {
        setPagination(DEFAULT_ADMIN_ORG_INVITATIONS_PAGINATION)
        queryClient.invalidateQueries({ queryKey })
      },
    })

  const { mutateAsync: getInvitationToken } = useMutation<
    { token: string },
    Error,
    string
  >({
    mutationFn: (invitationId) =>
      adminGetOrganizationInvitationToken({ orgId, invitationId }),
  })

  const { mutateAsync: revokeInvitation, isPending: revokePending } =
    useMutation<void, Error, string>({
      mutationFn: (invitationId) =>
        adminRevokeOrganizationInvitation({ orgId, invitationId }),
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey })
      },
    })

  function goToNextPage() {
    if (!invitationsPage?.next_cursor) return
    setPagination((previous) => ({
      cursor: invitationsPage.next_cursor ?? null,
      reverse: false,
      page: previous.page + 1,
    }))
  }

  function goToPreviousPage() {
    if (!invitationsPage?.prev_cursor) return
    setPagination((previous) => ({
      cursor: invitationsPage.prev_cursor ?? null,
      reverse: true,
      page: Math.max(previous.page - 1, 0),
    }))
  }

  return {
    invitations: invitationsPage?.items ?? [],
    isLoading,
    error,
    createInvitation,
    createPending,
    getInvitationToken,
    revokeInvitation,
    revokePending,
    goToNextPage,
    goToPreviousPage,
    hasNextPage: invitationsPage?.has_more ?? false,
    hasPreviousPage: invitationsPage?.has_previous ?? false,
    currentPage: pagination.page,
  }
}

/* ── USERS ─────────────────────────────────────────────────────────────────── */

export function useAdminUsers() {
  const queryClient = useQueryClient()

  const {
    data: users,
    isLoading,
    error,
  } = useQuery<AdminListUsersResponse>({
    queryKey: ["admin", "users"],
    queryFn: adminListUsers,
  })

  const { mutateAsync: createUser, isPending: createPending } = useMutation<
    AdminCreateUserResponse,
    Error,
    AdminUserCreate
  >({
    mutationFn: (data) => adminCreateUser({ requestBody: data }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["admin", "users"] }),
  })

  const { mutateAsync: promoteToSuperuser, isPending: promotePending } =
    useMutation<AdminUserRead, Error, string>({
      mutationFn: (userId) => adminPromoteToSuperuser({ userId }),
      onSuccess: () =>
        queryClient.invalidateQueries({ queryKey: ["admin", "users"] }),
    })

  const { mutateAsync: demoteFromSuperuser, isPending: demotePending } =
    useMutation<AdminUserRead, Error, string>({
      mutationFn: (userId) => adminDemoteFromSuperuser({ userId }),
      onSuccess: () =>
        queryClient.invalidateQueries({ queryKey: ["admin", "users"] }),
    })

  return {
    users,
    isLoading,
    error,
    createUser,
    createPending,
    promoteToSuperuser,
    promotePending,
    demoteFromSuperuser,
    demotePending,
  }
}

/* ── TIERS ─────────────────────────────────────────────────────────────────── */

export function useAdminTiers(includeInactive = true, enabled = true) {
  const queryClient = useQueryClient()

  const {
    data: tiers,
    isLoading,
    error,
  } = useQuery<AdminListTiersResponse>({
    queryKey: ["admin", "tiers", { includeInactive }],
    queryFn: () => adminListTiers({ includeInactive }),
    enabled,
  })

  const { mutateAsync: createTier, isPending: createPending } = useMutation<
    AdminCreateTierResponse,
    Error,
    TierCreate
  >({
    mutationFn: (data) => adminCreateTier({ requestBody: data }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["admin", "tiers"] }),
  })

  const { mutateAsync: updateTier, isPending: updatePending } = useMutation<
    TierRead,
    Error,
    { tierId: string; data: TierUpdate }
  >({
    mutationFn: ({ tierId, data }) =>
      adminUpdateTier({ tierId, requestBody: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "tiers"] })
      queryClient.invalidateQueries({
        queryKey: ["admin", "organizations", "tiers"],
      })
    },
  })

  const { mutateAsync: deleteTier, isPending: deletePending } = useMutation<
    void,
    Error,
    string
  >({
    mutationFn: (tierId) => adminDeleteTier({ tierId }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: ["admin", "tiers"] }),
  })

  return {
    tiers,
    isLoading,
    error,
    createTier,
    createPending,
    updateTier,
    updatePending,
    deleteTier,
    deletePending,
  }
}

export function useAdminTier(tierId: string) {
  const queryClient = useQueryClient()

  const {
    data: tier,
    isLoading,
    error,
  } = useQuery<TierRead>({
    queryKey: ["admin", "tiers", tierId],
    queryFn: () => adminGetTier({ tierId }),
    enabled: !!tierId,
  })

  const { mutateAsync: updateTier, isPending: updatePending } = useMutation<
    TierRead,
    Error,
    TierUpdate
  >({
    mutationFn: (data) => adminUpdateTier({ tierId, requestBody: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "tiers", tierId] })
      queryClient.invalidateQueries({ queryKey: ["admin", "tiers"] })
      queryClient.invalidateQueries({
        queryKey: ["admin", "organizations", "tiers"],
      })
    },
  })

  return {
    tier,
    isLoading,
    error,
    updateTier,
    updatePending,
  }
}

/* ── ORG TIER ASSIGNMENT ───────────────────────────────────────────────────── */

export function useAdminOrgTier(orgId: string) {
  const queryClient = useQueryClient()

  const {
    data: orgTier,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["admin", "organizations", orgId, "tier"],
    queryFn: () => adminGetOrgTier({ orgId }),
    enabled: !!orgId,
  })

  const { mutateAsync: updateOrgTier, isPending: updatePending } = useMutation({
    mutationFn: (data: OrganizationTierUpdate) =>
      adminUpdateOrgTier({ orgId, requestBody: data }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["admin", "organizations", orgId, "tier"],
      })
      queryClient.invalidateQueries({
        queryKey: ["admin", "organizations", "tiers"],
      })
      queryClient.invalidateQueries({ queryKey: ["admin", "organizations"] })
    },
  })

  return {
    orgTier,
    isLoading,
    error,
    updateOrgTier,
    updatePending,
  }
}

export function useAdminOrgTiers(orgIds: string[]) {
  const {
    data: orgTiers,
    isLoading,
    error,
  } = useQuery<OrganizationTierRead[]>({
    queryKey: ["admin", "organizations", "tiers", orgIds],
    queryFn: () => adminListOrgTiers({ orgIds }),
    enabled: orgIds.length > 0,
  })

  const orgTiersByOrgId = useMemo(() => {
    const entries = orgTiers ?? []
    return new Map(entries.map((orgTier) => [orgTier.organization_id, orgTier]))
  }, [orgTiers])

  return {
    orgTiers: orgTiers ?? [],
    orgTiersByOrgId,
    isLoading,
    error,
  }
}

/* ── ORG REGISTRY ──────────────────────────────────────────────────────────── */

export function useAdminOrgRegistry(orgId: string) {
  const queryClient = useQueryClient()

  const {
    data: repositories,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["admin", "organizations", orgId, "registry"],
    queryFn: () => adminListOrgRepositories({ orgId }),
    enabled: !!orgId,
  })

  const { mutateAsync: syncRepository, isPending: syncPending } = useMutation({
    mutationFn: ({
      repositoryId,
      force,
    }: {
      repositoryId: string
      force?: boolean
    }) =>
      adminSyncOrgRepository({
        orgId,
        repositoryId,
        requestBody: { force: force ?? false },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["admin", "organizations", orgId, "registry"],
      })
    },
  })

  const { mutateAsync: promoteVersion, isPending: promotePending } =
    useMutation({
      mutationFn: ({
        repositoryId,
        versionId,
      }: {
        repositoryId: string
        versionId: string
      }) =>
        adminPromoteOrgRepositoryVersion({
          orgId,
          repositoryId,
          versionId,
        }),
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["admin", "organizations", orgId, "registry"],
        })
      },
    })

  return {
    repositories,
    isLoading,
    error,
    syncRepository,
    syncPending,
    promoteVersion,
    promotePending,
  }
}

export function useAdminOrgRepositoryVersions(
  orgId: string,
  repositoryId: string
) {
  const {
    data: versions,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["admin", "organizations", orgId, "registry", repositoryId],
    queryFn: () => adminListOrgRepositoryVersions({ orgId, repositoryId }),
    enabled: !!orgId && !!repositoryId,
  })

  return {
    versions,
    isLoading,
    error,
  }
}

/* ── PLATFORM REGISTRY ─────────────────────────────────────────────────────── */

export function useAdminRegistryStatus(options?: { enabled?: boolean }) {
  const {
    data: status,
    isLoading,
    error,
    refetch,
  } = useQuery<AdminRegistryGetRegistryStatusResponse>({
    queryKey: ["admin", "registry", "status"],
    queryFn: adminRegistryGetRegistryStatus,
    enabled: options?.enabled ?? true,
  })

  return {
    status,
    isLoading,
    error,
    refetch,
  }
}

export function useAdminRegistryVersions(
  repositoryId?: string,
  limit?: number
) {
  const {
    data: versions,
    isLoading,
    error,
  } = useQuery<AdminRegistryListRegistryVersionsResponse>({
    queryKey: ["admin", "registry", "versions", { repositoryId, limit }],
    queryFn: () => adminRegistryListRegistryVersions({ repositoryId, limit }),
  })

  return {
    versions,
    isLoading,
    error,
  }
}

export function useAdminRegistrySync() {
  const queryClient = useQueryClient()

  const { mutateAsync: syncAllRepositories, isPending: syncAllPending } =
    useMutation({
      mutationFn: (force?: boolean) =>
        adminRegistrySyncAllRepositories({ force }),
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["admin", "registry"] })
      },
    })

  const { mutateAsync: syncRepository, isPending: syncPending } = useMutation({
    mutationFn: ({
      repositoryId,
      force,
    }: {
      repositoryId: string
      force?: boolean
    }) => adminRegistrySyncRepository({ repositoryId, force }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["admin", "registry"] })
    },
  })

  const { mutateAsync: promoteVersion, isPending: promotePending } =
    useMutation({
      mutationFn: ({
        repositoryId,
        versionId,
      }: {
        repositoryId: string
        versionId: string
      }) => adminRegistryPromoteRegistryVersion({ repositoryId, versionId }),
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: ["admin", "registry"] })
      },
    })

  return {
    syncAllRepositories,
    syncAllPending,
    syncRepository,
    syncPending,
    promoteVersion,
    promotePending,
  }
}

export function useAdminRegistrySettings() {
  const queryClient = useQueryClient()

  const {
    data: settings,
    isLoading,
    error,
  } = useQuery({
    queryKey: ["admin", "registry", "settings"],
    queryFn: adminGetRegistrySettings,
  })

  const { mutateAsync: updateSettings, isPending: updatePending } = useMutation(
    {
      mutationFn: (data: PlatformRegistrySettingsUpdate) =>
        adminUpdateRegistrySettings({ requestBody: data }),
      onSuccess: () => {
        queryClient.invalidateQueries({
          queryKey: ["admin", "registry", "settings"],
        })
      },
    }
  )

  return {
    settings,
    isLoading,
    error,
    updateSettings,
    updatePending,
  }
}
