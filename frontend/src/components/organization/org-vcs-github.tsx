"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  CheckCircle2Icon,
  ExternalLinkIcon,
  GithubIcon,
  Trash2Icon,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { GitHubAppManualForm } from "@/components/organization/org-vcs-github-manual-form"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useToast } from "@/components/ui/use-toast"
import {
  useDeleteGitHubAppCredentials,
  useGitHubAppCredentialsStatus,
  useGitHubAppManifest,
} from "@/lib/hooks"

const gitHubAppFormSchema = z.object({
  organizationName: z
    .string()
    .min(1, "Organization name is required")
    .max(100, "Organization name must be less than 100 characters"),
  githubHost: z
    .string()
    .url("Please enter a valid URL")
    .refine(
      (url) => {
        try {
          const urlObj = new URL(url)
          if (urlObj.protocol !== "https:") {
            return false
          }
          const allowedHosts = ["github.com", "www.github.com"]
          const isGitHubEnterprise =
            urlObj.hostname.endsWith(".github.com") ||
            urlObj.hostname.match(/^github\.[a-zA-Z0-9.-]+$/)
          return allowedHosts.includes(urlObj.hostname) || isGitHubEnterprise
        } catch {
          return false
        }
      },
      {
        message:
          "Must be a valid HTTPS GitHub URL (github.com or GitHub Enterprise)",
      }
    )
    .default("https://github.com"),
})

type GitHubAppFormData = z.infer<typeof gitHubAppFormSchema>

export function GitHubAppSetup() {
  const { manifest } = useGitHubAppManifest()
  const {
    credentialsStatus,
    credentialsStatusIsLoading,
    refetchCredentialsStatus,
  } = useGitHubAppCredentialsStatus()
  const { deleteCredentials } = useDeleteGitHubAppCredentials()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const { toast } = useToast()

  // Check if user just returned from successful GitHub App setup
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search)
    if (urlParams.has("setup_success")) {
      toast({
        title: "GitHub App configured successfully!",
        description: "Your GitHub App has been created and installed.",
      })
      window.history.replaceState({}, document.title, window.location.pathname)
    }
  }, [])

  const isConfigured = credentialsStatus?.exists ?? false

  const handleFormSuccess = () => {
    setDialogOpen(false)
    refetchCredentialsStatus()
    toast({
      title: "GitHub App configured successfully!",
      description: "Your GitHub App credentials have been saved.",
    })
  }

  const handleDelete = async () => {
    try {
      await deleteCredentials.mutateAsync()
      setDeleteDialogOpen(false)
      toast({
        title: "GitHub App credentials deleted",
        description: "Workflow sync has been disconnected.",
      })
    } catch (error) {
      console.error("Failed to delete GitHub App credentials:", error)
      toast({
        title: "Error",
        description:
          error instanceof Error
            ? error.message
            : "Failed to delete credentials",
        variant: "destructive",
      })
    }
  }

  if (credentialsStatusIsLoading) {
    return <CenteredSpinner />
  }

  return (
    <>
      <div className="flex items-center justify-between rounded-lg border p-4">
        <div className="flex items-center gap-3">
          {isConfigured ? (
            <CheckCircle2Icon className="size-5 text-green-500" />
          ) : (
            <GithubIcon className="size-5 text-muted-foreground" />
          )}
          <div>
            <p className="text-sm font-medium">GitHub</p>
            <p className="text-xs text-muted-foreground">
              {isConfigured ? (
                <>
                  App ID: {credentialsStatus?.app_id}
                  {credentialsStatus?.has_webhook_secret
                    ? ` · Webhook secret: ${credentialsStatus.webhook_secret_preview}`
                    : " · Webhook secret: not set"}
                  {credentialsStatus?.client_id
                    ? ` · Client ID: ${credentialsStatus.client_id}`
                    : " · Client ID: not set"}
                </>
              ) : (
                "Not connected"
              )}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          {isConfigured ? (
            <>
              <Button
                variant="outline"
                size="sm"
                onClick={() => setDialogOpen(true)}
              >
                Update
              </Button>
              <Button
                variant="destructive"
                size="sm"
                onClick={() => setDeleteDialogOpen(true)}
              >
                <Trash2Icon className="size-3.5" />
              </Button>
            </>
          ) : (
            <Button size="sm" onClick={() => setDialogOpen(true)}>
              Connect
            </Button>
          )}
        </div>
      </div>

      <GitHubConnectionDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        manifest={manifest}
        onFormSuccess={handleFormSuccess}
        existingAppId={
          isConfigured ? credentialsStatus?.app_id || undefined : undefined
        }
      />

      <AlertDialog open={deleteDialogOpen} onOpenChange={setDeleteDialogOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete GitHub App credentials</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the GitHub App credentials? This
              will disable workflow synchronization with your Git repositories.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteCredentials.isPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              disabled={deleteCredentials.isPending}
              className="bg-destructive hover:bg-destructive/90"
            >
              {deleteCredentials.isPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}

function GitHubConnectionDialog({
  open,
  onOpenChange,
  manifest,
  onFormSuccess,
  existingAppId,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  manifest: { manifest: Record<string, unknown> } | undefined
  onFormSuccess: () => void
  existingAppId?: string
}) {
  const { toast } = useToast()

  const form = useForm<GitHubAppFormData>({
    resolver: zodResolver(gitHubAppFormSchema),
    defaultValues: {
      organizationName: "My Organization",
      githubHost: "https://github.com",
    },
  })

  const onSubmit = async (data: GitHubAppFormData) => {
    if (!manifest?.manifest) {
      toast({
        title: "Error",
        description: "No manifest available to submit",
        variant: "destructive",
      })
      return
    }

    try {
      const urlObj = new URL(data.githubHost)
      if (urlObj.protocol !== "https:") {
        throw new Error("HTTPS required")
      }
    } catch {
      toast({
        title: "Invalid GitHub host",
        description: "Please use a valid HTTPS GitHub URL",
        variant: "destructive",
      })
      return
    }

    const formEl = document.createElement("form")
    formEl.method = "POST"
    formEl.target = "_blank"

    const targetUrl = new URL(
      `/organizations/${encodeURIComponent(data.organizationName)}/settings/apps/new`,
      data.githubHost
    )
    formEl.action = targetUrl.toString()

    const manifestInput = document.createElement("input")
    manifestInput.type = "hidden"
    manifestInput.name = "manifest"
    manifestInput.setAttribute("value", JSON.stringify(manifest.manifest))
    formEl.appendChild(manifestInput)

    if (data.githubHost !== "https://github.com") {
      const orgInput = document.createElement("input")
      orgInput.type = "hidden"
      orgInput.name = "organization"
      orgInput.setAttribute("value", data.organizationName)
      formEl.appendChild(orgInput)
    }

    document.body.appendChild(formEl)
    formEl.submit()
    document.body.removeChild(formEl)

    toast({
      title: "Redirecting to GitHub",
      description: "Opening GitHub App creation page in new tab",
    })
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>
            {existingAppId ? "Update GitHub App" : "Connect GitHub App"}
          </DialogTitle>
        </DialogHeader>
        <Tabs defaultValue="create" className="w-full">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="create" disableUnderline>
              Create new
            </TabsTrigger>
            <TabsTrigger value="existing" disableUnderline>
              Use existing
            </TabsTrigger>
          </TabsList>
          <TabsContent value="create" className="space-y-4 pt-2">
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onSubmit)}
                className="space-y-4"
              >
                <FormField
                  control={form.control}
                  name="organizationName"
                  render={({ field }) => (
                    <FormItem className="space-y-2">
                      <FormLabel>Organization name</FormLabel>
                      <FormControl>
                        <Input {...field} placeholder="My Organization" />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="githubHost"
                  render={({ field }) => (
                    <FormItem className="space-y-2">
                      <FormLabel>GitHub host URL</FormLabel>
                      <FormControl>
                        <Input {...field} placeholder="https://github.com" />
                      </FormControl>
                      <p className="text-xs text-muted-foreground">
                        For GitHub Enterprise, enter your instance URL
                      </p>
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <Button
                  type="submit"
                  className="gap-2"
                  disabled={!manifest?.manifest}
                >
                  <ExternalLinkIcon className="size-4" />
                  Create GitHub App
                </Button>
              </form>
            </Form>
          </TabsContent>
          <TabsContent value="existing" className="pt-2">
            <GitHubAppManualForm
              onSuccess={onFormSuccess}
              existingAppId={existingAppId}
            />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}
