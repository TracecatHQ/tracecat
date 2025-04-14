"use client"

import { useRouter } from "next/navigation"
import { workflowsCreateWorkflow } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { BracesIcon, ChevronDownIcon, UserCircle2Icon } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

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

const formSchema = z.object({
  file: z.instanceof(File).refine((file) => file.size <= 5000000, {
    message: "File size must be less than 5MB.",
  }),
})

type FormValues = z.infer<typeof formSchema>
export function WorkspaceManagementButton() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  const form = useForm<FormValues>({
    resolver: zodResolver(formSchema),
  })
  const handleCreateWorkflow = async () => {
    try {
      const response = await workflowsCreateWorkflow({ workspaceId })
      router.push(`/workflows/${response.id}`)
    } catch (error) {
      console.error("Error creating workflow:", error)
    }
  }

  const onSubmit = async (data: FormValues) => {
    try {
      const response = await workflowsCreateWorkflow({
        workspaceId,
        formData: {
          file: new Blob([data.file], { type: "application/yaml" }),
        },
      })
      router.push(`/workflows/${response.id}`)
    } catch (error) {
      console.error("Error creating workflow:", error)
    }
  }

  return (
    <Dialog>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            role="combobox"
            className="items-center space-x-2 bg-emerald-500 text-white shadow-sm hover:bg-emerald-500"
          >
            <span>Manage Workspace</span>
            <ChevronDownIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent className="w-48 text-xs" align="end">
          <DropdownMenuGroup>
            <DropdownMenuItem
              onClick={async () => await handleCreateWorkflow()}
            >
              Add Member
              <DropdownMenuShortcut>
                <UserCircle2Icon className="size-4" />
              </DropdownMenuShortcut>
            </DropdownMenuItem>
            <DialogTrigger asChild>
              <DropdownMenuItem>
                From YAML
                <DropdownMenuShortcut>
                  <BracesIcon className="size-4" />
                </DropdownMenuShortcut>
              </DropdownMenuItem>
            </DialogTrigger>
          </DropdownMenuGroup>
        </DropdownMenuContent>
      </DropdownMenu>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>Import workflow from file</DialogTitle>
          <DialogDescription>
            Import a workflow from either a YAML file.
          </DialogDescription>
        </DialogHeader>

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
            <FormField
              control={form.control}
              name="file"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>File</FormLabel>
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
      </DialogContent>
    </Dialog>
  )
}
