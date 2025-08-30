"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { KeyIcon, ShieldCheckIcon } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { AlertNotification } from "@/components/notifications"
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
import { Textarea } from "@/components/ui/textarea"
import { useToast } from "@/components/ui/use-toast"
import { useGitHubAppCredentials } from "@/lib/hooks"

const gitHubAppCredentialsSchema = z.object({
  app_id: z
    .string()
    .min(1, "App ID is required")
    .regex(/^\d+$/, "App ID must be numeric"),
  private_key: z
    .string()
    .min(1, "Private key is required")
    .refine(
      (val) =>
        val.includes("BEGIN RSA PRIVATE KEY") ||
        val.includes("BEGIN PRIVATE KEY"),
      "Private key must be in PEM format with BEGIN/END markers"
    ),
  webhook_secret: z.string().optional(),
  client_id: z.string().optional(),
})

type GitHubAppCredentialsFormData = z.infer<typeof gitHubAppCredentialsSchema>

interface GitHubAppManualFormProps {
  onSuccess?: () => void
  existingAppId?: string
  className?: string
}

export function GitHubAppManualForm({
  onSuccess,
  existingAppId,
  className,
}: GitHubAppManualFormProps) {
  const { saveCredentials } = useGitHubAppCredentials()
  const { toast } = useToast()
  const [isSubmitting, setIsSubmitting] = useState(false)

  const form = useForm<GitHubAppCredentialsFormData>({
    resolver: zodResolver(gitHubAppCredentialsSchema),
    defaultValues: {
      app_id: existingAppId || "",
      private_key: "",
      webhook_secret: "",
      client_id: "",
    },
  })

  const onSubmit = async (data: GitHubAppCredentialsFormData) => {
    try {
      setIsSubmitting(true)

      await saveCredentials.mutateAsync({
        app_id: data.app_id,
        private_key: data.private_key,
        webhook_secret: data.webhook_secret || undefined,
        client_id: data.client_id || undefined,
      })

      const action = existingAppId ? "updated" : "registered"
      toast({
        title: `GitHub App ${action} successfully`,
        description: `Your GitHub App credentials have been ${action}.`,
      })

      // Clear sensitive data from form
      form.setValue("private_key", "")
      if (data.webhook_secret) {
        form.setValue("webhook_secret", "")
      }

      onSuccess?.()
    } catch (error) {
      console.error("Failed to save GitHub App credentials:", error)
      toast({
        title: "Error",
        description:
          error instanceof Error
            ? error.message
            : "Failed to save GitHub App credentials",
        variant: "destructive",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <KeyIcon className="size-5" />
          {existingAppId ? "Update GitHub App" : "Register Existing GitHub App"}
        </CardTitle>
        <CardDescription>
          {existingAppId
            ? "Update the credentials for your existing GitHub App."
            : "Enter the credentials for a GitHub App you've already created in GitHub's settings."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {!existingAppId && (
          <div className="rounded-md border border-blue-200 bg-blue-50 p-4 dark:border-blue-800 dark:bg-blue-950/50">
            <div className="flex">
              <ShieldCheckIcon className="size-5 text-blue-400" />
              <div className="ml-3">
                <h3 className="text-sm font-medium text-blue-800 dark:text-blue-200">
                  Before you start
                </h3>
                <div className="mt-2 text-sm text-blue-700 dark:text-blue-300">
                  <p>Make sure you have:</p>
                  <ul className="mt-1 list-inside list-disc space-y-1">
                    <li>
                      Created a GitHub App in your organization's settings
                    </li>
                    <li>Downloaded the private key (.pem file)</li>
                    <li>Noted the App ID from the app settings</li>
                  </ul>
                </div>
              </div>
            </div>
          </div>
        )}

        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
            <FormField
              control={form.control}
              name="app_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>App ID *</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder="123456"
                      className="max-w-md"
                    />
                  </FormControl>
                  <p className="text-sm text-muted-foreground">
                    Found in: GitHub → Settings → GitHub Apps → Your App
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="private_key"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Private Key *</FormLabel>
                  <FormControl>
                    <Textarea
                      {...field}
                      placeholder="-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA...
-----END RSA PRIVATE KEY-----"
                      className="h-32 font-mono text-xs"
                    />
                  </FormControl>
                  <p className="text-sm text-muted-foreground">
                    Paste the entire contents of the .pem file you downloaded
                    from GitHub
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="webhook_secret"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Webhook Secret (optional)</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      type="password"
                      placeholder="Enter webhook secret"
                      className="max-w-md"
                    />
                  </FormControl>
                  <p className="text-sm text-muted-foreground">
                    Only needed if you configured a webhook secret in your
                    GitHub App
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />

            <FormField
              control={form.control}
              name="client_id"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Client ID (optional)</FormLabel>
                  <FormControl>
                    <Input
                      {...field}
                      placeholder="Iv1.abc123def456"
                      className="max-w-md"
                    />
                  </FormControl>
                  <p className="text-sm text-muted-foreground">
                    Found in the same location as App ID
                  </p>
                  <FormMessage />
                </FormItem>
              )}
            />

            <div className="flex gap-3 pt-4">
              <Button
                type="submit"
                disabled={isSubmitting || saveCredentials.isPending}
                className="min-w-32"
              >
                {isSubmitting || saveCredentials.isPending
                  ? "Saving..."
                  : existingAppId
                    ? "Update App"
                    : "Register App"}
              </Button>
            </div>
          </form>
        </Form>

        {saveCredentials.isError && (
          <AlertNotification
            level="error"
            message={
              saveCredentials.error instanceof Error
                ? saveCredentials.error.message
                : "Failed to save GitHub App credentials"
            }
          />
        )}
      </CardContent>
    </Card>
  )
}
