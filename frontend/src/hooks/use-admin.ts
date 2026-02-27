"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useMemo } from "react"
import {
  type AdminCreateOrganizationDomainResponse,
  type AdminCreateOrganizationResponse,
  type AdminCreateTierResponse,
  type AdminCreateUserResponse,
  type AdminListOrganizationDomainsResponse,
  type AdminListOrganizationsResponse,
  type AdminListTiersResponse,
  type AdminListUsersResponse,
  type AdminRegistryGetRegistryStatusResponse,
  type AdminRegistryListRegistryVersionsResponse,
  type AdminUserCreate,
  type AdminUserRead,
  adminCreateOrganization,
  adminCreateOrganizationDomain,
  adminCreateTier,
  adminCreateUser,
  adminDeleteOrganizationDomain,
  adminDeleteTier,
  adminDeleteUser,
  adminDemoteFromSuperuser,
  adminGetOrganization,
  adminGetOrgTier,
  adminGetRegistrySettings,
  adminGetTier,
  adminListOrganizationDomains,
  adminListOrganizations,
  adminListOrgRepositories,
  adminListOrgRepositoryVersions,
  adminListTiers,
  adminListUsers,
  adminPromoteOrgRepositoryVersion,
  adminPromoteToSuperuser,
  adminRegistryGetRegistryStatus,
  adminRegistryListRegistryVersions,
  adminRegistryPromoteRegistryVersion,
  adminRegistrySyncAllRepositories,
  adminRegistrySyncRepository,
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
import {
  adminDeleteOrganizationWithConfirmation,
  adminListOrgTiers,
} from "@/client/services.custom"

/* ── ORGANIZATIONS ─────────────────────────────────────────────────────────── */

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
        adminDeleteOrganizationWithConfirmation({
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

  const { mutateAsync: deleteUser, isPending: deletePending } = useMutation<
    void,
    Error,
    string
  >({
    mutationFn: (userId) => adminDeleteUser({ userId }),
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
    deleteUser,
    deletePending,
  }
}

/* ── TIERS ─────────────────────────────────────────────────────────────────── */

export function useAdminTiers(includeInactive = true) {
  const queryClient = useQueryClient()

  const {
    data: tiers,
    isLoading,
    error,
  } = useQuery<AdminListTiersResponse>({
    queryKey: ["admin", "tiers", { includeInactive }],
    queryFn: () => adminListTiers({ includeInactive }),
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

export function useAdminRegistryStatus() {
  const {
    data: status,
    isLoading,
    error,
    refetch,
  } = useQuery<AdminRegistryGetRegistryStatusResponse>({
    queryKey: ["admin", "registry", "status"],
    queryFn: adminRegistryGetRegistryStatus,
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
