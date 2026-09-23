"use client"

import { type ReactNode, useState } from "react"
import type { ScimActivationReviewRead, ScimConnectionRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { OrgSettingsScimConnection } from "@/components/organization/org-settings-scim-connection"
import {
  OrgSettingsScimMappings,
  type ScimMappingDraft,
} from "@/components/organization/org-settings-scim-mappings"
import { ScimReviewDialog } from "@/components/organization/scim-review-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  useScimActivation,
  useScimConnection,
  useScimDirectorySummary,
  useScimMappings,
} from "@/hooks/use-scim"
import { formatRelative } from "@/lib/time"
import { cn } from "@/lib/utils"

const DEFAULT_DESCRIPTION =
  "Provision users and groups from your identity provider."

type ConnectionState = "pending" | "active" | "disabled" | "revoked"

function connectionState(connection: ScimConnectionRead): ConnectionState {
  if (connection.revoked_at) return "revoked"
  return connection.status
}

const STATE_COPY: Record<
  ConnectionState,
  { label: string; dot: string; description: string }
> = {
  pending: {
    label: "Pending activation",
    dot: "bg-muted-foreground",
    description:
      "Your identity provider is sending users and groups. Nobody gets access until you activate.",
  },
  active: {
    label: "Active",
    dot: "bg-green-500",
    description: "Your identity provider manages membership of mapped groups.",
  },
  disabled: {
    label: "Disabled",
    dot: "bg-muted-foreground",
    description: "Provisioning is disabled.",
  },
  revoked: {
    label: "Token revoked",
    dot: "bg-rose-500",
    description: "Rotate the token to resume provisioning.",
  },
}

/** Settings page header: title, optional status badge, and one action. */
export function ScimPageHeader({
  badge,
  description = DEFAULT_DESCRIPTION,
  action,
}: {
  badge?: ReactNode
  description?: string
  action?: ReactNode
}) {
  return (
    <div className="flex w-full items-start gap-6">
      <div className="flex-1 items-start space-y-3 text-left">
        <div className="flex items-center gap-3">
          <h2 className="text-2xl font-semibold tracking-tight">
            SCIM provisioning
          </h2>
          {badge}
        </div>
        <p className="text-base text-muted-foreground">{description}</p>
      </div>
      {action ? (
        <div className="flex shrink-0 items-start">{action}</div>
      ) : null}
    </div>
  )
}

function StatusBadge({ state }: { state: ConnectionState }) {
  const copy = STATE_COPY[state]
  return (
    <Badge
      variant="outline"
      className="gap-1.5 font-normal text-muted-foreground"
    >
      <span className={cn("size-1.5 rounded-full", copy.dot)} />
      {copy.label}
    </Badge>
  )
}

function StatTile({
  label,
  value,
  detail,
}: {
  label: string
  value: string
  detail: string
}) {
  return (
    <div className="space-y-1 rounded-lg border p-4">
      <p className="text-sm text-muted-foreground">{label}</p>
      <p className="text-2xl font-semibold tracking-tight">{value}</p>
      <p className="text-sm text-muted-foreground">{detail}</p>
    </div>
  )
}

/** What the provider has pushed, so admins can confirm the IdP is connected. */
function ScimDirectoryStats({
  connection,
}: {
  connection: ScimConnectionRead
}) {
  const { directorySummary } = useScimDirectorySummary({ enabled: true })
  const users = directorySummary?.users
  const groups = directorySummary?.groups
  return (
    <div className="grid grid-cols-3 gap-3">
      <StatTile
        label="Users pushed"
        value={users ? String(users.total) : "—"}
        detail={
          users ? `${users.active} active · ${users.inactive} inactive` : ""
        }
      />
      <StatTile
        label="Groups pushed"
        value={groups ? String(groups.total) : "—"}
        detail={groups ? `${groups.unmapped} not mapped` : ""}
      />
      <StatTile
        label="Last push"
        value={formatRelative(connection.last_used_at) ?? "Never"}
        detail={
          connection.last_used_at
            ? new Date(connection.last_used_at).toLocaleString()
            : "No requests from your identity provider yet"
        }
      />
    </div>
  )
}

type Review = { review: ScimActivationReviewRead; activation: boolean }

/**
 * SCIM settings: connection, directory stats, group mappings, and activation.
 *
 * Owns draft mappings and the review dialog so the header action and the
 * mapping table share one activation flow.
 */
export function OrgSettingsScim() {
  const canManage = useScopeCheck("org:scim:manage") === true
  const { connection, connectionIsLoading, connectionError } =
    useScimConnection()
  const { review, activate } = useScimActivation()
  const { createMapping, createMappingIsPending } = useScimMappings()
  const [drafts, setDrafts] = useState<ScimMappingDraft[]>([])
  const [pendingReview, setPendingReview] = useState<Review | null>(null)

  if (connectionIsLoading) {
    return (
      <>
        <ScimPageHeader />
        <CenteredSpinner />
      </>
    )
  }

  if (connectionError?.status === 403) {
    return (
      <>
        <ScimPageHeader />
        <EntitlementRequiredEmptyState
          title="You lack permission"
          description="You need permission to manage SCIM provisioning."
        />
      </>
    )
  }

  const state = connection ? connectionState(connection) : null
  const reviewIsPending = review.isPending || activate.isPending

  function openReview(proposed: ScimMappingDraft[], activation: boolean) {
    void review
      .mutateAsync(
        proposed.map((item) => ({
          external_group_id: item.external_group_id,
          group_id: item.group_id,
        }))
      )
      .then((result) => setPendingReview({ review: result, activation }))
      .catch(() => {})
  }

  function handleAdd(draft: ScimMappingDraft) {
    if (state !== "pending") {
      openReview([draft], false)
      return
    }
    setDrafts((current) =>
      current.some(
        (item) =>
          item.external_group_id === draft.external_group_id &&
          item.group_id === draft.group_id
      )
        ? current
        : [...current, draft]
    )
  }

  async function confirmReview() {
    if (!pendingReview) return
    // Apply exactly what was reviewed, even if the table changed meanwhile.
    const proposed = pendingReview.review.plans.map((plan) => ({
      external_group_id: plan.external_group_id,
      group_id: plan.group_id,
    }))
    if (pendingReview.activation) {
      await activate.mutateAsync(proposed)
      setDrafts([])
    } else {
      const mapping = proposed[0]
      if (!mapping) return
      await createMapping({
        externalGroupId: mapping.external_group_id,
        groupId: mapping.group_id,
      })
    }
    setPendingReview(null)
  }

  return (
    <>
      <ScimPageHeader
        badge={state ? <StatusBadge state={state} /> : undefined}
        description={state ? STATE_COPY[state].description : undefined}
        action={
          state === "pending" ? (
            <Button
              disabled={!canManage || reviewIsPending}
              onClick={() => openReview(drafts, true)}
            >
              Review and activate
            </Button>
          ) : undefined
        }
      />
      <div className="space-y-6">
        <OrgSettingsScimConnection />
        {connection && !connectionError && (
          <ScimDirectoryStats connection={connection} />
        )}
      </div>
      {!connectionError && (
        <OrgSettingsScimMappings
          connected={Boolean(connection)}
          status={connection?.status}
          revoked={Boolean(connection?.revoked_at)}
          drafts={drafts}
          reviewIsPending={reviewIsPending}
          onAdd={handleAdd}
          onRemoveDraft={(draft) =>
            setDrafts((current) =>
              current.filter(
                (item) =>
                  item.external_group_id !== draft.external_group_id ||
                  item.group_id !== draft.group_id
              )
            )
          }
          onDiscardDrafts={() => setDrafts([])}
        />
      )}
      {pendingReview && (
        <ScimReviewDialog
          review={pendingReview.review}
          activation={pendingReview.activation}
          pending={activate.isPending || createMappingIsPending}
          onClose={() => setPendingReview(null)}
          onConfirm={confirmReview}
        />
      )}
    </>
  )
}
