"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { SearchIcon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { CaseReadMinimal, PromptRunRequest } from "@/client"
import { ApiError } from "@/client"
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
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { useListPrompts, useRunPrompt } from "@/hooks/use-prompt"
import { useWorkspace } from "@/providers/workspace"

const promptSelectionSchema = z.object({
  promptId: z.string().min(1, "Please select a prompt"),
})

type PromptSelectionSchema = z.infer<typeof promptSelectionSchema>

interface PromptSelectionDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedCases: CaseReadMinimal[]
  onSuccess?: () => void
}

export function PromptSelectionDialog({
  open,
  onOpenChange,
  selectedCases,
  onSuccess,
}: PromptSelectionDialogProps) {
  const { workspaceId } = useWorkspace()
  const [searchTerm, setSearchTerm] = useState("")

  const {
    data: prompts,
    isLoading: promptsLoading,
    error: promptsError,
  } = useListPrompts({ workspaceId })

  const { runPrompt, runPromptPending } = useRunPrompt(workspaceId)

  const form = useForm<PromptSelectionSchema>({
    resolver: zodResolver(promptSelectionSchema),
    defaultValues: {
      promptId: "",
    },
    mode: "onSubmit",
  })

  const filteredPrompts =
    prompts?.filter(
      (prompt) =>
        prompt.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
        prompt.content.toLowerCase().includes(searchTerm.toLowerCase())
    ) || []

  const selectedPrompt = prompts?.find(
    (prompt) => prompt.id === form.watch("promptId")
  )

  const onSubmit = async (data: PromptSelectionSchema) => {
    try {
      const caseIds = selectedCases.map((c) => c.id)

      const request: PromptRunRequest = {
        entities: caseIds.map((caseId) => ({
          entity_id: caseId,
          entity_type: "case",
        })),
      }

      await runPrompt({
        promptId: data.promptId,
        request,
      })

      onOpenChange(false)
      form.reset()
      onSuccess?.()
    } catch (error) {
      if (error instanceof ApiError) {
        form.setError("root", {
          type: "manual",
          message: error.message,
        })
      } else {
        form.setError("root", {
          type: "manual",
          message:
            error instanceof Error ? error.message : "Failed to run prompt",
        })
        console.error("Error running prompt:", error)
      }
    }
  }

  const handleClose = () => {
    onOpenChange(false)
    form.reset()
    setSearchTerm("")
  }

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-[800px]">
        <DialogHeader>
          <DialogTitle>Run Prompt on Selected Cases</DialogTitle>
          <DialogDescription>
            Select a prompt to run on {selectedCases.length} selected case
            {selectedCases.length !== 1 ? "s" : ""}.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <div className="space-y-4">
              <div className="relative">
                <SearchIcon className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search prompts..."
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  className="pl-9"
                />
              </div>

              <FormField
                control={form.control}
                name="promptId"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Available Prompts</FormLabel>
                    <FormControl>
                      {promptsLoading ? (
                        <div className="space-y-2">
                          <Skeleton className="h-10 w-full" />
                          <Skeleton className="h-20 w-full" />
                        </div>
                      ) : promptsError ? (
                        <div className="text-sm text-red-500">
                          Failed to load prompts
                        </div>
                      ) : filteredPrompts.length === 0 ? (
                        <div className="text-sm text-muted-foreground">
                          {searchTerm
                            ? "No prompts match your search"
                            : "No prompts available"}
                        </div>
                      ) : (
                        <Select
                          value={field.value}
                          onValueChange={field.onChange}
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="Select a prompt" />
                          </SelectTrigger>
                          <SelectContent>
                            <ScrollArea className="h-[200px]">
                              {filteredPrompts.map((prompt) => (
                                <SelectItem key={prompt.id} value={prompt.id}>
                                  <div className="flex flex-col items-start">
                                    <div className="font-medium">
                                      {prompt.title}
                                    </div>
                                    <div className="text-xs text-muted-foreground line-clamp-2">
                                      {prompt.content.slice(0, 100)}
                                      {prompt.content.length > 100 ? "..." : ""}
                                    </div>
                                  </div>
                                </SelectItem>
                              ))}
                            </ScrollArea>
                          </SelectContent>
                        </Select>
                      )}
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {selectedPrompt && (
                <div className="rounded-md border p-4 space-y-2">
                  <h4 className="font-medium">Selected Prompt</h4>
                  <div className="text-sm">
                    <div className="font-medium">{selectedPrompt.title}</div>
                    <div className="text-muted-foreground mt-1">
                      {selectedPrompt.tools?.length || 0} tool(s) available
                    </div>
                  </div>
                  <div className="text-xs text-muted-foreground">
                    Created:{" "}
                    {new Date(selectedPrompt.created_at).toLocaleDateString()}
                  </div>
                </div>
              )}

              <div className="rounded-md border p-4">
                <h4 className="font-medium mb-2">Selected Cases</h4>
                <div className="space-y-1 max-h-[120px] overflow-y-auto">
                  {selectedCases.map((case_) => (
                    <div key={case_.id} className="text-sm">
                      <span className="font-medium">{case_.short_id}</span>
                      <span className="text-muted-foreground ml-2">
                        {case_.summary}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>

            {form.formState.errors.root && (
              <div className="text-sm text-red-500">
                {form.formState.errors.root.message}
              </div>
            )}

            <DialogFooter>
              <Button type="button" variant="outline" onClick={handleClose}>
                Cancel
              </Button>
              <Button
                type="submit"
                disabled={runPromptPending || !selectedPrompt}
              >
                {runPromptPending ? "Running..." : "Run Prompt"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
