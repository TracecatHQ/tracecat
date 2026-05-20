"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useRouter } from "next/navigation"
import { useMemo } from "react"
import { useForm } from "react-hook-form"
import z from "zod"
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
import { toast } from "@/components/ui/use-toast"
import {
  useCreateAgentPreset,
  useMoveAgentPreset,
} from "@/hooks/use-agent-presets"
import { useAgentDefaultModel, useWorkspaceAgentModels } from "@/lib/hooks"
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
  const { models, providers, modelsLoading } =
    useWorkspaceAgentModels(workspaceId)
  const { defaultModel, defaultModelSelection, defaultModelLoading } =
    useAgentDefaultModel()
  const { createAgentPreset, createAgentPresetIsPending } =
    useCreateAgentPreset(workspaceId)
  const { moveAgentPreset, moveAgentPresetIsPending } =
    useMoveAgentPreset(workspaceId)

  const initialAgentModel = useMemo(() => {
    if (!models) return null
    return (
      (defaultModelSelection
        ? models.find((model) => model.id === defaultModelSelection.catalog_id)
        : null) ??
      (defaultModel
        ? models.find((model) => model.model_name === defaultModel)
        : null) ??
      models[0] ??
      null
    )
  }, [defaultModel, defaultModelSelection, models])

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
      toast({
        title: "Agent model required",
        description:
          "Enable an agent model in organization settings before creating an agent.",
        variant: "destructive",
      })
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
        currentPath && currentPath !== "/" ? currentPath : null
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
