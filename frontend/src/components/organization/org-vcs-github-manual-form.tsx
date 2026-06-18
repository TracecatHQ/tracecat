"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { FileTextIcon, UploadCloudIcon } from "lucide-react"
import {
  type ChangeEvent,
  type DragEvent,
  type KeyboardEvent,
  useCallback,
  useRef,
  useState,
} from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
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
import { cn } from "@/lib/utils"

const pemFileExtensionRegex = /\.pem$/i

function getPrivateKeyDropzoneClass({
  hasError,
  isDragOver,
}: {
  hasError: boolean
  isDragOver: boolean
}) {
  if (hasError) {
    return "border-destructive/80 bg-destructive/5"
  }
  if (isDragOver) {
    return "border-primary/60 bg-primary/5"
  }
  return "border-muted-foreground/30 bg-muted/40 hover:border-muted-foreground/50"
}

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
  hasStoredCredentials?: boolean
  className?: string
}

export function GitHubAppManualForm({
  onSuccess,
  existingAppId,
  hasStoredCredentials = false,
  className,
}: GitHubAppManualFormProps) {
  const { saveCredentials } = useGitHubAppCredentials()
  const { toast } = useToast()
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [privateKeyFileName, setPrivateKeyFileName] = useState<string | null>(
    null
  )
  const [isPrivateKeyDragOver, setIsPrivateKeyDragOver] = useState(false)
  const privateKeyFileInputRef = useRef<HTMLInputElement | null>(null)

  const form = useForm<GitHubAppCredentialsFormData>({
    resolver: zodResolver(gitHubAppCredentialsSchema),
    defaultValues: {
      app_id: existingAppId || "",
      private_key: "",
      webhook_secret: "",
      client_id: "",
    },
  })

  const resetPrivateKeyFileInput = useCallback(() => {
    if (privateKeyFileInputRef.current) {
      privateKeyFileInputRef.current.value = ""
    }
  }, [])

  const handlePrivateKeyFile = useCallback(
    (file: File | undefined) => {
      if (!file) {
        return
      }

      if (!pemFileExtensionRegex.test(file.name)) {
        setPrivateKeyFileName(null)
        form.setError("private_key", {
          type: "manual",
          message: "Upload a .pem file.",
        })
        resetPrivateKeyFileInput()
        return
      }

      const reader = new FileReader()
      reader.onload = () => {
        const privateKey =
          typeof reader.result === "string" ? reader.result : ""

        form.clearErrors("private_key")
        form.setValue("private_key", privateKey, {
          shouldDirty: true,
          shouldTouch: true,
          shouldValidate: true,
        })
        setPrivateKeyFileName(file.name)
        resetPrivateKeyFileInput()
      }
      reader.onerror = () => {
        setPrivateKeyFileName(null)
        form.setError("private_key", {
          type: "manual",
          message: "Failed to read the uploaded PEM file.",
        })
        resetPrivateKeyFileInput()
      }

      reader.readAsText(file)
    },
    [form, resetPrivateKeyFileInput]
  )

  const handlePrivateKeyInputChange = useCallback(
    (event: ChangeEvent<HTMLInputElement>) => {
      handlePrivateKeyFile(event.target.files?.[0])
    },
    [handlePrivateKeyFile]
  )

  const handlePrivateKeyDrop = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      event.stopPropagation()
      setIsPrivateKeyDragOver(false)
      handlePrivateKeyFile(event.dataTransfer.files?.[0])
      event.dataTransfer.clearData()
    },
    [handlePrivateKeyFile]
  )

  const handlePrivateKeyDragOver = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      event.preventDefault()
      event.dataTransfer.dropEffect = "copy"
      setIsPrivateKeyDragOver(true)
    },
    []
  )

  const handlePrivateKeyDragLeave = useCallback(
    (event: DragEvent<HTMLDivElement>) => {
      const relatedTarget = event.relatedTarget
      if (
        relatedTarget instanceof Node &&
        event.currentTarget.contains(relatedTarget)
      ) {
        return
      }
      setIsPrivateKeyDragOver(false)
    },
    []
  )

  const handlePrivateKeyDropzoneClick = useCallback(() => {
    privateKeyFileInputRef.current?.click()
  }, [])

  const handlePrivateKeyDropzoneKeyDown = useCallback(
    (event: KeyboardEvent<HTMLDivElement>) => {
      if (event.key !== "Enter" && event.key !== " ") {
        return
      }
      event.preventDefault()
      handlePrivateKeyDropzoneClick()
    },
    [handlePrivateKeyDropzoneClick]
  )

  const onSubmit = async (data: GitHubAppCredentialsFormData) => {
    try {
      setIsSubmitting(true)

      await saveCredentials.mutateAsync({
        app_id: data.app_id,
        private_key: data.private_key,
        webhook_secret: data.webhook_secret || undefined,
        client_id: data.client_id || undefined,
      })

      const action = hasStoredCredentials ? "updated" : "registered"
      toast({
        title: `GitHub App ${action} successfully`,
        description: `Your GitHub App credentials have been ${action}.`,
      })

      // Clear sensitive data from form
      form.setValue("private_key", "")
      setPrivateKeyFileName(null)
      resetPrivateKeyFileInput()
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

  const buttonLabel = hasStoredCredentials ? "Save changes" : "Save credentials"

  const containerClass = className ? `space-y-4 ${className}` : "space-y-4"

  return (
    <div className={containerClass}>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="flex flex-col">
          <div className="space-y-8">
            <div className="space-y-2">
              <FormField
                control={form.control}
                name="app_id"
                render={({ field }) => (
                  <FormItem className="space-y-2">
                    <FormLabel>GitHub App ID *</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        placeholder="123456"
                        className="max-w-md"
                      />
                    </FormControl>
                    <p className="text-xs text-muted-foreground">
                      Find this in GitHub → Settings → GitHub Apps
                    </p>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="private_key"
                render={({ field, fieldState }) => {
                  const dropzoneStateClass = getPrivateKeyDropzoneClass({
                    hasError: fieldState.invalid,
                    isDragOver: isPrivateKeyDragOver,
                  })

                  return (
                    <FormItem className="space-y-2">
                      <FormLabel>Private Key *</FormLabel>
                      <input
                        ref={privateKeyFileInputRef}
                        type="file"
                        accept=".pem"
                        className="hidden"
                        onChange={handlePrivateKeyInputChange}
                      />
                      <div
                        data-testid="github-app-private-key-dropzone"
                        role="button"
                        tabIndex={0}
                        aria-label="Choose GitHub App private key PEM file"
                        onClick={handlePrivateKeyDropzoneClick}
                        onKeyDown={handlePrivateKeyDropzoneKeyDown}
                        onDrop={handlePrivateKeyDrop}
                        onDragEnter={handlePrivateKeyDragOver}
                        onDragOver={handlePrivateKeyDragOver}
                        onDragLeave={handlePrivateKeyDragLeave}
                        className={cn(
                          "flex cursor-pointer flex-col items-center justify-center gap-3 rounded-md border border-dashed px-4 py-8 text-center transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
                          dropzoneStateClass
                        )}
                      >
                        <span
                          className={cn(
                            "flex size-10 items-center justify-center rounded-full transition-colors",
                            isPrivateKeyDragOver
                              ? "bg-primary/10 text-primary"
                              : "bg-muted-foreground/10 text-muted-foreground"
                          )}
                        >
                          {privateKeyFileName ? (
                            <FileTextIcon className="size-5" />
                          ) : (
                            <UploadCloudIcon className="size-5" />
                          )}
                        </span>
                        <div className="flex flex-col gap-0.5">
                          {privateKeyFileName ? (
                            <>
                              <p className="text-sm font-medium">
                                {privateKeyFileName}
                              </p>
                              <p className="text-xs text-muted-foreground">
                                Click to replace, or paste below
                              </p>
                            </>
                          ) : (
                            <>
                              <p className="text-sm font-medium">
                                Drop .pem file here, or{" "}
                                <span className="text-primary">
                                  click to browse
                                </span>
                              </p>
                              <p className="text-xs text-muted-foreground">
                                You can also paste it below
                              </p>
                            </>
                          )}
                        </div>
                      </div>
                      <FormControl>
                        <Textarea
                          {...field}
                          onChange={(event) => {
                            setPrivateKeyFileName(null)
                            form.clearErrors("private_key")
                            field.onChange(event)
                          }}
                          placeholder={`-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA1Xq8z2vY3kHnPbW9pJ4rT5sU6wK
9bV3nHpQ7rTaB4eYhN2dWfL5mC8xR1oZ0gD7uF6iX3c
…
hQ4kR2bJ8nVwPzL0aXmS9tE1yU6sJ0oZ4eY7dWfL5mC
-----END RSA PRIVATE KEY-----`}
                          className="h-32 font-mono text-xs"
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )
                }}
              />

              <FormField
                control={form.control}
                name="webhook_secret"
                render={({ field }) => (
                  <FormItem className="space-y-2">
                    <FormLabel>Webhook Secret (optional)</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        type="password"
                        placeholder="Enter webhook secret"
                        className="max-w-md"
                      />
                    </FormControl>
                    <p className="text-xs text-muted-foreground">
                      Needed only if you configured a webhook secret in GitHub
                    </p>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="client_id"
                render={({ field }) => (
                  <FormItem className="space-y-2">
                    <FormLabel>Client ID (optional)</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        placeholder="Iv1.abc123def456"
                        className="max-w-md"
                      />
                    </FormControl>
                    <p className="text-xs text-muted-foreground">
                      Found alongside the App ID in GitHub
                    </p>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>

            <Button
              type="submit"
              disabled={isSubmitting || saveCredentials.isPending}
              className="min-w-32"
            >
              {isSubmitting || saveCredentials.isPending
                ? "Saving..."
                : buttonLabel}
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
    </div>
  )
}
