"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { ApiError } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { BracesIcon, ChevronDownIcon, PlusCircleIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import YAML from "yaml"
import { z } from "zod"

import { TracecatApiError } from "@/lib/errors"
import { useWorkflowManager } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
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
import { Separator } from "@/components/ui/separator"

const formSchema = z.object({
  file: z.instanceof(File).refine((file) => file.size <= 5000000, {
    message: "File size must be less than 5MB.",
  }),
  use_workflow_id: z.boolean().default(false),
})

type FormValues = z.infer<typeof formSchema>
export function CreateWorkflowButton() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { createWorkflow } = useWorkflowManager()
  const [validationErrors, setValidationErrors] = useState<string | null>(null)
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      use_workflow_id: false,
    },
  })
  const workspaceUrl = `/workspaces/${workspaceId}/workflows`
  const handleCreateWorkflow = async () => {
    try {
      const response = await createWorkflow({ workspaceId })
      router.push(`${workspaceUrl}/${response.id}`)
    } catch (error) {
      console.error("Error creating workflow:", error)
    }
  }

  const onSubmit = async (data: FormValues) => {
    try {
      const { file, use_workflow_id } = data
      const contentType = file.type
      const response = await createWorkflow({
        workspaceId,
        formData: {
          file: new Blob([file], { type: contentType }),
          use_workflow_id,
        },
      })
      router.push(`${workspaceUrl}/${response.id}`)
    } catch (error) {
      if (error instanceof ApiError) {
        const apiError = error as TracecatApiError
        switch (apiError.status) {
          case 400:
            console.error("Bad request:", apiError)
            form.setError("file", {
              message: "The uploaded workflow YAML / JSON is invalid.",
            })

            setValidationErrors(
              YAML.stringify(YAML.parse(String(apiError.body.detail)))
            )
            break
          case 409:
            console.error("Conflict:", apiError)
            form.setError("file", {
              message: `A workflow with this workflow ID already exists.
              Please either uncheck "Use workflow ID from file", delete the existing workflow, remove the ID from the file, or upload a file with a different ID.`,
            })
            break
          default:
            console.error("Unexpected error:", apiError)
        }
      } else {
        console.error("Unexpected error:", error)
      }
    }
  }

  return (
    <Dialog>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            role="combobox"
            className="h-7 items-center space-x-1 bg-emerald-500/80 px-3 py-1 text-xs text-white shadow-sm hover:border-emerald-500 hover:bg-emerald-400/80"
          >
            <ChevronDownIcon className="size-3" />
            <span>Create new</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          className="
            [&_[data-radix-collection-item]]:flex
            [&_[data-radix-collection-item]]:items-center
            [&_[data-radix-collection-item]]:gap-2
          "
        >
          <DropdownMenuItem onSelect={handleCreateWorkflow}>
            <PlusCircleIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Workflow</span>
              <span className="text-xs text-muted-foreground">
                Start from scratch
              </span>
            </div>
          </DropdownMenuItem>
          <DialogTrigger asChild>
            <DropdownMenuItem>
              <BracesIcon className="size-4 text-foreground/80" />
              <div className="flex flex-col text-xs">
                <span>From YAML / JSON</span>
                <span className="text-xs text-muted-foreground">
                  Import a workflow file
                </span>
              </div>
            </DropdownMenuItem>
          </DialogTrigger>
          <DropdownMenuGroup></DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>Import workflow from file</DialogTitle>
          <DialogDescription>
            Import a workflow from a YAML or JSON file.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
            <FormField
              control={form.control}
              name="file"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>YAML / JSON</FormLabel>
                  <FormControl>
                    <Input
                      type="file"
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        if (file) {
                          field.onChange(file)
                        }
                      }}
                    />
                  </FormControl>
                  <FormDescription>Upload file (max 5MB)</FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="use_workflow_id"
              render={({ field }) => (
                <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4">
                  <FormControl>
                    <Checkbox
                      checked={field.value}
                      onCheckedChange={field.onChange}
                    />
                  </FormControl>
                  <div className="space-y-1 leading-none">
                    <FormLabel>Use workflow ID from file</FormLabel>
                    <FormDescription>
                      When checked, the system will use the workflow ID provided
                      in the YAML/JSON file. If not checked, a new ID will be
                      generated.
                    </FormDescription>
                  </div>
                </FormItem>
              )}
            />

            <Button type="submit">Upload</Button>
          </form>
        </Form>
        {validationErrors && (
          <div className="flex h-36 flex-col space-y-2 overflow-auto rounded-md border border-rose-500 bg-rose-100 p-2 font-mono text-xs text-rose-600">
            <span className="font-semibold">Validation errors</span>
            <Separator className="bg-rose-400" />
            <pre>{validationErrors}</pre>
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}
