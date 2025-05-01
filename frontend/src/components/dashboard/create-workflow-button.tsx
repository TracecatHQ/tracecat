"use client"

import { useState } from "react"
import { useRouter } from "next/navigation"
import { ApiError } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import {
  BracesIcon,
  ChevronDownIcon,
  FolderIcon,
  PlusCircleIcon,
} from "lucide-react"
import { useForm } from "react-hook-form"
import YAML from "yaml"
import { z } from "zod"

import { TracecatApiError } from "@/lib/errors"
import { useFolders, useWorkflowManager } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
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

const importFormSchema = z.object({
  file: z.instanceof(File).refine((file) => file.size <= 5000000, {
    message: "File size must be less than 5MB.",
  }),
  use_workflow_id: z.boolean().default(false),
})

type ImportFormValues = z.infer<typeof importFormSchema>

function ImportWorkflowDialog({
  open,
  onOpenChange,
  workspaceId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
}) {
  const router = useRouter()
  const [validationErrors, setValidationErrors] = useState<string | null>(null)
  const { createWorkflow } = useWorkflowManager()

  const form = useForm<ImportFormValues>({
    resolver: zodResolver(importFormSchema),
    defaultValues: {
      use_workflow_id: false,
    },
  })

  const onSubmit = async (data: ImportFormValues) => {
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
      router.push(`/workspaces/${workspaceId}/workflows/${response.id}`)
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
    <Dialog open={open} onOpenChange={onOpenChange}>
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

const folderFormSchema = z.object({
  name: z.string().min(1, "Folder name is required"),
})

type FolderFormValues = z.infer<typeof folderFormSchema>

function CreateFolderDialog({
  open,
  onOpenChange,
  workspaceId,
  currentFolderPath,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
  currentFolderPath: string | null
}) {
  const { createFolder } = useFolders(workspaceId)

  const form = useForm<FolderFormValues>({
    resolver: zodResolver(folderFormSchema),
    defaultValues: {
      name: "",
    },
  })

  const handleCreateFolder = async (data: FolderFormValues) => {
    try {
      await createFolder({
        name: data.name.trim(),
        parent_path: currentFolderPath || "/",
      })
      form.reset()
      onOpenChange(false)
    } catch (error) {
      console.log("Error creating folder:", error)
      form.setError("name", {
        message: "Folder already exists or another error occurred.",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Create new folder</DialogTitle>
          <DialogDescription>
            Create a new folder to organize your workflows.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleCreateFolder)}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Folder name</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="Enter folder name"
                      {...field}
                      autoFocus
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <DialogFooter>
              <Button
                variant="outline"
                type="button"
                onClick={() => {
                  form.reset()
                  onOpenChange(false)
                }}
              >
                Cancel
              </Button>
              <Button type="submit">Create</Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

// Main Create Workflow Button Component
export function CreateWorkflowButton({
  view,
  currentFolderPath,
}: {
  view: "default" | "folders"
  currentFolderPath: string | null
}) {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const { createWorkflow, moveWorkflow } = useWorkflowManager()
  const [importDialogOpen, setImportDialogOpen] = useState(false)
  const [folderDialogOpen, setFolderDialogOpen] = useState(false)

  const handleCreateWorkflow = async () => {
    try {
      const response = await createWorkflow({ workspaceId })
      await moveWorkflow({
        workflowId: response.id,
        workspaceId,
        requestBody: {
          folder_path: currentFolderPath,
        },
      })

      router.push(`/workspaces/${workspaceId}/workflows/${response.id}`)
    } catch (error) {
      console.log("Error creating workflow:", error)
    }
  }

  return (
    <>
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
          <DropdownMenuItem onSelect={() => setImportDialogOpen(true)}>
            <BracesIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>From YAML / JSON</span>
              <span className="text-xs text-muted-foreground">
                Import a workflow file
              </span>
            </div>
          </DropdownMenuItem>
          {view === "folders" && (
            <DropdownMenuItem onSelect={() => setFolderDialogOpen(true)}>
              <FolderIcon className="size-4 text-foreground/80" />
              <div className="flex flex-col text-xs">
                <span>Folder</span>
                <span className="text-xs text-muted-foreground">
                  Create a new folder
                </span>
              </div>
            </DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>

      <ImportWorkflowDialog
        open={importDialogOpen}
        onOpenChange={setImportDialogOpen}
        workspaceId={workspaceId}
      />

      <CreateFolderDialog
        open={folderDialogOpen}
        onOpenChange={setFolderDialogOpen}
        workspaceId={workspaceId}
        currentFolderPath={currentFolderPath}
      />
    </>
  )
}
