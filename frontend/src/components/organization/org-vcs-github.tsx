"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import {
  CheckCircleIcon,
  CopyIcon,
  ExternalLinkIcon,
  GitBranchIcon,
} from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { GitHubAppManualForm } from "@/components/organization/org-vcs-github-manual-form"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
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
import { Textarea } from "@/components/ui/textarea"
import { useToast } from "@/components/ui/use-toast"
import {
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
          // Enforce HTTPS and validate against known GitHub hosts
          if (urlObj.protocol !== "https:") {
            return false
          }
          // Allow github.com and GitHub Enterprise patterns
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
  const { manifest, manifestIsLoading, manifestError } = useGitHubAppManifest()
  const {
    credentialsStatus,
    credentialsStatusIsLoading,
    refetchCredentialsStatus,
  } = useGitHubAppCredentialsStatus()
  const [showSuccessMessage, setShowSuccessMessage] = useState(false)
  const [activeTab, setActiveTab] = useState("manifest")
  const { toast } = useToast()

  const form = useForm<GitHubAppFormData>({
    resolver: zodResolver(gitHubAppFormSchema),
    defaultValues: {
      organizationName: "My Organization",
      githubHost: "https://github.com",
    },
  })

  // Check if user just returned from successful GitHub App setup
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search)
    if (urlParams.has("setup_success")) {
      setShowSuccessMessage(true)
      toast({
        title: "GitHub App configured successfully!",
        description: "Your GitHub App has been created and installed.",
      })
      // Clean up URL
      window.history.replaceState({}, document.title, window.location.pathname)
    }
  }, [])

  // Auto-switch to manual tab if credentials already exist
  useEffect(() => {
    if (credentialsStatus?.exists && activeTab === "manifest") {
      setActiveTab("manual")
    }
  }, [credentialsStatus?.exists, activeTab])

  const copyManifest = () => {
    if (manifest?.manifest) {
      navigator.clipboard.writeText(JSON.stringify(manifest.manifest, null, 2))
      toast({
        title: "Copied to clipboard",
        description: "GitHub App manifest JSON copied to clipboard",
      })
    }
  }

  const onSubmit = async (data: GitHubAppFormData) => {
    if (!manifest?.manifest) {
      toast({
        title: "Error",
        description: "No manifest available to submit",
        variant: "destructive",
      })
      return
    }

    // Additional security validation (redundant with schema but defense in depth)
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

    // Safely create form with proper escaping
    const form = document.createElement("form")
    form.method = "POST"
    form.target = "_blank"

    // Use URL constructor for safe URL building
    const targetUrl = new URL(
      `/organizations/${encodeURIComponent(data.organizationName)}/settings/apps/new`,
      data.githubHost
    )
    form.action = targetUrl.toString()

    // Safely add manifest as hidden field with proper escaping
    const manifestInput = document.createElement("input")
    manifestInput.type = "hidden"
    manifestInput.name = "manifest"
    // Use setAttribute to safely set the value, which handles escaping
    manifestInput.setAttribute("value", JSON.stringify(manifest.manifest))
    form.appendChild(manifestInput)

    // Add organization parameter if not default GitHub host
    if (data.githubHost !== "https://github.com") {
      const orgInput = document.createElement("input")
      orgInput.type = "hidden"
      orgInput.name = "organization"
      // Use setAttribute for safe value setting
      orgInput.setAttribute("value", data.organizationName)
      form.appendChild(orgInput)
    }

    document.body.appendChild(form)
    form.submit()
    document.body.removeChild(form)

    toast({
      title: "Redirecting to GitHub",
      description: "Opening GitHub App creation page in new tab",
    })
  }

  const handleManualFormSuccess = () => {
    setShowSuccessMessage(true)
    refetchCredentialsStatus()
    toast({
      title: "GitHub App configured successfully!",
      description: "Your GitHub App credentials have been saved.",
    })
  }

  if (manifestIsLoading || credentialsStatusIsLoading) {
    return <CenteredSpinner />
  }

  if (manifestError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading GitHub App manifest: ${manifestError.message}`}
      />
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <GitBranchIcon className="size-5" />
          GitHub App Setup
        </CardTitle>
        <CardDescription>
          Create a GitHub App to enable workflow synchronization with your Git
          repositories
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {showSuccessMessage && (
          <div className="rounded-md border border-green-200 bg-green-50 p-4 dark:border-green-800 dark:bg-green-950/50">
            <div className="flex">
              <CheckCircleIcon className="size-5 text-green-400" />
              <div className="ml-3">
                <h3 className="text-sm font-medium text-green-800 dark:text-green-200">
                  GitHub App configured successfully!
                </h3>
                <p className="mt-1 text-sm text-green-700 dark:text-green-300">
                  Your GitHub App has been created and configured. You can now
                  use it for workflow synchronization.
                </p>
              </div>
            </div>
          </div>
        )}

        {credentialsStatus?.exists && (
          <div className="rounded-md border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950/50">
            <div className="flex">
              <CheckCircleIcon className="size-5 text-blue-400" />
              <div className="ml-3">
                <h3 className="text-sm font-medium text-blue-800 dark:text-blue-200">
                  GitHub App credentials configured
                </h3>
                <p className="mt-1 text-sm text-blue-700 dark:text-blue-300">
                  App ID: {credentialsStatus.app_id} •
                  {credentialsStatus.has_webhook_secret
                    ? " Webhook secret: ✓"
                    : " Webhook secret: ✗"}{" "}
                  •
                  {credentialsStatus.has_client_id
                    ? " Client ID: ✓"
                    : " Client ID: ✗"}
                </p>
              </div>
            </div>
          </div>
        )}

        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <TabsList className="grid w-full grid-cols-2">
            <TabsTrigger value="manifest" disableUnderline>
              Create new GitHub App
            </TabsTrigger>
            <TabsTrigger value="manual" disableUnderline>
              Use existing GitHub App
            </TabsTrigger>
          </TabsList>

          <TabsContent value="manifest" className="space-y-6">
            <div className="rounded-md border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/50">
              <div className="flex">
                <div className="ml-3">
                  <h3 className="text-sm font-medium text-amber-800 dark:text-amber-200">
                    Create a new GitHub App
                  </h3>
                  <div className="mt-2 text-sm text-amber-700 dark:text-amber-300">
                    <p>
                      This option will create a new GitHub App with the correct
                      permissions for Tracecat workflow synchronization.
                    </p>
                  </div>
                </div>
              </div>
            </div>

            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onSubmit)}
                className="space-y-4"
              >
                <FormField
                  control={form.control}
                  name="organizationName"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Organization name</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          placeholder="My Organization"
                          className="max-w-md"
                        />
                      </FormControl>
                      <p className="text-sm text-muted-foreground">
                        This will be included in the GitHub App name
                      </p>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={form.control}
                  name="githubHost"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>GitHub Host URL</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          placeholder="https://github.com"
                          className="max-w-md"
                        />
                      </FormControl>
                      <p className="text-sm text-muted-foreground">
                        Use this for GitHub Enterprise or custom GitHub
                        instances
                      </p>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </form>
            </Form>

            {manifest && (
              <>
                <div>
                  <label className="text-sm font-medium">Instructions</label>
                  <div className="mt-2 space-y-2">
                    {manifest.instructions.map((instruction, index) => (
                      <div key={index} className="flex items-start gap-2">
                        <span className="inline-flex size-5 shrink-0 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
                          {index + 1}
                        </span>
                        <p className="text-sm text-muted-foreground">
                          {instruction}
                        </p>
                      </div>
                    ))}
                  </div>
                </div>

                <div>
                  <label className="text-sm font-medium">
                    Generated manifest
                  </label>
                  <div className="mt-2 space-y-2">
                    <Textarea
                      value={JSON.stringify(manifest.manifest, null, 2)}
                      readOnly
                      className="h-64 font-mono text-xs"
                    />
                    <div className="flex gap-2">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={copyManifest}
                        className="gap-2"
                      >
                        <CopyIcon className="size-4" />
                        Copy manifest
                      </Button>
                      <Button
                        type="button"
                        onClick={form.handleSubmit(onSubmit)}
                        className="gap-2"
                      >
                        <ExternalLinkIcon className="size-4" />
                        Create GitHub App
                      </Button>
                    </div>
                  </div>
                </div>

                <div className="rounded-md border border-amber-200 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/50">
                  <div className="flex">
                    <div className="ml-3">
                      <h3 className="text-sm font-medium text-amber-800 dark:text-amber-200">
                        Next steps
                      </h3>
                      <div className="mt-2 text-sm text-amber-700 dark:text-amber-300">
                        <p>
                          After clicking "Create GitHub App", you'll be taken to
                          GitHub where you can:
                        </p>
                        <ul className="mt-2 list-disc space-y-1 pl-5">
                          <li>Review and confirm the app permissions</li>
                          <li>
                            Create the app (GitHub will redirect back
                            automatically)
                          </li>
                          <li>Install the app on your repositories</li>
                          <li>Return here to see the configuration status</li>
                        </ul>
                      </div>
                    </div>
                  </div>
                </div>
              </>
            )}
          </TabsContent>

          <TabsContent value="manual" className="space-y-6">
            <GitHubAppManualForm
              onSuccess={handleManualFormSuccess}
              existingAppId={
                credentialsStatus?.exists
                  ? credentialsStatus.app_id || undefined
                  : undefined
              }
            />
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  )
}
