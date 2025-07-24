"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  BotIcon,
  CheckCircleIcon,
  SettingsIcon,
  Trash2Icon,
} from "lucide-react"
import { useState } from "react"
import { useForm, useFormContext } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { AgentCredentialsDialog } from "@/components/organization/org-agent-credentials-dialog"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  useAgentDefaultModel,
  useAgentModels,
  useDeleteProviderCredentials,
  useModelProvidersStatus,
  useProviderCredentialConfigs,
} from "@/lib/hooks"

const agentFormSchema = z.object({
  default_model: z.string().optional(),
})

type AgentFormValues = z.infer<typeof agentFormSchema>

/**
 * Subcomponent that handles the default model selection field
 * Uses useFormContext to access the form and depends on models and defaultModel data
 */
function DefaultModelSelector({ isUpdating }: { isUpdating: boolean }) {
  const form = useFormContext<AgentFormValues>()
  const { models, modelsLoading, modelsError } = useAgentModels()

  if (modelsLoading) {
    return <CenteredSpinner />
  }

  if (modelsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading models: ${modelsError instanceof Error ? modelsError.message : "Unknown error"}`}
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
    <>
      <FormField
        control={form.control}
        name="default_model"
        render={({ field }) => (
          <FormItem className="flex flex-col">
            <FormLabel>Default AI model</FormLabel>
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
            <FormDescription>
              Select the default AI model for agent operations in your
              organization.
            </FormDescription>
            <FormMessage />
          </FormItem>
        )}
      />

      <Button type="submit" disabled={isUpdating}>
        {isUpdating ? "Updating..." : "Update agent settings"}
      </Button>
    </>
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

/**
 * Main form component that coordinates the subcomponents and handles form submission
 */
export function OrgSettingsAgentForm() {
  const {
    defaultModel,
    defaultModelLoading,
    defaultModelError,
    updateDefaultModel,
    isUpdating,
  } = useAgentDefaultModel()

  const form = useForm<AgentFormValues>({
    resolver: zodResolver(agentFormSchema),
    values: {
      default_model: defaultModel || "",
    },
  })

  const onSubmit = async (data: AgentFormValues) => {
    if (!data.default_model) return

    try {
      await updateDefaultModel(data.default_model)
    } catch (err) {
      console.error("Failed to update default model:", err)
    }
  }

  // Handle loading state for the initial form data
  if (defaultModelLoading) {
    return <CenteredSpinner />
  }

  // Handle error state for the initial form data
  if (defaultModelError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading agent configuration: ${defaultModelError instanceof Error ? defaultModelError.message : "Unknown error"}`}
      />
    )
  }

  return (
    <div className="space-y-8">
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
          <DefaultModelSelector isUpdating={isUpdating} />
        </form>
      </Form>

      <ProviderCredentialsSection />
    </div>
  )
}
