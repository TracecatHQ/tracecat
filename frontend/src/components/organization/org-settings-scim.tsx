"use client"

import { type ReactNode, useState } from "react"
import type {
  ExternalGroupMappingRead,
  ScimActivationReviewRead,
  ScimConnectionRead,
  ScimReviewRequest,
} from "@/client"
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
  useScimMappings,
} from "@/hooks/use-scim"
import { cn } from "@/lib/utils"

const DESCRIPTION = "Sync users and groups from your identity provider."

type ConnectionState = "pending" | "active" | "disabled" | "revoked"

function connectionState(connection: ScimConnectionRead): ConnectionState {
  if (connection.status === "disabled") return "disabled"
  if (connection.revoked_at) return "revoked"
  return connection.status
}

const STATE_COPY: Record<ConnectionState, { label: string; dot: string }> = {
  pending: {
    label: "Pending activation",
    dot: "bg-muted-foreground",
  },
  active: {
    label: "Active",
    dot: "bg-green-500",
  },
  disabled: {
    label: "Disconnected",
    dot: "bg-muted-foreground",
  },
  revoked: {
    label: "Token revoked",
    dot: "bg-rose-500",
  },
}

/** Settings page header: title, optional status badge, and one action. */
export function ScimPageHeader({
  badge,
  action,
}: {
  badge?: ReactNode
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
        <p className="text-base text-muted-foreground">{DESCRIPTION}</p>
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

type Review = {
  review: ScimActivationReviewRead
  activation: boolean
  request: ScimReviewRequest
}

function sameTarget(a: ScimMappingDraft, b: ScimMappingDraft): boolean {
  return (
    a.external_group_id === b.external_group_id && a.group_id === b.group_id
  )
}

/**
 * SCIM settings: connection, directory stats, group mappings, and activation.
 *
 * Owns draft additions and removals so every mapping change is reviewed and
 * applied together, before and after activation.
 */
export function OrgSettingsScim() {
  const canManage = useScopeCheck("org:scim:manage") === true
  const { connection, connectionIsLoading, connectionError } =
    useScimConnection()
  const { review, activate } = useScimActivation()
  const { applyMappingChanges, applyMappingChangesIsPending } =
    useScimMappings()
  const [drafts, setDrafts] = useState<ScimMappingDraft[]>([])
  const [removals, setRemovals] = useState<ExternalGroupMappingRead[]>([])
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

  function openReview(activation: boolean) {
    const request: ScimReviewRequest = {
      mappings: drafts.map((item) => ({
        external_group_id: item.external_group_id,
        group_id: item.group_id,
      })),
      delete: activation ? [] : removals.map((mapping) => mapping.id),
    }
    void review
      .mutateAsync(request)
      .then((result) =>
        setPendingReview({ review: result, activation, request })
      )
      .catch(() => {})
  }

  function handleAdd(draft: ScimMappingDraft) {
    if (removals.some((mapping) => sameTarget(mapping, draft))) {
      setRemovals((current) =>
        current.filter((mapping) => !sameTarget(mapping, draft))
      )
      return
    }
    setDrafts((current) =>
      current.some((item) => sameTarget(item, draft))
        ? current
        : [...current, draft]
    )
  }

  function handleRemove(
    draft: ScimMappingDraft,
    mapping?: ExternalGroupMappingRead
  ) {
    if (mapping) {
      setRemovals((current) => [...current, mapping])
      return
    }
    setDrafts((current) => current.filter((item) => !sameTarget(item, draft)))
  }

  async function confirmReview() {
    if (!pendingReview) return
    // Apply exactly what was reviewed, even if the table changed meanwhile.
    const { mappings = [], delete: removed = [] } = pendingReview.request
    if (pendingReview.activation) {
      await activate.mutateAsync(mappings)
    } else {
      await applyMappingChanges({ create: mappings, delete: removed })
    }
    setDrafts([])
    setRemovals([])
    setPendingReview(null)
  }

  return (
    <>
      <ScimPageHeader
        badge={state ? <StatusBadge state={state} /> : undefined}
        action={
          state === "pending" ? (
            <Button
              disabled={!canManage || reviewIsPending}
              onClick={() => openReview(true)}
            >
              Review and activate
            </Button>
          ) : undefined
        }
      />
      <OrgSettingsScimConnection
        onDisconnect={() => {
          // Drafts target the detached directory; keep none for the next one.
          setDrafts([])
          setRemovals([])
        }}
      />
      {!connectionError && (
        <OrgSettingsScimMappings
          connected={Boolean(connection)}
          status={connection?.status}
          revoked={Boolean(connection?.revoked_at)}
          drafts={drafts}
          removals={removals}
          reviewIsPending={reviewIsPending}
          onAdd={handleAdd}
          onRemove={handleRemove}
          onDiscardDrafts={() => {
            setDrafts([])
            setRemovals([])
          }}
          onReviewChanges={() => openReview(false)}
        />
      )}
      {pendingReview && (
        <ScimReviewDialog
          review={pendingReview.review}
          activation={pendingReview.activation}
          pending={activate.isPending || applyMappingChangesIsPending}
          onClose={() => setPendingReview(null)}
          onConfirm={confirmReview}
        />
      )}
    </>
  )
}
