"use client"

import { ArrowRightIcon, Loader2, Trash2Icon, UsersIcon } from "lucide-react"
import { useState } from "react"
import type {
  ExternalGroupMappingRead,
  ScimActivationReviewRead,
  ScimConnectionStatus,
} from "@/client"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"
import { CenteredSpinner } from "@/components/loading/spinner"
import { ScimReviewDialog } from "@/components/organization/scim-review-dialog"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  useScimActivation,
  useScimExternalGroups,
  useScimMappings,
} from "@/hooks/use-scim"
import { useRbacGroups } from "@/lib/hooks"

/**
 * Map synced IdP groups onto Tracecat groups.
 *
 * Without a mapping the projection has no rule to apply, so an IdP can push
 * users without granting them any access. `connected` reflects whether a SCIM
 * token exists; the editor explains that it stays inert until it does.
 */
export function OrgSettingsScimMappings({
  connected,
  status,
  revoked,
}: {
  connected: boolean
  status?: ScimConnectionStatus
  revoked: boolean
}) {
  const { review, activate } = useScimActivation()
  const [drafts, setDrafts] = useState<ExternalGroupMappingRead[]>([])
  const [reviewActivation, setReviewActivation] = useState(false)
  const [preview, setPreview] = useState<ScimActivationReviewRead | null>(null)
  const [removing, setRemoving] = useState<ExternalGroupMappingRead | null>(
    null
  )
  const isPending = status === "pending"
  const canEdit = !revoked && (isPending || status === "active")

  const { externalGroups, externalGroupsIsLoading, externalGroupsError } =
    useScimExternalGroups()
  const {
    mappings,
    mappingsIsLoading,
    mappingsError,
    createMapping,
    createMappingIsPending,
    deleteMapping,
    deleteMappingIsPending,
  } = useScimMappings()
  const {
    groups,
    isLoading: groupsIsLoading,
    error: groupsError,
  } = useRbacGroups()

  const [externalGroupId, setExternalGroupId] = useState<string>("")
  const [groupId, setGroupId] = useState<string>("")

  async function handleCreate() {
    if (!externalGroupId || !groupId) {
      return
    }
    if (isPending) {
      const source = externalGroups?.find(
        (group) => group.id === externalGroupId
      )
      const target = groups?.find((group) => group.id === groupId)
      if (!source || !target) return
      if (
        !drafts.some(
          (item) =>
            item.external_group_id === externalGroupId &&
            item.group_id === groupId
        )
      ) {
        setDrafts([
          ...drafts,
          {
            id: `${externalGroupId}-${groupId}`,
            external_group_id: externalGroupId,
            group_id: groupId,
            external_group_external_id: source.external_id,
            external_group_display_name: source.display_name,
            group_name: target.name,
          },
        ])
      }
      setExternalGroupId("")
      setGroupId("")
      return
    }
    setReviewActivation(false)
    setPreview(
      await review.mutateAsync([
        { external_group_id: externalGroupId, group_id: groupId },
      ])
    )
  }

  async function confirmReview() {
    if (!preview) return
    const proposed = preview.plans.map((plan) => ({
      external_group_id: plan.external_group_id,
      group_id: plan.group_id,
    }))
    if (reviewActivation) {
      await activate.mutateAsync(proposed)
      setDrafts([])
    } else {
      const mapping = proposed[0]
      if (!mapping) return
      await createMapping({
        externalGroupId: mapping.external_group_id,
        groupId: mapping.group_id,
      })
      setExternalGroupId("")
      setGroupId("")
    }
    setPreview(null)
  }

  const header = (
    <div className="space-y-1">
      <h3 className="text-lg font-medium">Group mappings</h3>
      <p className="text-sm text-muted-foreground">
        Grant group access to active, admitted identity provider users. Pending
        mappings are drafts until activation.
      </p>
    </div>
  )

  if (!connected) {
    return (
      <div className="space-y-4">
        {header}
        <Empty className="gap-4 rounded-lg border py-12">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <UsersIcon className="size-6" />
            </EmptyMedia>
            <EmptyTitle>Connect SCIM first</EmptyTitle>
            <EmptyDescription>
              Generate a connection token above, then configure Tracecat in your
              identity provider. Groups you push appear here and can be mapped.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    )
  }

  if (externalGroupsIsLoading || mappingsIsLoading || groupsIsLoading) {
    return (
      <div className="space-y-4">
        {header}
        <CenteredSpinner />
      </div>
    )
  }

  if (externalGroupsError || mappingsError || groupsError) {
    return (
      <p role="alert">Unable to load group mappings. Reload to try again.</p>
    )
  }

  const availableExternalGroups = externalGroups ?? []
  const availableGroups = groups ?? []
  const existingMappings = isPending ? drafts : (mappings ?? [])

  return (
    <div className="space-y-4">
      {header}
      {revoked && (
        <p>Rotate the revoked token before changing SCIM configuration.</p>
      )}
      {isPending && (
        <div className="space-y-2">
          <p className="text-sm">
            Directory pushes do not admit users until activation. Add optional
            draft mappings, then review.
          </p>
          <Button
            disabled={!canEdit || review.isPending || activate.isPending}
            onClick={() => {
              setReviewActivation(true)
              void review
                .mutateAsync(
                  drafts.map((item) => ({
                    external_group_id: item.external_group_id,
                    group_id: item.group_id,
                  }))
                )
                .then(setPreview)
                .catch(() => {})
            }}
          >
            Review activation
          </Button>
        </div>
      )}
      {preview && (
        <ScimReviewDialog
          review={preview}
          activation={reviewActivation}
          pending={activate.isPending || createMappingIsPending}
          onClose={() => setPreview(null)}
          onConfirm={confirmReview}
        />
      )}
      {removing && (
        <ConfirmDestructiveDialog
          open
          onOpenChange={(open) => {
            if (!open) setRemoving(null)
          }}
          confirmPhrase={removing.group_name}
          title="Remove mapping"
          confirmLabel="Remove mapping"
          isPending={deleteMappingIsPending}
          description={
            (mappings ?? []).filter(
              (item) => item.group_id === removing.group_id
            ).length === 1
              ? "This is the final mapping. Current active, admitted IdP members will be retained as manual members. Their group access will remain."
              : "Other mappings remain. Members supplied only by this mapping will lose this group path; other grants are preserved."
          }
          onConfirm={async () => {
            await deleteMapping(removing.id)
            setRemoving(null)
          }}
        />
      )}

      {availableExternalGroups.length === 0 ? (
        <Empty className="gap-4 rounded-lg border py-12">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <UsersIcon className="size-6" />
            </EmptyMedia>
            <EmptyTitle>No synced groups yet</EmptyTitle>
            <EmptyDescription>
              Your identity provider has not pushed any groups. Finish assigning
              groups to the Tracecat application in your IdP — they appear here
              once it sends them.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : (
        <div className="space-y-6 rounded-lg border p-6">
          <div className="flex flex-wrap items-end gap-3">
            <div className="min-w-[220px] flex-1 space-y-2">
              <span className="text-sm text-muted-foreground">
                Identity provider group
              </span>
              <Select
                value={externalGroupId}
                onValueChange={setExternalGroupId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a synced group" />
                </SelectTrigger>
                <SelectContent>
                  {availableExternalGroups.map((externalGroup) => (
                    <SelectItem key={externalGroup.id} value={externalGroup.id}>
                      {externalGroup.display_name} ({externalGroup.member_count}{" "}
                      members)
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <div className="min-w-[220px] flex-1 space-y-2">
              <span className="text-sm text-muted-foreground">
                Tracecat group
              </span>
              <Select value={groupId} onValueChange={setGroupId}>
                <SelectTrigger>
                  <SelectValue placeholder="Select a Tracecat group" />
                </SelectTrigger>
                <SelectContent>
                  {availableGroups.map((group) => (
                    <SelectItem key={group.id} value={group.id}>
                      {group.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>

            <Button
              onClick={() => {
                void handleCreate().catch(() => {})
              }}
              disabled={
                !canEdit ||
                !externalGroupId ||
                !groupId ||
                createMappingIsPending ||
                review.isPending
              }
            >
              {createMappingIsPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : null}
              {isPending ? "Add draft mapping" : "Review mapping"}
            </Button>
          </div>

          {existingMappings.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No mappings yet. Admitted users retain their existing grants and
              organization membership.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Identity provider group</TableHead>
                  <TableHead>Tracecat group</TableHead>
                  <TableHead className="w-16" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {existingMappings.map((mapping) => (
                  <TableRow key={mapping.id}>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span>{mapping.external_group_display_name}</span>
                        <ArrowRightIcon className="size-3 text-muted-foreground" />
                      </div>
                    </TableCell>
                    <TableCell>{mapping.group_name}</TableCell>
                    <TableCell>
                      <Button
                        variant="ghost"
                        size="icon"
                        disabled={
                          !canEdit || deleteMappingIsPending || review.isPending
                        }
                        onClick={() => {
                          if (isPending)
                            setDrafts(
                              drafts.filter((item) => item.id !== mapping.id)
                            )
                          else setRemoving(mapping)
                        }}
                        aria-label={`Remove mapping for ${mapping.external_group_display_name}`}
                      >
                        <Trash2Icon className="size-4 text-muted-foreground" />
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </div>
      )}
    </div>
  )
}
