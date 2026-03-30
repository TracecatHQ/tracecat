"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowUpRight, ShieldCheck } from "lucide-react"
import {
  caseDropdownsUpdateDropdownDefinition,
  casesUpdateField,
} from "@/client"
import { DataTable, DataTableColumnHeader } from "@/components/data-table"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import { useCaseDropdownDefinitions, useCaseFields } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Row shape for the unified closure requirements table. */
interface ClosureRow {
  id: string
  name: string
  type: string
  kind: "field" | "dropdown"
  required_on_closure: boolean
}

/**
 * Unified view for managing which custom fields and dropdowns
 * are required before a case can be closed or resolved.
 */
export function ClosureRequirementsView() {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const { workspace, workspaceLoading, workspaceError } = useWorkspaceDetails()
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")

  const { caseFields, caseFieldsIsLoading, caseFieldsError } =
    useCaseFields(workspaceId)
  const {
    dropdownDefinitions,
    dropdownDefinitionsIsLoading,
    dropdownDefinitionsError,
  } = useCaseDropdownDefinitions(workspaceId, caseAddonsEnabled)

  const { mutateAsync: updateField } = useMutation({
    mutationFn: async ({
      fieldId,
      requiredOnClosure,
    }: {
      fieldId: string
      requiredOnClosure: boolean
    }) => {
      await casesUpdateField({
        workspaceId,
        fieldId,
        requestBody: { required_on_closure: requiredOnClosure },
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["case-fields", workspaceId] })
    },
  })

  const { mutateAsync: updateDropdown } = useMutation({
    mutationFn: async ({
      definitionId,
      requiredOnClosure,
    }: {
      definitionId: string
      requiredOnClosure: boolean
    }) => {
      await caseDropdownsUpdateDropdownDefinition({
        workspaceId,
        definitionId,
        requestBody: { required_on_closure: requiredOnClosure },
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["case-dropdown-definitions", workspaceId],
      })
    },
  })

  if (entitlementsLoading) {
    return <CenteredSpinner />
  }

  if (!caseAddonsEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] items-center justify-center py-8">
          <EntitlementRequiredEmptyState
            title="Enterprise only"
            description="Closure requirements are only available on enterprise plans."
          >
            <Button
              variant="link"
              asChild
              className="text-muted-foreground"
              size="sm"
            >
              <a
                href="https://tracecat.com"
                target="_blank"
                rel="noopener noreferrer"
              >
                Learn more <ArrowUpRight className="size-4" />
              </a>
            </Button>
          </EntitlementRequiredEmptyState>
        </div>
      </div>
    )
  }

  if (workspaceLoading || caseFieldsIsLoading || dropdownDefinitionsIsLoading) {
    return <CenteredSpinner />
  }

  if (workspaceError) {
    return (
      <AlertNotification
        level="error"
        message="Error loading workspace info."
      />
    )
  }

  if (!workspace) {
    return <AlertNotification level="error" message="Workspace not found." />
  }

  if (caseFieldsError) {
    return <AlertNotification level="error" message={caseFieldsError.message} />
  }

  if (dropdownDefinitionsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading dropdowns: ${dropdownDefinitionsError.message}`}
      />
    )
  }

  const customFields = (caseFields ?? []).filter((f) => !f.reserved)
  const dropdowns = dropdownDefinitions ?? []

  const rows: ClosureRow[] = [
    ...customFields.map(
      (f): ClosureRow => ({
        id: f.id,
        name: f.id,
        type: f.kind ?? f.type,
        kind: "field",
        required_on_closure: f.required_on_closure ?? false,
      })
    ),
    ...dropdowns.map(
      (d): ClosureRow => ({
        id: d.id,
        name: d.name,
        type: "Dropdown",
        kind: "dropdown",
        required_on_closure: d.required_on_closure,
      })
    ),
  ]

  if (rows.length === 0) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
          <Empty className="h-full">
            <EmptyHeader>
              <EmptyMedia variant="icon">
                <ShieldCheck className="size-6" />
              </EmptyMedia>
              <EmptyTitle>No fields or dropdowns defined yet</EmptyTitle>
              <EmptyDescription>
                Create custom fields or dropdowns first, then configure which
                ones are required before a case can be closed or resolved.
              </EmptyDescription>
            </EmptyHeader>
          </Empty>
        </div>
      </div>
    )
  }

  async function handleToggle(row: ClosureRow, checked: boolean) {
    try {
      if (row.kind === "field") {
        await updateField({ fieldId: row.id, requiredOnClosure: checked })
      } else {
        await updateDropdown({
          definitionId: row.id,
          requiredOnClosure: checked,
        })
      }
      toast({
        title: checked ? "Marked as required" : "Requirement removed",
        description: `${row.name} ${checked ? "will be" : "is no longer"} required on closure.`,
      })
    } catch {
      toast({
        variant: "destructive",
        title: "Failed to update",
        description: "Please try again.",
      })
    }
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        <div className="space-y-4">
          <DataTable
            data={rows}
            toolbarProps={{
              filterProps: {
                column: "name",
                placeholder: "Filter by name...",
              },
            }}
            columns={[
              {
                accessorKey: "name",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Name"
                  />
                ),
                cell: ({ row }) => (
                  <div className="text-xs text-foreground/80">
                    {row.getValue<string>("name")}
                  </div>
                ),
                enableSorting: true,
                enableHiding: false,
              },
              {
                accessorKey: "type",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Type"
                  />
                ),
                cell: ({ row }) => (
                  <div className="text-xs text-muted-foreground">
                    {row.getValue<string>("type")}
                  </div>
                ),
                enableSorting: true,
                enableHiding: false,
              },
              {
                accessorKey: "required_on_closure",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Required"
                  />
                ),
                cell: ({ row }) => (
                  <Switch
                    checked={row.getValue<boolean>("required_on_closure")}
                    onCheckedChange={(checked) =>
                      handleToggle(row.original, checked)
                    }
                  />
                ),
                enableSorting: false,
                enableHiding: false,
              },
            ]}
          />
        </div>
      </div>
    </div>
  )
}
