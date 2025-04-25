"use client"

import React, { useEffect, useState } from "react"
import { usePathname, useRouter } from "next/navigation"
import { ApiError, WorkspaceCreate, WorkspaceReadMinimal } from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"
import { CaretSortIcon, CheckIcon } from "@radix-ui/react-icons"
import { KeyRoundIcon, PlusCircleIcon } from "lucide-react"
import { useForm } from "react-hook-form"

import { useWorkspaceManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
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
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

export function WorkspaceSelector(props: React.HTMLAttributes<HTMLElement>) {
  const { user } = useAuth()
  const isAdmin = user?.is_superuser || user?.role === "admin"
  const { workspaceId, workspaceLoading, workspaceError } = useWorkspace()
  const { workspaces, workspacesError, workspacesLoading, setLastWorkspaceId } =
    useWorkspaceManager()
  const [open, setOpen] = useState(false)
  const [currWorkspace, setCurrWorkspace] = useState<
    WorkspaceReadMinimal | undefined
  >()
  const pathname = usePathname()
  const router = useRouter()
  const [createWorkspaceDialogOpen, setCreateWorkspaceDialogOpen] =
    useState(false)

  useEffect(() => {
    if (workspaceId) {
      setCurrWorkspace(workspaces?.find((ws) => ws.id === workspaceId))
      setLastWorkspaceId(workspaceId)
    }
  }, [workspaceId, workspaces])

  if (workspacesLoading || workspaceLoading) {
    return <div>...</div>
  }
  if (workspacesError || workspaceError) {
    console.error(
      "Selector: Error loading workspaces",
      workspacesError || workspaceError
    )
    throw workspaceError || workspacesError
  }

  return (
    <Dialog
      open={createWorkspaceDialogOpen}
      onOpenChange={setCreateWorkspaceDialogOpen}
    >
      <Popover open={open} onOpenChange={setOpen} {...props}>
        <PopoverTrigger asChild>
          <Button
            variant="outline"
            role="combobox"
            aria-label="Load a workspace..."
            aria-expanded={open}
            className="flex-1 justify-between md:min-w-[150px] md:max-w-[200px] lg:min-w-[250px] lg:max-w-[300px]"
          >
            {currWorkspace?.name || "Select a workspace..."}
            <CaretSortIcon className="ml-2 size-4 shrink-0 opacity-50" />
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-[300px] p-0" align="start">
          <Command>
            <CommandInput placeholder="Search workspaces..." />
            <CommandList>
              <CommandEmpty>No workspaces found.</CommandEmpty>
              <CommandGroup heading="My Workspaces">
                {workspaces?.map((ws) => (
                  <CommandItem
                    key={ws.id}
                    onSelect={() => {
                      setCurrWorkspace(ws)
                      // replace /workspaces/<ws-id>/... with /workspaces/<new-ws-id>/...
                      const newPath = pathname.replace(
                        /\/workspaces\/[^/]+/,
                        `/workspaces/${ws.id}`
                      )
                      router.push(newPath)
                      setOpen(false)
                    }}
                  >
                    {ws.name}
                    <CheckIcon
                      className={cn(
                        "ml-auto size-4",
                        currWorkspace?.id === ws.id
                          ? "opacity-100"
                          : "opacity-0"
                      )}
                    />
                  </CommandItem>
                ))}
              </CommandGroup>
              {isAdmin && (
                <>
                  <CommandSeparator />
                  <CommandGroup heading="Management">
                    <DialogTrigger asChild>
                      <CommandItem
                        className="flex items-center"
                        key="add-workspace"
                        onSelect={() => {
                          setOpen(false)
                          setCreateWorkspaceDialogOpen(true)
                        }}
                      >
                        <PlusCircleIcon className="mr-2 size-4" />
                        Add Workspace
                      </CommandItem>
                    </DialogTrigger>
                  </CommandGroup>
                </>
              )}
            </CommandList>
          </Command>
        </PopoverContent>
      </Popover>
      <CreateWorkspaceForm setOpen={setCreateWorkspaceDialogOpen} />
    </Dialog>
  )
}
function CreateWorkspaceForm({
  setOpen,
}: {
  setOpen: (open: boolean) => void
}) {
  const { createWorkspace } = useWorkspaceManager()
  const methods = useForm<WorkspaceCreate>({
    defaultValues: {
      name: "",
    },
  })

  const onSubmit = async (values: WorkspaceCreate) => {
    console.log("Creating workspace", values)
    try {
      await createWorkspace(values)
      console.log("Workspace created")
      setOpen(false)
    } catch (error) {
      if (error instanceof ApiError) {
        methods.setError("name", {
          type: "manual",
          message:
            error.status === 409
              ? "A workspace with this name already exists."
              : error.message,
        })
      } else {
        console.error("Error creating workspace", error)
        methods.setError("name", {
          type: "manual",
          message: (error as Error).message || "Could not create workspace.",
        })
      }
    }
  }
  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Create a new workspace</DialogTitle>
        <DialogDescription>
          Provide a name for the new workspace.
        </DialogDescription>
      </DialogHeader>
      <Form {...methods}>
        <form onSubmit={methods.handleSubmit(onSubmit)}>
          <div className="space-y-4">
            <FormField
              key="name"
              control={methods.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel className="text-sm">Name</FormLabel>
                  <FormControl>
                    <Input
                      className="text-sm"
                      placeholder="Workspace Name"
                      {...field}
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            <DialogFooter>
              <Button className="ml-auto space-x-2" type="submit">
                <KeyRoundIcon className="mr-2 size-4" />
                Create
              </Button>
            </DialogFooter>
          </div>
        </form>
      </Form>
    </DialogContent>
  )
}
