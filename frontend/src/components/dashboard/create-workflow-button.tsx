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
  DropdownMenuShortcut,
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
})

type FormValues = z.infer<typeof formSchema>
export function CreateWorkflowButton() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { createWorkflow } = useWorkflowManager()
  const [validationErrors, setValidationErrors] = useState<string | null>(null)
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
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
      const { file } = data
      const contentType = file.type
      const response = await createWorkflow({
        workspaceId,
        formData: {
          file: new Blob([file], { type: contentType }),
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
              YAML.stringify(YAML.parse(apiError.body.detail))
            )
            break
          case 409:
            console.error("Conflict:", apiError)
            form.setError("file", {
              message: `A workflow with this workflow ID already exists.
              Please either delete the existing workflow, remove the ID from the file, or upload a file with a different ID.`,
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
            className="items-center space-x-2 bg-emerald-500 tracking-wide text-white shadow-sm hover:bg-emerald-500"
          >
            <span>Create new</span>
            <ChevronDownIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-48" align="end">
          <DropdownMenuGroup>
            <DropdownMenuItem
              onClick={async () => await handleCreateWorkflow()}
            >
              Workflow
              <DropdownMenuShortcut>
                <PlusCircleIcon className="size-4" />
              </DropdownMenuShortcut>
            </DropdownMenuItem>
            <DialogTrigger asChild>
              <DropdownMenuItem>
                From YAML / JSON
                <DropdownMenuShortcut>
                  <BracesIcon className="size-4" />
                </DropdownMenuShortcut>
              </DropdownMenuItem>
            </DialogTrigger>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <DialogContent className="sm:max-w-[600px]">
        <DialogHeader>
          <DialogTitle>Import workflow from file</DialogTitle>
          <DialogDescription>
            Import a workflow from either a YAML or JSON file.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
            <FormField
              control={form.control}
              name="file"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>File Upload</FormLabel>
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
                  <FormDescription>Upload a file (max 5MB)</FormDescription>
                  <FormMessage />
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
