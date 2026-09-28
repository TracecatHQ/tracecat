"use client"

import { Plus, Trash2 } from "lucide-react"
import { useFieldArray, useFormContext } from "react-hook-form"
import { ActionSelect } from "@/components/chat/action-select"
import type { Suggestion } from "@/components/tags-input"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { FormControl, FormField, FormItem } from "@/components/ui/form"
import { Switch } from "@/components/ui/switch"
import { useEntitlements } from "@/hooks/use-entitlements"

interface ApprovalRuleFields {
  toolApprovals: Array<{ tool: string; allow: boolean }>
}

/** Edit paid approval rules while allowing removal after a plan downgrade. */
export function AgentPresetApprovalRules({
  isSaving,
  actionSuggestions,
}: {
  isSaving: boolean
  actionSuggestions: Suggestion[]
}) {
  const { control } = useFormContext<ApprovalRuleFields>()
  const { fields, append, remove } = useFieldArray({
    control,
    name: "toolApprovals",
  })
  const { hasEntitlement, isLoading } = useEntitlements()
  const approvalsEnabled = hasEntitlement("agent_addons")

  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <p className="text-sm font-medium">Approval rules</p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={() => append({ tool: "", allow: true })}
          disabled={isSaving || !approvalsEnabled}
        >
          <Plus className="mr-2 size-4" />
          Add rule
        </Button>
      </div>
      {!isLoading && !approvalsEnabled ? (
        <Alert>
          <AlertDescription>
            Approval rules require an Enterprise plan. You can remove existing
            rules to run this agent without approvals.
          </AlertDescription>
        </Alert>
      ) : null}
      {fields.length === 0 ? (
        <p className="rounded-md border border-dashed px-3 py-4 text-xs text-muted-foreground">
          No manual approval rules yet. Add a tool to require human review or to
          force manual overrides.
        </p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="grid gap-3 px-3 md:grid-cols-[minmax(0,1fr)_220px_auto] md:items-center">
            <div className="text-xs font-medium text-muted-foreground">
              Tool
            </div>
            <div className="text-xs font-medium text-muted-foreground md:text-center">
              Manual approval
            </div>
            <div className="w-10" aria-hidden="true" />
          </div>

          <div className="flex flex-col gap-2">
            {fields.map((item, index) => {
              const approvalSwitchId = `tool-approval-${item.id}-allow`

              return (
                <div
                  key={item.id}
                  className="grid gap-3 px-3 py-3 md:grid-cols-[minmax(0,1fr)_220px_auto] md:items-center"
                >
                  <FormField
                    control={control}
                    name={`toolApprovals.${index}.tool`}
                    render={({ field }) => (
                      <FormItem className="flex-1">
                        <FormControl>
                          <ActionSelect
                            field={field}
                            suggestions={[...actionSuggestions]}
                            searchKeys={[
                              "label",
                              "value",
                              "description",
                              "group",
                            ]}
                            placeholder="Select an action or MCP tool..."
                            disabled={isSaving || !approvalsEnabled}
                          />
                        </FormControl>
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={control}
                    name={`toolApprovals.${index}.allow`}
                    render={({ field }) => (
                      <FormItem className="md:justify-self-center">
                        <FormControl>
                          <div className="flex items-center gap-3 px-3 py-2">
                            <Switch
                              id={approvalSwitchId}
                              aria-label="Manual approval"
                              checked={Boolean(field.value)}
                              onCheckedChange={field.onChange}
                              disabled={isSaving || !approvalsEnabled}
                            />
                            <span className="text-sm font-medium min-w-[100px]">
                              {field.value ? "Required" : "Not required"}
                            </span>
                          </div>
                        </FormControl>
                      </FormItem>
                    )}
                  />
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="justify-self-start self-start text-muted-foreground md:justify-self-end"
                    onClick={() => remove(index)}
                    disabled={isSaving}
                    aria-label="Remove approval rule"
                  >
                    <Trash2 className="size-4" />
                  </Button>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </section>
  )
}
