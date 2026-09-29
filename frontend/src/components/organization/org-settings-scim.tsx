"use client"

import { useState } from "react"
import type {
  ExternalGroupMappingRead,
  ScimActivationReviewRead,
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
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import {
  useScimActivation,
  useScimConnection,
  useScimMappings,
} from "@/hooks/use-scim"

const DESCRIPTION = "Sync users and groups from your identity provider."
// The review and apply endpoints each accept at most this many changes.
const MAX_MAPPING_CHANGES = 100

function warnChangeLimit() {
  toast({
    title: "Too many pending changes",
    description: `Review up to ${MAX_MAPPING_CHANGES} additions and ${MAX_MAPPING_CHANGES} removals at a time.`,
  })
}

/** Settings page header: title and description. */
export function ScimPageHeader() {
  return (
    <div className="flex w-full items-start">
      <div className="items-start space-y-3 text-left">
        <h2 className="text-2xl font-semibold tracking-tight">
          SCIM provisioning
        </h2>
        <p className="text-base text-muted-foreground">{DESCRIPTION}</p>
      </div>
    </div>
  )
}

type Review = {
  review: ScimActivationReviewRead
  activation: boolean
  request: ScimReviewRequest
  complete: boolean
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
        setPendingReview({
          review: result,
          activation,
          request,
          complete: false,
        })
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
    if (drafts.some((item) => sameTarget(item, draft))) return
    if (drafts.length >= MAX_MAPPING_CHANGES) {
      warnChangeLimit()
      return
    }
    setDrafts((current) => [...current, draft])
  }

  function handleRemove(
    draft: ScimMappingDraft,
    mapping?: ExternalGroupMappingRead
  ) {
    if (mapping) {
      if (removals.length >= MAX_MAPPING_CHANGES) {
        warnChangeLimit()
        return
      }
      setRemovals((current) => [...current, mapping])
      return
    }
    setDrafts((current) => current.filter((item) => !sameTarget(item, draft)))
  }

  function loadFullReview() {
    if (!pendingReview || pendingReview.complete || review.isPending) return
    const { request } = pendingReview
    void review
      .mutateAsync({ ...request, full: true })
      .then((result) =>
        setPendingReview((current) =>
          // Ignore a late reply for a review that was closed or replaced.
          current?.request === request
            ? { ...current, review: result, complete: true }
            : current
        )
      )
      .catch(() => {})
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
      <ScimPageHeader />
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
      {connection?.status === "pending" && !connection.revoked_at && (
        <div className="flex justify-end">
          <Button
            disabled={!canManage || reviewIsPending}
            onClick={() => openReview(true)}
          >
            Review and activate
          </Button>
        </div>
      )}
      {pendingReview && (
        <ScimReviewDialog
          review={pendingReview.review}
          activation={pendingReview.activation}
          complete={pendingReview.complete}
          loadingAll={review.isPending}
          pushedAt={connection?.last_used_at}
          pending={activate.isPending || applyMappingChangesIsPending}
          onShowAll={loadFullReview}
          onClose={() => setPendingReview(null)}
          onConfirm={confirmReview}
        />
      )}
    </>
  )
}
