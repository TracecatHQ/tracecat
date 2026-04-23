"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  BotIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  SettingsIcon,
  Trash2Icon,
} from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { AgentCredentialsDialog } from "@/components/organization/org-agent-credentials-dialog"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useAgentDefaultModel,
  useAgentModels,
  useDeleteProviderCredentials,
  useModelProvidersStatus,
  useOrgAgentUsage,
  useProviderCredentialConfigs,
  useWorkspaceManager,
} from "@/lib/hooks"

const agentFormSchema = z.object({
  default_model: z.string().optional(),
})

type AgentFormValues = z.infer<typeof agentFormSchema>

/**
 * Default-model form. A dropdown and a Save button — mirrors the shape of
 * every other settings form on the site.
 */
function DefaultModelForm() {
  const { models, modelsLoading, modelsError } = useAgentModels()
  const {
    defaultModel,
    defaultModelLoading,
    defaultModelError,
    updateDefaultModel,
    isUpdating,
  } = useAgentDefaultModel()

  const form = useForm<AgentFormValues>({
    resolver: zodResolver(agentFormSchema),
    values: { default_model: defaultModel || "" },
  })

  async function onSubmit(data: AgentFormValues) {
    if (!data.default_model || data.default_model === defaultModel) return
    try {
      await updateDefaultModel(data.default_model)
      toast({
        title: "Default model updated",
        description: `Agent operations will now use ${data.default_model}.`,
      })
    } catch (err) {
      console.error("Failed to update default model", err)
      toast({
        title: "Failed to update default model",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  if (modelsLoading || defaultModelLoading) {
    return <CenteredSpinner />
  }

  if (modelsError || defaultModelError) {
    const err = modelsError || defaultModelError
    return (
      <AlertNotification
        level="error"
        message={`Error loading agent configuration: ${err instanceof Error ? err.message : "Unknown error"}`}
      />
    )
  }

  if (!models) {
    return (
      <AlertNotification
        level="error"
        message="Failed to load available models"
      />
    )
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormField
          control={form.control}
          name="default_model"
          render={({ field }) => (
            <FormItem className="flex flex-col">
              <FormLabel>Default AI model</FormLabel>
              <div className="flex items-center gap-2">
                <FormControl>
                  <Select onValueChange={field.onChange} value={field.value}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select a model" />
                    </SelectTrigger>
                    <SelectContent>
                      {Object.entries(models).map(([modelName, model]) => (
                        <SelectItem key={modelName} value={modelName}>
                          <div className="flex items-center space-x-2">
                            <BotIcon className="size-4" />
                            <span>{model.name}</span>
                            <span className="text-xs text-muted-foreground">
                              ({model.provider})
                            </span>
                          </div>
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </FormControl>
                <Button type="submit" disabled={isUpdating}>
                  {isUpdating ? "Saving..." : "Save"}
                </Button>
              </div>
              <FormDescription>
                Select the default AI model for agent operations in your
                organization.
              </FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
      </form>
    </Form>
  )
}

/**
 * Subcomponent that handles provider credentials configuration
 * Depends on models and providersStatus data
 */
function ProviderCredentialsSection() {
  const [selectedCredentialsProvider, setSelectedCredentialsProvider] =
    useState<string | null>(null)
  const [deleteConfirmProvider, setDeleteConfirmProvider] = useState<
    string | null
  >(null)

  const { models, modelsLoading, modelsError } = useAgentModels()
  const {
    providersStatus,
    isLoading: statusLoading,
    error: statusError,
    refetch: refetchStatus,
  } = useModelProvidersStatus()
  const { providerConfigs, providerConfigsLoading, providerConfigsError } =
    useProviderCredentialConfigs()
  const { deleteProviderCredentials, isDeletingCredentials } =
    useDeleteProviderCredentials()

  const getProviderStatus = (provider: string) => {
    return providersStatus?.[provider] || false
  }

  const refreshProvidersStatus = () => {
    refetchStatus()
  }

  const handleDeleteCredentials = async (provider: string) => {
    try {
      await deleteProviderCredentials(provider)
      setDeleteConfirmProvider(null)
      refreshProvidersStatus()
    } catch (error) {
      console.error(`Failed to delete credentials for ${provider}:`, error)
    }
  }

  if (modelsLoading || statusLoading || providerConfigsLoading) {
    return <CenteredSpinner />
  }

  if (modelsError || statusError || providerConfigsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading provider data: ${(modelsError || statusError || providerConfigsError) instanceof Error ? (modelsError || statusError || providerConfigsError)?.message : "Unknown error"}`}
      />
    )
  }

  if (!models || !providersStatus) {
    return (
      <AlertNotification
        level="error"
        message="Failed to load provider credentials data"
      />
    )
  }

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">Provider credentials</h3>
      <p className="text-sm text-muted-foreground">
        Configure credentials for AI providers to enable model access.
      </p>

      <div className="grid gap-4">
        {providerConfigs?.map(({ provider, label }) => {
          const hasCredentials = getProviderStatus(provider)
          return (
            <div
              key={provider}
              className="flex items-center justify-between rounded-lg border p-4"
            >
              <div className="flex items-center space-x-3">
                <div className="flex items-center space-x-2">
                  {hasCredentials ? (
                    <CheckCircleIcon className="size-5 text-green-500" />
                  ) : (
                    <SettingsIcon className="size-5 text-muted-foreground" />
                  )}
                  <h4 className="font-medium">{label}</h4>
                </div>
              </div>
              <div className="flex items-center space-x-2">
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setSelectedCredentialsProvider(provider)}
                >
                  {hasCredentials ? "Update" : "Configure"}
                </Button>
                {hasCredentials && (
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => setDeleteConfirmProvider(provider)}
                    disabled={isDeletingCredentials}
                  >
                    <Trash2Icon className="size-3.5" />
                  </Button>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <AgentCredentialsDialog
        provider={selectedCredentialsProvider}
        isOpen={selectedCredentialsProvider !== null}
        onClose={() => setSelectedCredentialsProvider(null)}
        onSuccess={refreshProvidersStatus}
      />

      <AlertDialog
        open={deleteConfirmProvider !== null}
        onOpenChange={(open) => !open && setDeleteConfirmProvider(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete credentials</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the credentials for{" "}
              <span className="font-medium">
                {deleteConfirmProvider &&
                  providerConfigs?.find(
                    (p) => p.provider === deleteConfirmProvider
                  )?.label}
              </span>
              ? This action cannot be undone and will disable access to models
              from this provider.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={isDeletingCredentials}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={() =>
                deleteConfirmProvider &&
                handleDeleteCredentials(deleteConfirmProvider)
              }
              disabled={isDeletingCredentials}
              className="bg-destructive hover:bg-destructive/90"
            >
              {isDeletingCredentials ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}

const DOLLAR_FORMATTER = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
})

/**
 * Format a cent amount as a localized USD string ("1234" → "$12.34").
 */
function formatCents(cents: number | null | undefined): string {
  if (cents == null || !Number.isFinite(cents)) return "$0.00"
  return DOLLAR_FORMATTER.format(cents / 100)
}

const budgetFormSchema = z.object({
  // Blank string clears the cap; otherwise must be a non-negative dollar
  // value (up to 2 decimal places). The backend stores integer cents so we
  // reject anything finer-grained than a cent.
  monthly_budget_dollars: z
    .string()
    .trim()
    .refine(
      (value) => {
        if (value === "") return true
        const parsed = Number(value)
        return Number.isFinite(parsed) && parsed > 0
      },
      {
        message: "Enter a positive dollar amount, or leave blank to remove.",
      }
    ),
})

type BudgetFormValues = z.infer<typeof budgetFormSchema>

/**
 * Agent spend section: shows the current UTC-month total, an optional
 * per-workspace breakdown, and a form to set or clear the monthly dollar
 * budget cap.
 */
function AgentUsageSection() {
  const { usage, usageLoading, usageError, updateUsageLimit, isUpdatingLimit } =
    useOrgAgentUsage()
  const { workspaces } = useWorkspaceManager()
  const workspaceNameById = new Map(
    (workspaces ?? []).map((ws) => [ws.id, ws.name])
  )

  const initialBudgetDollars =
    usage?.limit_cents != null ? (usage.limit_cents / 100).toFixed(2) : ""

  const form = useForm<BudgetFormValues>({
    resolver: zodResolver(budgetFormSchema),
    values: {
      monthly_budget_dollars: initialBudgetDollars,
    },
  })

  async function onSubmit(data: BudgetFormValues) {
    const raw = data.monthly_budget_dollars.trim()
    const nextCents =
      raw === "" ? null : Math.round(Number.parseFloat(raw) * 100)
    try {
      await updateUsageLimit({ monthly_budget_cents: nextCents })
      toast({
        title: nextCents === null ? "Budget cleared" : "Budget updated",
        description:
          nextCents === null
            ? "Agent runs in this organization are now uncapped."
            : `Monthly budget set to ${formatCents(nextCents)}.`,
      })
    } catch (error) {
      console.error("Failed to update budget", error)
      toast({
        title: "Failed to update budget",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  if (usageLoading) {
    return <CenteredSpinner />
  }

  if (usageError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading agent usage: ${usageError instanceof Error ? usageError.message : "Unknown error"}`}
      />
    )
  }

  if (!usage) {
    return (
      <AlertNotification level="error" message="Failed to load agent usage" />
    )
  }

  const totalCents = usage.total_cents ?? 0
  const limitCents = usage.limit_cents ?? null
  const byWorkspaceCents = usage.by_workspace_cents ?? {}

  // Intentionally uncapped: an org can record spend past its cap between the
  // last check and the SDK terminating a run, and we want that visible (e.g.
  // "$0.09 of $0.06 (150%)") rather than flattened to 100%.
  const percentUsed =
    limitCents == null || limitCents === 0
      ? null
      : Math.round((totalCents / limitCents) * 100)

  const workspaceRows = Object.entries(byWorkspaceCents).sort(
    ([, a], [, b]) => b - a
  )

  const spentLine =
    limitCents == null
      ? `Spent ${formatCents(totalCents)} this month`
      : `Spent ${formatCents(totalCents)} of ${formatCents(limitCents)} this month${
          percentUsed != null ? ` (${percentUsed}%)` : ""
        }`

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <FormItem>
          <FormLabel>Spend this month (UTC)</FormLabel>
          <FormControl>
            <Input value={spentLine} disabled />
          </FormControl>
          <FormDescription>
            Dollar cost of agent runs in this organization. Counters reset at
            the start of each UTC month.
          </FormDescription>
        </FormItem>

        <FormField
          control={form.control}
          name="monthly_budget_dollars"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Monthly budget (USD)</FormLabel>
              <div className="flex items-center gap-2">
                <FormControl>
                  <Input
                    type="number"
                    min={0}
                    step={0.01}
                    inputMode="decimal"
                    placeholder="Leave blank for unlimited"
                    {...field}
                  />
                </FormControl>
                <Button type="submit" disabled={isUpdatingLimit}>
                  {isUpdatingLimit ? "Saving..." : "Save"}
                </Button>
              </div>
              <FormDescription>
                New agent runs are blocked once the organization's spend for the
                current UTC month reaches this budget.
              </FormDescription>
              <FormMessage />

              {workspaceRows.length > 0 ? (
                <Accordion type="single" collapsible>
                  <AccordionItem value="by-workspace" className="border-b-0">
                    <AccordionTrigger
                      className={
                        "justify-start gap-2 py-2 text-sm text-muted-foreground " +
                        "hover:no-underline [&>svg:last-child]:hidden " +
                        "[&[data-state=open]_[data-chevron]]:rotate-180"
                      }
                    >
                      <ChevronDownIcon
                        data-chevron
                        className="size-4 shrink-0 transition-transform duration-200"
                      />
                      <span>Spend by workspace</span>
                    </AccordionTrigger>
                    <AccordionContent className="pb-2 pt-2">
                      <ul className="space-y-1 text-sm">
                        {workspaceRows.map(([workspaceId, cents]) => {
                          const name = workspaceNameById.get(workspaceId)
                          const label =
                            name ??
                            (workspaceId === "unknown"
                              ? "Unknown workspace"
                              : "Deleted workspace")
                          return (
                            <li
                              key={workspaceId}
                              className="flex items-center justify-between text-muted-foreground"
                            >
                              <span>{label}</span>
                              <span className="tabular-nums">
                                {formatCents(cents)}
                              </span>
                            </li>
                          )
                        })}
                      </ul>
                    </AccordionContent>
                  </AccordionItem>
                </Accordion>
              ) : null}
            </FormItem>
          )}
        />
      </form>
    </Form>
  )
}

/**
 * Conditionally mounts the agent usage section only when the organization has
 * the agent_addons entitlement. Keeps the section's queries from firing for
 * orgs that don't have access.
 */
function AgentUsageGate() {
  const { hasEntitlement, isLoading } = useEntitlements()
  if (isLoading) return null
  if (!hasEntitlement("agent_addons")) return null
  return (
    <>
      <AgentUsageSection />
      <div className="h-px w-full bg-border" />
    </>
  )
}

/**
 * Main form component that coordinates the subcomponents.
 */
export function OrgSettingsAgentForm() {
  return (
    <div className="space-y-8">
      <AgentUsageGate />

      <DefaultModelForm />

      <ProviderCredentialsSection />
    </div>
  )
}
