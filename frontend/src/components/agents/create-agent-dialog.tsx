"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useMemo } from "react"
import { useForm } from "react-hook-form"
import z from "zod"
import { Spinner } from "@/components/loading/spinner"
import { useSettingsModal } from "@/components/settings/settings-modal-context"
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
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateAgentPreset,
  useMoveAgentPreset,
} from "@/hooks/use-agent-presets"
import { useEntitlements } from "@/hooks/use-entitlements"
import {
  useAgentDefaultModel,
  useUserScopes,
  useWorkspaceAgentModels,
} from "@/lib/hooks"
import { hasGrantedScope } from "@/lib/scopes"
import { useWorkspaceId } from "@/providers/workspace-id"

const createAgentSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Name is required")
    .max(120, "Name cannot be longer than 120 characters"),
  description: z.string().max(1000).optional(),
})

type CreateAgentFormValues = z.infer<typeof createAgentSchema>

interface CreateAgentDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentPath?: string | null
}

export function CreateAgentDialog({
  open,
  onOpenChange,
  currentPath,
}: CreateAgentDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? (
        <CreateAgentDialogContent
          currentPath={currentPath}
          onOpenChange={onOpenChange}
        />
      ) : null}
    </Dialog>
  )
}

function CreateAgentDialogContent({
  currentPath,
  onOpenChange,
}: {
  currentPath?: string | null
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const { setOpen: setSettingsOpen, setActiveSection } = useSettingsModal()
  const { userScopes } = useUserScopes()
  const canAdministerOrg = hasGrantedScope(
    "org:update",
    new Set(userScopes?.scopes ?? [])
  )
  const { userScopes: workspaceScopes } = useUserScopes(workspaceId)
  const canAdministerWorkspace = hasGrantedScope(
    "workspace:update",
    new Set(workspaceScopes?.scopes ?? [])
  )
  const {
    models,
    providers,
    catalogLoading,
    catalogError,
    providersLoading,
    providersError,
  } = useWorkspaceAgentModels(workspaceId)
  const {
    defaultModelSelection,
    defaultModelSelectionLoading: defaultModelLoading,
    defaultModelSelectionError: defaultModelError,
  } = useAgentDefaultModel()
  const { createAgentPreset, createAgentPresetIsPending } =
    useCreateAgentPreset(workspaceId)
  const { moveAgentPreset, moveAgentPresetIsPending } =
    useMoveAgentPreset(workspaceId)
  const { hasEntitlement } = useEntitlements()
  const foldersEnabled = hasEntitlement("agent_addons")

  const initialAgentModel = useMemo(() => {
    // The canonical endpoint also resolves legacy settings; null means no usable default.
    if (!models || !defaultModelSelection) return null
    return (
      models.find((model) => model.id === defaultModelSelection.catalog_id) ??
      null
    )
  }, [defaultModelSelection, models])

  const needsCustomProvider = Boolean(initialAgentModel?.custom_provider_id)
  const modelsLoading =
    catalogLoading || (needsCustomProvider && providersLoading)
  const modelsError = catalogError || (needsCustomProvider && providersError)

  const initialAgentModelBaseUrl = useMemo(() => {
    if (!initialAgentModel?.custom_provider_id) return null
    return (
      providers?.find(
        (provider) => provider.id === initialAgentModel.custom_provider_id
      )?.base_url ?? null
    )
  }, [initialAgentModel, providers])

  const methods = useForm<CreateAgentFormValues>({
    resolver: zodResolver(createAgentSchema),
    defaultValues: {
      name: "",
      description: "",
    },
  })

  const handleSubmit = async (values: CreateAgentFormValues) => {
    if (!initialAgentModel) {
      return
    }

    try {
      const preset = await createAgentPreset({
        name: values.name,
        model_provider: initialAgentModel.model_provider,
        model_name: initialAgentModel.model_name,
        catalog_id: initialAgentModel.id,
        base_url: initialAgentModelBaseUrl ?? undefined,
        description: values.description || undefined,
      })
      const targetFolderPath =
        foldersEnabled && currentPath && currentPath !== "/"
          ? currentPath
          : null
      if (targetFolderPath) {
        try {
          await moveAgentPreset({
            presetId: preset.id,
            folder_path: targetFolderPath,
          })
        } catch {
          // Move hook already toasts; continue to open the created preset.
        }
      }
      methods.reset()
      onOpenChange(false)
      router.push(`/workspaces/${workspaceId}/agents/${preset.id}`)
    } catch (error) {
      console.error("Failed to create agent:", error)
    }
  }

  if (modelsError || defaultModelError) {
    return (
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Unable to load models</DialogTitle>
          <DialogDescription>
            We couldn&apos;t load the models available for this workspace.
            Please try again later.
          </DialogDescription>
        </DialogHeader>
      </DialogContent>
    )
  }

  if (modelsLoading || defaultModelLoading || !models) {
    return (
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Loading models</DialogTitle>
          <DialogDescription>
            Loading the models available for this workspace.
          </DialogDescription>
        </DialogHeader>
        <div className="flex justify-center py-4" aria-label="Loading models">
          <Spinner className="size-6" />
        </div>
      </DialogContent>
    )
  }

  if (!initialAgentModel) {
    // A legacy name may remain after its model is disabled organization-wide.
    const hasDefaultModel = Boolean(defaultModelSelection)
    let description: string
    if (hasDefaultModel) {
      description = canAdministerWorkspace
        ? "The organization default model is not enabled for this workspace. Enable it in workspace AI model settings before creating an agent."
        : "The organization default model is not enabled for this workspace. Ask a workspace administrator to enable it before creating an agent."
    } else {
      description = canAdministerOrg
        ? "Choose a default model in organization settings before creating an agent."
        : "Ask an organization administrator to configure a default model before creating an agent."
    }

    return (
      <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {hasDefaultModel
              ? "Enable the default model"
              : "Set up model provider"}
          </DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        {hasDefaultModel && canAdministerWorkspace ? (
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                onOpenChange(false)
                setActiveSection("workspace-models")
                setSettingsOpen(true)
              }}
            >
              Configure workspace models
            </Button>
          </DialogFooter>
        ) : null}
        {!hasDefaultModel && canAdministerOrg ? (
          <DialogFooter>
            <Button asChild variant="outline">
              <Link
                href="/organization/settings/agent"
                onClick={() => onOpenChange(false)}
              >
                Configure models
              </Link>
            </Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    )
  }

  return (
    <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Create agent</DialogTitle>
        <DialogDescription>
          Give the agent a name and optional description. You can change its
          model after creation.
        </DialogDescription>
      </DialogHeader>
      <Form {...methods}>
        <form onSubmit={methods.handleSubmit(handleSubmit)}>
          <div className="grid gap-4">
            <FormField
              control={methods.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-sm">Name</FormLabel>
                  <FormControl>
                    <Input
                      className="text-sm"
                      placeholder="My agent"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={methods.control}
              name="description"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-sm">
                    Description{" "}
                    <span className="text-muted-foreground">(optional)</span>
                  </FormLabel>
                  <FormControl>
                    <Textarea
                      className="min-h-[60px] resize-none text-sm"
                      placeholder="What does this agent do?"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button
                type="submit"
                disabled={
                  createAgentPresetIsPending ||
                  moveAgentPresetIsPending ||
                  modelsLoading ||
                  defaultModelLoading
                }
              >
                Create agent
              </Button>
            </DialogFooter>
          </div>
        </form>
      </Form>
    </DialogContent>
  )
}
