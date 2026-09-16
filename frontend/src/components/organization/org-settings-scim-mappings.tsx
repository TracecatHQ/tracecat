"use client"

import { ArrowRightIcon, Loader2, Trash2Icon, UsersIcon } from "lucide-react"
import { useState } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
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
import { useScimExternalGroups, useScimMappings } from "@/hooks/use-scim"
import { useRbacGroups } from "@/lib/hooks"

/**
 * Map synced IdP groups onto Tracecat groups.
 *
 * Without a mapping the projection has no rule to apply, so an IdP can push
 * users without granting them any access. `connected` reflects whether a SCIM
 * token exists; the editor explains that it stays inert until it does.
 */
export function OrgSettingsScimMappings({ connected }: { connected: boolean }) {
  const { externalGroups, externalGroupsIsLoading } = useScimExternalGroups()
  const {
    mappings,
    mappingsIsLoading,
    createMapping,
    createMappingIsPending,
    deleteMapping,
    deleteMappingIsPending,
  } = useScimMappings()
  const { groups, isLoading: groupsIsLoading } = useRbacGroups()

  const [externalGroupId, setExternalGroupId] = useState<string>("")
  const [groupId, setGroupId] = useState<string>("")

  async function handleCreate() {
    if (!externalGroupId || !groupId) {
      return
    }
    await createMapping({ externalGroupId, groupId })
    setExternalGroupId("")
    setGroupId("")
  }

  const header = (
    <div className="space-y-1">
      <h3 className="text-lg font-medium">Group mappings</h3>
      <p className="text-sm text-muted-foreground">
        Grant membership of a Tracecat group to everyone in a synced identity
        provider group. Without a mapping, provisioned users receive no access.
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

  const availableExternalGroups = externalGroups ?? []
  const availableGroups = groups ?? []
  const existingMappings = mappings ?? []

  return (
    <div className="space-y-4">
      {header}

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
              onClick={handleCreate}
              disabled={!externalGroupId || !groupId || createMappingIsPending}
            >
              {createMappingIsPending ? (
                <Loader2 className="mr-2 size-4 animate-spin" />
              ) : null}
              Add mapping
            </Button>
          </div>

          {existingMappings.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No mappings yet. Provisioned users stay without access until you
              add one.
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
                        disabled={deleteMappingIsPending}
                        onClick={() => deleteMapping(mapping.id)}
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
