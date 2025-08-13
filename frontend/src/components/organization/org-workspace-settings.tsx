"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { KeyRoundIcon, TrashIcon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  type SecretCreate,
  type SecretReadMinimal,
  secretsListSecrets,
  type WorkspaceRead,
  type WorkspaceUpdate,
  workspacesDeleteWorkspace,
  workspacesUpdateWorkspace,
} from "@/client"
import {
  CreateSSHKeyDialog,
  CreateSSHKeyDialogTrigger,
} from "@/components/ssh-keys/ssh-key-create-dialog"
import { CustomTagInput } from "@/components/tags-input"
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
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceSecrets } from "@/lib/hooks"
import { OrgWorkspaceDeleteDialog } from "./org-workspace-delete-dialog"
import { OrgWorkspaceSSHKeyDeleteDialog } from "./org-workspace-ssh-key-delete-dialog"

const workspaceSettingsSchema = z.object({
  name: z.string().min(1, "Workspace name is required"),
  git_repo_url: z
    .string()
    .nullish()
    .refine(
      (url) => !url || /^git\+ssh:\/\/git@[^/]+\/[^/]+\/[^/@]+\.git$/.test(url),
      "Must be a valid Git SSH URL in format: git+ssh://git@host/org/repo.git"
    ),
  git_allowed_domains: z.array(
    z.object({
      id: z.string(),
      text: z.string().min(1, "Cannot be empty"),
    })
  ),
})

type WorkspaceSettingsForm = z.infer<typeof workspaceSettingsSchema>

interface OrgWorkspaceSettingsProps {
  workspace: WorkspaceRead
  onWorkspaceDeleted?: () => void
}

export function OrgWorkspaceSettings({
  workspace,
  onWorkspaceDeleted,
}: OrgWorkspaceSettingsProps) {
  const queryClient = useQueryClient()
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [sshKeyToDelete, setSSHKeyToDelete] =
    useState<SecretReadMinimal | null>(null)
  const { createSecret, deleteSecretById } = useWorkspaceSecrets(workspace.id)

  // Fetch SSH keys for this workspace
  const { data: sshKeys, isLoading: sshKeysLoading } = useQuery<
    SecretReadMinimal[]
  >({
    queryKey: ["workspace-ssh-keys", workspace.id],
    queryFn: async () =>
      await secretsListSecrets({
        workspaceId: workspace.id,
        type: ["ssh-key"],
      }),
  })

  const form = useForm<WorkspaceSettingsForm>({
    resolver: zodResolver(workspaceSettingsSchema),
    defaultValues: {
      name: workspace.name,
      git_repo_url: workspace.settings?.git_repo_url || "",
      git_allowed_domains: workspace.settings?.git_allowed_domains
        ? workspace.settings.git_allowed_domains
            .split(",")
            .map((domain, index) => ({
              id: index.toString(),
              text: domain.trim(),
            }))
        : [{ id: "0", text: "github.com" }],
    },
  })

  const { mutateAsync: updateWorkspace, isPending: isUpdating } = useMutation({
    mutationFn: async (params: WorkspaceUpdate) => {
      return await workspacesUpdateWorkspace({
        workspaceId: workspace.id,
        requestBody: params,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspace", workspace.id] })
      queryClient.invalidateQueries({ queryKey: ["workspaces"] })
      toast({
        title: "Workspace updated",
        description: "The workspace name has been updated successfully.",
      })
    },
    onError: (error) => {
      console.error("Failed to update workspace", error)
      toast({
        title: "Error updating workspace",
        description: "Failed to update the workspace. Please try again.",
        variant: "destructive",
      })
    },
  })

  const { mutateAsync: deleteWorkspace, isPending: isDeleting } = useMutation({
    mutationFn: async () => {
      return await workspacesDeleteWorkspace({
        workspaceId: workspace.id,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["workspaces"] })
      toast({
        title: "Workspace deleted",
        description: "The workspace has been deleted successfully.",
      })
      onWorkspaceDeleted?.()
    },
    onError: (error) => {
      console.error("Failed to delete workspace", error)
      toast({
        title: "Error deleting workspace",
        description: "Failed to delete the workspace. Please try again.",
        variant: "destructive",
      })
    },
  })

  const onSubmit = async (values: WorkspaceSettingsForm) => {
    const settings: Record<string, string> = {
      ...workspace.settings,
      git_allowed_domains: values.git_allowed_domains
        .map((domain) => domain.text)
        .join(","),
    }

    // Only add git_repo_url if it has a value
    if (values.git_repo_url) {
      settings.git_repo_url = values.git_repo_url
    }

    await updateWorkspace({
      name: values.name,
      settings,
    })
  }

  const handleDeleteWorkspace = async () => {
    await deleteWorkspace()
    setDeleteDialogOpen(false)
  }

  const handleCreateWorkspaceSSHKey = async (secret: SecretCreate) => {
    await createSecret(secret)
    // Invalidate SSH keys query to refresh the list
    queryClient.invalidateQueries({
      queryKey: ["workspace-ssh-keys", workspace.id],
    })
  }

  const handleDeleteSSHKey = async () => {
    if (!sshKeyToDelete) return
    try {
      await deleteSecretById(sshKeyToDelete)
      // Invalidate SSH keys query to refresh the list
      queryClient.invalidateQueries({
        queryKey: ["workspace-ssh-keys", workspace.id],
      })
      setSSHKeyToDelete(null)
    } catch (error) {
      console.error("Failed to delete SSH key:", error)
    }
  }

  return (
    <div className="space-y-8">
      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-medium">Workspace name</h3>
          <p className="text-sm text-muted-foreground">
            Change the name of this workspace.
          </p>
        </div>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
            <FormField
              control={form.control}
              name="name"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Name</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder="Enter workspace name"
                      className="max-w-md"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div>
              <h4 className="text-md font-medium mb-4">
                Git repository settings
              </h4>
              <p className="text-sm text-muted-foreground mb-4">
                Configure the Git repository used to store and sync workflow
                definitions for this workspace.
              </p>
              <div className="space-y-4">
                <FormField
                  control={form.control}
                  name="git_repo_url"
                  render={({ field }) => (
                    <FormItem className="flex flex-col">
                      <FormLabel>Remote repository URL</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="git+ssh://git@my-host/my-org/my-repo.git"
                          {...field}
                          value={field.value ?? ""}
                        />
                      </FormControl>
                      <FormDescription>
                        Git URL of the remote repository. Must use{" "}
                        <span className="font-mono tracking-tighter">
                          git+ssh
                        </span>{" "}
                        scheme.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="git_allowed_domains"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Allowed Git domains</FormLabel>
                      <FormControl>
                        <CustomTagInput
                          {...field}
                          placeholder="Enter a domain..."
                          tags={field.value}
                          setTags={field.onChange}
                        />
                      </FormControl>
                      <FormDescription>
                        Add domains that are allowed for Git operations (e.g.,
                        github.com)
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </div>

            <Button type="submit" disabled={isUpdating}>
              {isUpdating ? "Saving..." : "Save changes"}
            </Button>
          </form>
        </Form>
      </div>

      <Separator />

      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-medium">SSH key management</h3>
          <p className="text-sm text-muted-foreground">
            Manage SSH keys for authenticating with private Git repositories.
          </p>
        </div>

        {/* Display existing SSH keys */}
        {sshKeysLoading ? (
          <div className="text-sm text-muted-foreground">
            Loading SSH keys...
          </div>
        ) : sshKeys && sshKeys.length > 0 ? (
          <div className="space-y-2">
            {sshKeys.map((sshKey) => (
              <div
                key={sshKey.id}
                className="flex items-center justify-between rounded-md border p-3"
              >
                <div className="flex items-center space-x-3">
                  <KeyRoundIcon className="size-4 text-muted-foreground" />
                  <div>
                    <div className="font-medium">{sshKey.name}</div>
                    {sshKey.description && (
                      <div className="text-sm text-muted-foreground">
                        {sshKey.description}
                      </div>
                    )}
                    {sshKey.environment !== "default" && (
                      <div className="text-xs text-muted-foreground">
                        Environment: {sshKey.environment}
                      </div>
                    )}
                  </div>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setSSHKeyToDelete(sshKey)}
                  className="text-destructive hover:text-destructive"
                >
                  <TrashIcon className="size-4" />
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <div className="text-sm text-muted-foreground">
            No SSH keys configured. Create one to authenticate with private Git
            repositories.
          </div>
        )}

        <CreateSSHKeyDialog
          handler={handleCreateWorkspaceSSHKey}
          fieldConfig={{
            name: {
              defaultValue: "store-ssh-key",
              disabled: true,
            },
          }}
        >
          <CreateSSHKeyDialogTrigger asChild>
            <Button variant="outline" className="space-x-2">
              <KeyRoundIcon className="mr-2 size-4" />
              Create SSH key
            </Button>
          </CreateSSHKeyDialogTrigger>
        </CreateSSHKeyDialog>
      </div>

      <Separator />

      <div className="space-y-4">
        <div>
          <h3 className="text-lg font-medium text-destructive">Danger zone</h3>
          <p className="text-sm text-muted-foreground">
            Permanently delete this workspace and all of its data.
          </p>
        </div>
        <div className="rounded-md border border-destructive p-4">
          <div className="flex items-center justify-between">
            <div className="space-y-1">
              <p className="text-sm font-medium">Delete this workspace</p>
              <p className="text-sm text-muted-foreground">
                Once deleted, this workspace and all its data cannot be
                recovered.
              </p>
            </div>
            <Button
              variant="destructive"
              onClick={() => setDeleteDialogOpen(true)}
              disabled={isDeleting}
            >
              Delete workspace
            </Button>
          </div>
        </div>
      </div>

      <OrgWorkspaceDeleteDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        workspaceName={workspace.name}
        onConfirm={handleDeleteWorkspace}
        isDeleting={isDeleting}
      />

      {/* SSH Key Delete Confirmation Dialog */}
      <OrgWorkspaceSSHKeyDeleteDialog
        open={!!sshKeyToDelete}
        onOpenChange={(open) => !open && setSSHKeyToDelete(null)}
        sshKey={sshKeyToDelete}
        onConfirm={handleDeleteSSHKey}
      />
    </div>
  )
}
