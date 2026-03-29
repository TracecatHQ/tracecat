"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ShieldCheck } from "lucide-react"
import {
  caseDropdownsUpdateDropdownDefinition,
  casesUpdateField,
} from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Switch } from "@/components/ui/switch"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { toast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useCaseDropdownDefinitions, useCaseFields } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Unified view for managing which custom fields and dropdowns
 * are required before a case can be closed or resolved.
 */
export function ClosureRequirementsView() {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const { hasEntitlement } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")

  const { caseFields, caseFieldsIsLoading, caseFieldsError } =
    useCaseFields(workspaceId)
  const { dropdownDefinitions, dropdownDefinitionsIsLoading } =
    useCaseDropdownDefinitions(workspaceId, caseAddonsEnabled)

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

  if (caseFieldsIsLoading || dropdownDefinitionsIsLoading) {
    return <CenteredSpinner />
  }
  if (caseFieldsError) {
    return <AlertNotification level="error" message={caseFieldsError.message} />
  }

  const customFields = (caseFields ?? []).filter((f) => !f.reserved)
  const dropdowns = dropdownDefinitions ?? []
  const isEmpty = customFields.length === 0 && dropdowns.length === 0

  if (isEmpty) {
    return (
      <Empty>
        <EmptyMedia>
          <ShieldCheck className="size-10" />
        </EmptyMedia>
        <EmptyHeader>
          <EmptyTitle>No fields or dropdowns defined yet</EmptyTitle>
          <EmptyDescription>
            Create custom fields or dropdowns first, then configure which ones
            are required before a case can be closed or resolved.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  async function handleFieldToggle(fieldId: string, checked: boolean) {
    try {
      await updateField({ fieldId, requiredOnClosure: checked })
      toast({
        title: checked ? "Field marked as required" : "Requirement removed",
        description: `${fieldId} ${checked ? "will be" : "is no longer"} required on closure.`,
      })
    } catch {
      toast({
        variant: "destructive",
        title: "Failed to update field",
        description: "Please try again.",
      })
    }
  }

  async function handleDropdownToggle(
    definitionId: string,
    name: string,
    checked: boolean
  ) {
    try {
      await updateDropdown({ definitionId, requiredOnClosure: checked })
      toast({
        title: checked ? "Dropdown marked as required" : "Requirement removed",
        description: `${name} ${checked ? "will be" : "is no longer"} required on closure.`,
      })
    } catch {
      toast({
        variant: "destructive",
        title: "Failed to update dropdown",
        description: "Please try again.",
      })
    }
  }

  return (
    <div className="space-y-6">
      <div className="space-y-1">
        <h3 className="text-sm font-medium">Closure requirements</h3>
        <p className="text-sm text-muted-foreground">
          Toggle which fields and dropdowns must be filled before a case can be
          closed or resolved.
        </p>
      </div>
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-[200px]">Name</TableHead>
            <TableHead>Type</TableHead>
            <TableHead className="w-[120px] text-right">Required</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {customFields.map((field) => (
            <TableRow key={`field-${field.id}`}>
              <TableCell className="font-medium">{field.id}</TableCell>
              <TableCell className="text-muted-foreground">
                {field.kind ?? field.type}
              </TableCell>
              <TableCell className="text-right">
                <Switch
                  checked={field.required_on_closure ?? false}
                  onCheckedChange={(checked) =>
                    handleFieldToggle(field.id, checked)
                  }
                />
              </TableCell>
            </TableRow>
          ))}
          {dropdowns.map((dd) => (
            <TableRow key={`dropdown-${dd.id}`}>
              <TableCell className="font-medium">{dd.name}</TableCell>
              <TableCell className="text-muted-foreground">Dropdown</TableCell>
              <TableCell className="text-right">
                <Switch
                  checked={dd.required_on_closure}
                  onCheckedChange={(checked) =>
                    handleDropdownToggle(dd.id, dd.name, checked)
                  }
                />
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  )
}
