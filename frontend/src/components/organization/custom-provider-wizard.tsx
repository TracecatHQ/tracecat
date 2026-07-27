import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { ArrowLeft, Check, Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import {
  type AgentCatalogRead,
  type AgentCustomProviderRead,
  type ApiError,
  type CustomProviderType,
  createCustomProvider,
  deleteCustomProvider,
  listCatalog,
  refreshCustomProviderCatalog,
  validateCustomProviderConnection,
} from "@/client"
import { ProviderIcon } from "@/components/icons"
import {
  AdvancedSection,
  BaseUrlField,
  CredentialFields,
} from "@/components/organization/custom-provider-fields"
import {
  buildProviderCreatePayload,
  CUSTOM_PROVIDER_TYPES,
  type CustomProviderFormValues,
  customProviderSchema,
  DEFAULT_CUSTOM_PROVIDER_VALUES,
  getCustomProviderTypeOption,
  typeDefaultPassthrough,
} from "@/components/organization/custom-provider-form"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"

type WizardStep = "type" | "config" | "test"

const DISCOVERY_PREVIEW_LIMIT = 12
const DISCOVERY_POLL_ATTEMPTS = 8
const DISCOVERY_POLL_INTERVAL_MS = 1500

/**
 * Fetch every catalog entry belonging to a custom provider. The list endpoint
 * has no custom-provider filter, so we page through and filter client-side.
 */
async function fetchProviderModels(
  providerId: string
): Promise<AgentCatalogRead[]> {
  const items: AgentCatalogRead[] = []
  let cursor: string | undefined
  do {
    const response = await listCatalog({ cursor, limit: 100 })
    for (const entry of response.items) {
      if (entry.custom_provider_id === providerId) {
        items.push(entry)
      }
    }
    cursor = response.next_cursor ?? undefined
  } while (cursor)
  return items
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/**
 * Step 1 type picker. Renders one selectable card per provider type.
 */
function TypeStep({
  value,
  onChange,
}: {
  value: CustomProviderType
  onChange: (type: CustomProviderType) => void
}) {
  return (
    <div className="space-y-3">
      {CUSTOM_PROVIDER_TYPES.map((type) => {
        const option = getCustomProviderTypeOption(type)
        const selected = value === type
        return (
          <button
            type="button"
            key={type}
            onClick={() => onChange(type)}
            aria-pressed={selected}
            className={cn(
              "flex w-full items-start gap-3 rounded-lg border p-4 text-left transition-colors",
              selected
                ? "border-foreground/40 bg-muted/50"
                : "border-border hover:bg-muted/30"
            )}
          >
            <ProviderIcon
              className="mt-0.5 size-6 rounded-md"
              providerId={option.iconId}
            />
            <div className="min-w-0 space-y-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium">{option.label}</span>
                {selected ? <Check className="size-4 text-foreground" /> : null}
              </div>
              <p className="text-xs text-muted-foreground">
                {option.description}
              </p>
            </div>
          </button>
        )
      })}
    </div>
  )
}

/**
 * Multi-step wizard for creating a custom provider.
 *
 * Steps: (1) pick type, (2) type-aware base URL + credentials, (3) live
 * connection test followed by a discovered-models preview. The provider is only
 * persisted once the connection test succeeds; Finish is gated on that success.
 */
export function CustomProviderWizard({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const form = useForm<CustomProviderFormValues>({
    resolver: zodResolver(customProviderSchema),
    mode: "onBlur",
    defaultValues: DEFAULT_CUSTOM_PROVIDER_VALUES,
  })

  const [step, setStep] = useState<WizardStep>("type")
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [connectionValid, setConnectionValid] = useState(false)
  const [createdProvider, setCreatedProvider] =
    useState<AgentCustomProviderRead | null>(null)
  const [discoveredModels, setDiscoveredModels] = useState<
    AgentCatalogRead[] | null
  >(null)

  const selectedType = form.watch("type")

  useEffect(() => {
    if (!open) {
      return
    }
    form.reset(DEFAULT_CUSTOM_PROVIDER_VALUES)
    setStep("type")
    setAdvancedOpen(false)
    setConnectionValid(false)
    setCreatedProvider(null)
    setDiscoveredModels(null)
  }, [open, form])

  // Picking a type prefills its per-type passthrough default (litellm/ollama
  // on, generic off). Prefill only, freely changeable afterward.
  function handleTypeChange(type: CustomProviderType) {
    form.setValue("type", type)
    form.setValue("passthrough", typeDefaultPassthrough(type))
  }

  const testMutation = useMutation({
    mutationFn: async (
      values: CustomProviderFormValues
    ): Promise<{
      provider: AgentCustomProviderRead
      models: AgentCatalogRead[]
    }> => {
      const payload = buildProviderCreatePayload(values)
      const result = await validateCustomProviderConnection({
        requestBody: payload,
      })
      if (!result.valid) {
        throw new Error("The provider did not respond successfully.")
      }
      // Re-testing must not orphan the provider created by a prior attempt.
      if (createdProvider) {
        await deleteCustomProvider({ providerId: createdProvider.id }).catch(
          () => {}
        )
        setCreatedProvider(null)
      }
      const provider = await createCustomProvider({ requestBody: payload })
      await refreshCustomProviderCatalog({ providerId: provider.id })
      // Discovery hydrates lazily; poll a bounded number of times for a preview.
      let models: AgentCatalogRead[] = []
      for (let attempt = 0; attempt < DISCOVERY_POLL_ATTEMPTS; attempt++) {
        models = await fetchProviderModels(provider.id)
        if (models.length > 0) {
          break
        }
        await delay(DISCOVERY_POLL_INTERVAL_MS)
      }
      return { provider, models }
    },
    onSuccess: ({ provider, models }) => {
      setConnectionValid(true)
      setCreatedProvider(provider)
      setDiscoveredModels(models)
      queryClient.invalidateQueries({
        queryKey: ["organization", "agent-providers"],
      })
      queryClient.invalidateQueries({
        queryKey: ["organization", "agent-catalog"],
      })
      // Discovery auto-enables every discovered model org-wide, so refresh the
      // access rows too or the settings view shows them as disabled.
      queryClient.invalidateQueries({
        queryKey: ["organization", "agent-model-access"],
      })
    },
    onError: (error: unknown) => {
      setConnectionValid(false)
      const detail =
        error instanceof Error
          ? (getApiErrorDetail(error as ApiError) ?? error.message)
          : "Unable to validate the custom source."
      toast({
        title: "Connection test failed",
        description: detail,
        variant: "destructive",
      })
    },
  })

  async function handleNextFromConfig() {
    const valid = await form.trigger([
      "type",
      "displayName",
      "baseUrl",
      "apiKeyHeader",
      "apiKey",
      "customHeadersJson",
      "passthrough",
    ])
    if (!valid) {
      if (form.formState.errors.customHeadersJson) {
        setAdvancedOpen(true)
      }
      return
    }
    setStep("test")
  }

  async function handleTestConnection() {
    const valid = await form.trigger()
    if (!valid) {
      if (form.formState.errors.customHeadersJson) {
        setAdvancedOpen(true)
      }
      return
    }
    await testMutation.mutateAsync(form.getValues()).catch(() => {})
  }

  function handleFinish() {
    if (!connectionValid) {
      return
    }
    onOpenChange(false)
    toast({
      title: "Custom source created",
      description: createdProvider
        ? `Created ${createdProvider.display_name}.`
        : "Created the custom source.",
    })
  }

  const previewModels =
    discoveredModels?.slice(0, DISCOVERY_PREVIEW_LIMIT) ?? []
  const hiddenModelCount = Math.max(
    0,
    (discoveredModels?.length ?? 0) - previewModels.length
  )

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Add custom source</DialogTitle>
          <DialogDescription>
            {step === "type"
              ? "Choose the kind of provider you want to connect."
              : step === "config"
                ? "Configure the endpoint and credentials."
                : "Test the connection and preview discovered models."}
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            onSubmit={(event) => event.preventDefault()}
            className="space-y-4"
          >
            {step === "type" ? (
              <TypeStep value={selectedType} onChange={handleTypeChange} />
            ) : null}

            {step === "config" ? (
              <>
                <FormField
                  control={form.control}
                  name="displayName"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Name</FormLabel>
                      <FormControl>
                        <Input {...field} placeholder="Local gateway" />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <BaseUrlField form={form} type={selectedType} />
                <CredentialFields
                  form={form}
                  type={selectedType}
                  isEdit={false}
                />
                <AdvancedSection
                  form={form}
                  type={selectedType}
                  open={advancedOpen}
                  onOpenChange={setAdvancedOpen}
                  surface="wizard"
                />
              </>
            ) : null}

            {step === "test" ? (
              <div className="space-y-4">
                <div className="rounded-lg border p-4">
                  <div className="flex items-center justify-between gap-4">
                    <div className="min-w-0 space-y-1">
                      <p className="text-sm font-medium">
                        {form.getValues("displayName") || "Custom source"}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        {getCustomProviderTypeOption(selectedType).label}
                        {form.getValues("baseUrl")
                          ? ` · ${form.getValues("baseUrl")}`
                          : ""}
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      disabled={testMutation.isPending}
                      onClick={() => void handleTestConnection()}
                    >
                      {testMutation.isPending ? (
                        <Loader2 className="mr-2 size-4 animate-spin" />
                      ) : null}
                      {connectionValid ? "Re-test" : "Test connection"}
                    </Button>
                  </div>
                </div>

                {connectionValid ? (
                  <div className="rounded-lg border p-4">
                    <div className="flex items-center gap-2">
                      <Check className="size-4 text-foreground" />
                      <p className="text-sm font-medium">Connection verified</p>
                    </div>
                    {previewModels.length ? (
                      <>
                        <p className="mt-3 text-xs text-muted-foreground">
                          Discovered models
                        </p>
                        <ul className="mt-2 space-y-1">
                          {previewModels.map((model) => (
                            <li
                              key={model.id}
                              className="truncate font-mono text-xs text-foreground"
                            >
                              {model.model_name}
                            </li>
                          ))}
                        </ul>
                        {hiddenModelCount > 0 ? (
                          <p className="mt-2 text-xs text-muted-foreground">
                            +{hiddenModelCount} more
                          </p>
                        ) : null}
                      </>
                    ) : (
                      <p className="mt-2 text-xs text-muted-foreground">
                        No models discovered yet. Discovery continues in the
                        background; refresh the source later to see them.
                      </p>
                    )}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">
                    Run the connection test to verify the endpoint and preview
                    discovered models before finishing.
                  </p>
                )}
              </div>
            ) : null}

            <DialogFooter className="gap-2 sm:gap-0">
              {step === "type" ? (
                <Button type="button" onClick={() => setStep("config")}>
                  Continue
                </Button>
              ) : null}

              {step === "config" ? (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => setStep("type")}
                  >
                    <ArrowLeft className="mr-2 size-4" />
                    Back
                  </Button>
                  <Button
                    type="button"
                    onClick={() => void handleNextFromConfig()}
                  >
                    Continue
                  </Button>
                </>
              ) : null}

              {step === "test" ? (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    disabled={testMutation.isPending}
                    onClick={() => {
                      setConnectionValid(false)
                      setDiscoveredModels(null)
                      setStep("config")
                    }}
                  >
                    <ArrowLeft className="mr-2 size-4" />
                    Back
                  </Button>
                  <Button
                    type="button"
                    disabled={!connectionValid || testMutation.isPending}
                    onClick={handleFinish}
                  >
                    Finish
                  </Button>
                </>
              ) : null}
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
