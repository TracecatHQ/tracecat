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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { useCreateAgentPreset } from "@/hooks/use-agent-presets"
import { useWorkspaceAgentModels } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const createAgentSchema = z.object({
  name: z
    .string()
    .trim()
    .min(1, "Name is required")
    .max(120, "Name cannot be longer than 120 characters"),
  model_provider: z.string().min(1, "Provider is required"),
  model_name: z.string().min(1, "Model is required"),
  catalog_id: z.string().optional(),
  description: z.string().max(1000).optional(),
})

type CreateAgentFormValues = z.infer<typeof createAgentSchema>

interface CreateAgentDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function CreateAgentDialog({
  open,
  onOpenChange,
}: CreateAgentDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open ? <CreateAgentDialogContent onOpenChange={onOpenChange} /> : null}
    </Dialog>
  )
}

function CreateAgentDialogContent({
  onOpenChange,
}: {
  onOpenChange: (open: boolean) => void
}) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const { models, modelsLoading } = useWorkspaceAgentModels(workspaceId)
  const { createAgentPreset, createAgentPresetIsPending } =
    useCreateAgentPreset(workspaceId)

  const providers = useMemo(() => {
    return Array.from(
      new Set((models ?? []).map((model) => model.model_provider))
    ).sort((a, b) => a.localeCompare(b))
  }, [models])

  const methods = useForm<CreateAgentFormValues>({
    resolver: zodResolver(createAgentSchema),
    defaultValues: {
      name: "",
      model_provider: "",
      model_name: "",
      catalog_id: "",
      description: "",
    },
  })

  const selectedProvider = methods.watch("model_provider")

  const filteredModels = useMemo(() => {
    if (!models || !selectedProvider) return []
    return models.filter((model) => model.model_provider === selectedProvider)
  }, [models, selectedProvider])

  const handleSubmit = async (values: CreateAgentFormValues) => {
    try {
      const preset = await createAgentPreset({
        name: values.name,
        model_provider: values.model_provider,
        model_name: values.model_name,
        catalog_id: values.catalog_id || undefined,
        description: values.description || undefined,
      })
      methods.reset()
      onOpenChange(false)
      router.push(
        `/workspaces/${workspaceId}/agents/${preset.id}?tab=assistant`
      )
    } catch (error) {
      console.error("Failed to create agent:", error)
    }
  }

  return (
    <DialogContent className="max-h-[calc(100vh-2rem)] overflow-y-auto sm:max-w-lg">
      <DialogHeader>
        <DialogTitle>Create agent</DialogTitle>
        <DialogDescription>
          Configure a new agent with a name, model, and optional description.
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
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              <FormField
                control={methods.control}
                name="model_provider"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm">Provider</FormLabel>
                    <Select
                      value={field.value}
                      onValueChange={(value) => {
                        field.onChange(value)
                        methods.setValue("model_name", "")
                        methods.setValue("catalog_id", "")
                      }}
                    >
                      <FormControl>
                        <SelectTrigger className="min-w-0 text-sm">
                          <SelectValue placeholder="Select provider" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {providers.map((provider) => (
                          <SelectItem key={provider} value={provider}>
                            {provider}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={methods.control}
                name="model_name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm">Model</FormLabel>
                    <Select
                      value={methods.watch("catalog_id") || ""}
                      onValueChange={(catalogId) => {
                        const selectedModel = filteredModels.find(
                          (model) => model.id === catalogId
                        )
                        methods.setValue("catalog_id", catalogId)
                        if (selectedModel) {
                          field.onChange(selectedModel.model_name)
                          methods.setValue(
                            "model_provider",
                            selectedModel.model_provider
                          )
                        }
                      }}
                      disabled={!selectedProvider || modelsLoading}
                    >
                      <FormControl>
                        <SelectTrigger className="min-w-0 text-sm">
                          <SelectValue placeholder="Select model" />
                        </SelectTrigger>
                      </FormControl>
                      <SelectContent>
                        {filteredModels.map((model) => (
                          <SelectItem key={model.id} value={model.id}>
                            {model.model_name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
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
                disabled={createAgentPresetIsPending || modelsLoading}
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
