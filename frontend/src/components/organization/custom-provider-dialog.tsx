import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import {
  type AgentCustomProviderRead,
  type ApiError,
  updateCustomProvider,
  validateCustomProviderConnection,
} from "@/client"
import {
  AdvancedSection,
  BaseUrlField,
  CredentialFields,
} from "@/components/organization/custom-provider-fields"
import {
  buildProviderCreatePayload,
  buildProviderUpdatePayload,
  CUSTOM_PROVIDER_TYPES,
  type CustomProviderFormValues,
  customProviderSchema,
  getCustomProviderTypeOption,
  getProviderDialogDefaults,
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail } from "@/lib/errors"

/**
 * Edit dialog for an existing custom provider. Unlike the create wizard this is
 * a single form, but `type` is editable via a select. Ollama hides credentials;
 * passthrough is a free toggle for every type and shows the stored value with no
 * type-driven mutation.
 */
export function CustomProviderDialog({
  provider,
  open,
  onOpenChange,
}: {
  provider: AgentCustomProviderRead
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const queryClient = useQueryClient()
  const form = useForm<CustomProviderFormValues>({
    resolver: zodResolver(customProviderSchema),
    mode: "onBlur",
    defaultValues: getProviderDialogDefaults(provider),
  })

  const selectedType = form.watch("type")
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const hasCustomHeadersError = !!form.formState.errors.customHeadersJson

  useEffect(() => {
    form.reset(getProviderDialogDefaults(provider))
    setAdvancedOpen(false)
  }, [form, provider, open])

  useEffect(() => {
    if (hasCustomHeadersError) {
      setAdvancedOpen(true)
    }
  }, [hasCustomHeadersError])

  const saveMutation = useMutation({
    mutationFn: async (values: CustomProviderFormValues) =>
      await updateCustomProvider({
        providerId: provider.id,
        requestBody: buildProviderUpdatePayload(values),
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["organization", "agent-providers"],
      })
      onOpenChange(false)
      toast({
        title: "Custom source updated",
        description: "Saved the custom source configuration.",
      })
    },
    onError: (error: ApiError) => {
      toast({
        title: "Update failed",
        description:
          getApiErrorDetail(error) ?? "Unable to save the custom source.",
        variant: "destructive",
      })
    },
  })

  const validateMutation = useMutation({
    mutationFn: async (values: CustomProviderFormValues) =>
      await validateCustomProviderConnection({
        requestBody: buildProviderCreatePayload(values),
      }),
    onSuccess: (result) => {
      toast({
        title: result.valid ? "Connection looks good" : "Connection failed",
        description: result.valid
          ? "The provider responded successfully."
          : "The provider did not respond successfully.",
        variant: result.valid ? "default" : "destructive",
      })
    },
    onError: (error: ApiError) => {
      toast({
        title: "Connection test failed",
        description:
          getApiErrorDetail(error) ?? "Unable to validate the custom source.",
        variant: "destructive",
      })
    },
  })

  async function handleValidate() {
    const valid = await form.trigger()
    if (!valid) {
      if (form.formState.errors.customHeadersJson) {
        setAdvancedOpen(true)
      }
      return
    }
    await validateMutation.mutateAsync(form.getValues())
  }

  async function handleSubmit(values: CustomProviderFormValues) {
    await saveMutation.mutateAsync(values)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Edit custom source</DialogTitle>
          <DialogDescription>
            Configure a user-defined LLM provider endpoint. Changing the type or
            base URL re-runs discovery.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleSubmit, () => {
              if (form.formState.errors.customHeadersJson) setAdvancedOpen(true)
            })}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="type"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Type</FormLabel>
                  <Select onValueChange={field.onChange} value={field.value}>
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      {CUSTOM_PROVIDER_TYPES.map((type) => (
                        <SelectItem key={type} value={type}>
                          {getCustomProviderTypeOption(type).label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />

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
            <CredentialFields form={form} type={selectedType} isEdit />
            <AdvancedSection
              form={form}
              type={selectedType}
              open={advancedOpen}
              onOpenChange={setAdvancedOpen}
              surface="edit"
            />

            <DialogFooter className="gap-2 sm:gap-0">
              <Button
                type="button"
                variant="outline"
                disabled={saveMutation.isPending || validateMutation.isPending}
                onClick={() => void handleValidate()}
              >
                {validateMutation.isPending ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : null}
                Test connection
              </Button>
              <Button
                type="submit"
                disabled={saveMutation.isPending || validateMutation.isPending}
              >
                {saveMutation.isPending ? (
                  <Loader2 className="mr-2 size-4 animate-spin" />
                ) : null}
                Save source
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
