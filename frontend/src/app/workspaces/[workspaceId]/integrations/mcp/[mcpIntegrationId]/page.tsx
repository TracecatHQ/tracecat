"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { AlertCircle, ChevronLeft, Loader2, Save, Trash2 } from "lucide-react"
import Link from "next/link"
import { useParams, useRouter } from "next/navigation"
import { useCallback, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { MCPIntegrationRead } from "@/client"
import { ProviderIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
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
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent } from "@/components/ui/card"
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
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  useDeleteMcpIntegration,
  useGetMcpIntegration,
  useIntegrations,
  useUpdateMcpIntegration,
} from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const AUTH_TYPES = [
  {
    value: "OAUTH2",
    label: "OAuth 2.0",
    description: "Use existing OAuth integration (MCP standard)",
  },
  {
    value: "CUSTOM",
    label: "Custom",
    description: "API key, bearer token, or custom headers (JSON)",
  },
  {
    value: "NONE",
    label: "No Authentication",
    description: "No authentication required (for self-hosted)",
  },
] as const

const formSchema = z
  .object({
    name: z
      .string()
      .trim()
      .min(3, { message: "Name must be at least 3 characters long" })
      .max(255, { message: "Name must be 255 characters or fewer" }),
    description: z
      .string()
      .trim()
      .max(512, { message: "Description must be 512 characters or fewer" })
      .optional()
      .or(z.literal("")),
    server_uri: z.string().trim().url({ message: "Enter a valid URL" }),
    auth_type: z.enum(["OAUTH2", "CUSTOM", "NONE"]),
    oauth_integration_id: z.string().uuid().optional().or(z.literal("")),
    custom_credentials: z.string().trim().optional().or(z.literal("")),
  })
  .refine(
    (data) => {
      if (data.auth_type === "OAUTH2") {
        return !!data.oauth_integration_id && data.oauth_integration_id !== ""
      }
      return true
    },
    {
      message: "OAuth integration is required for OAuth 2.0 authentication",
      path: ["oauth_integration_id"],
    }
  )
  .refine(
    (data) => {
      if (data.auth_type === "CUSTOM") {
        if (!data.custom_credentials || data.custom_credentials.trim() === "") {
          return false
        }
        try {
          JSON.parse(data.custom_credentials)
          return true
        } catch {
          return false
        }
      }
      return true
    },
    {
      message: "Custom credentials must be valid JSON",
      path: ["custom_credentials"],
    }
  )

type MCPIntegrationFormValues = z.infer<typeof formSchema>

export default function McpIntegrationDetailPage() {
  const params = useParams()
  const workspaceId = useWorkspaceId()

  if (!params) {
    return <div>Error: Invalid parameters</div>
  }

  const mcpIntegrationId = params.mcpIntegrationId as string

  const { mcpIntegration, mcpIntegrationIsLoading, mcpIntegrationError } =
    useGetMcpIntegration(workspaceId, mcpIntegrationId)

  if (mcpIntegrationIsLoading) {
    return <CenteredSpinner />
  }

  if (mcpIntegrationError || !mcpIntegration) {
    return (
      <div className="container mx-auto max-w-4xl p-6">
        <div className="flex flex-col items-center justify-center space-y-4 py-12">
          <AlertCircle className="size-12 text-muted-foreground" />
          <h2 className="text-xl font-semibold">MCP integration not found</h2>
          <div className="text-muted-foreground">
            The requested MCP integration could not be found.
          </div>
          <Link href={`/workspaces/${workspaceId}/integrations`}>
            <Button variant="outline" className="mt-2">
              <ChevronLeft className="mr-2 size-4" />
              Back to integrations
            </Button>
          </Link>
        </div>
      </div>
    )
  }

  return <McpIntegrationDetailContent mcpIntegration={mcpIntegration} />
}

function McpIntegrationDetailContent({
  mcpIntegration,
}: {
  mcpIntegration: MCPIntegrationRead
}) {
  const workspaceId = useWorkspaceId()
  const router = useRouter()
  const { updateMcpIntegration, updateMcpIntegrationIsPending } =
    useUpdateMcpIntegration(workspaceId)
  const { deleteMcpIntegration, deleteMcpIntegrationIsPending } =
    useDeleteMcpIntegration(workspaceId)
  const { integrations, providers, integrationsIsLoading } =
    useIntegrations(workspaceId)

  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false)
  const [deleteConfirmText, setDeleteConfirmText] = useState("")

  const form = useForm<MCPIntegrationFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: {
      name: mcpIntegration.name,
      description: mcpIntegration.description || "",
      server_uri: mcpIntegration.server_uri,
      auth_type: mcpIntegration.auth_type,
      oauth_integration_id: mcpIntegration.oauth_integration_id || "",
      custom_credentials: "",
    },
  })

  const authType = form.watch("auth_type")

  const onSubmit = async (values: MCPIntegrationFormValues) => {
    try {
      await updateMcpIntegration({
        mcpIntegrationId: mcpIntegration.id,
        params: {
          name: values.name.trim(),
          description: values.description?.trim() || undefined,
          server_uri: values.server_uri.trim(),
          auth_type: values.auth_type,
          oauth_integration_id:
            values.auth_type === "OAUTH2" && values.oauth_integration_id
              ? values.oauth_integration_id
              : undefined,
          custom_credentials:
            values.auth_type === "CUSTOM" && values.custom_credentials
              ? values.custom_credentials.trim()
              : undefined,
        },
      })
    } catch (error) {
      console.error("Failed to update MCP integration:", error)
    }
  }

  const handleDelete = useCallback(async () => {
    if (deleteConfirmText !== mcpIntegration.name) return

    try {
      await deleteMcpIntegration(mcpIntegration.id)
      router.push(`/workspaces/${workspaceId}/integrations`)
    } catch (error) {
      console.error("Failed to delete MCP integration:", error)
    }
  }, [
    deleteConfirmText,
    mcpIntegration,
    deleteMcpIntegration,
    router,
    workspaceId,
  ])

  const handleDeleteDialogOpenChange = (open: boolean) => {
    setDeleteDialogOpen(open)
    if (!open) {
      setDeleteConfirmText("")
    }
  }

  const isPending =
    updateMcpIntegrationIsPending || deleteMcpIntegrationIsPending

  return (
    <div className="container mx-auto max-w-4xl p-6 mb-20 mt-12">
      {/* Header */}
      <div className="mb-8">
        <Link
          href={`/workspaces/${workspaceId}/integrations`}
          className="inline-flex items-center text-sm text-muted-foreground hover:text-foreground mb-4"
        >
          <ChevronLeft className="mr-1 size-4" />
          Back to integrations
        </Link>
        <div className="flex items-start justify-between">
          <div className="flex items-start gap-4">
            <div className="flex size-12 items-center justify-center rounded-lg border bg-muted">
              <div className="flex h-8 w-8 items-center justify-center rounded-md bg-gray-100 text-xs font-medium text-gray-600">
                MCP
              </div>
            </div>
            <div>
              <h1 className="text-3xl font-bold">{mcpIntegration.name}</h1>
              {mcpIntegration.description && (
                <p className="mt-1 text-muted-foreground">
                  {mcpIntegration.description}
                </p>
              )}
              <div className="mt-2 gap-2">
                <Badge variant="outline">
                  {AUTH_TYPES.find((t) => t.value === mcpIntegration.auth_type)
                    ?.label || mcpIntegration.auth_type}
                </Badge>
              </div>
            </div>
          </div>
        </div>
      </div>

      {/* Configuration Form */}
      <Card>
        <CardContent className="pt-6">
          <Form {...form}>
            <form
              className="space-y-5"
              onSubmit={form.handleSubmit(onSubmit)}
              noValidate
            >
              <FormField
                control={form.control}
                name="name"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Integration name</FormLabel>
                    <FormControl>
                      <Input placeholder="My MCP Server" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="description"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Description</FormLabel>
                    <FormControl>
                      <Textarea
                        placeholder="Optional description for this MCP integration"
                        {...field}
                      />
                    </FormControl>
                    <FormDescription className="text-xs">
                      Appears in the integrations list for this workspace.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="server_uri"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Server URI</FormLabel>
                    <FormControl>
                      <Input
                        placeholder="https://mcp.example.com/mcp"
                        {...field}
                      />
                    </FormControl>
                    <FormDescription className="text-xs">
                      The MCP server endpoint URL. Must use HTTPS (localhost
                      allowed with HTTP).
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />
              <FormField
                control={form.control}
                name="auth_type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Authentication type</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select authentication type">
                            {field.value
                              ? AUTH_TYPES.find(
                                  (opt) => opt.value === field.value
                                )?.label
                              : null}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {AUTH_TYPES.map((option) => (
                            <SelectItem
                              key={option.value}
                              value={option.value}
                              textValue={option.label}
                            >
                              <div className="flex flex-col gap-1">
                                <span className="text-sm font-medium">
                                  {option.label}
                                </span>
                                <span className="text-xs text-muted-foreground">
                                  {option.description}
                                </span>
                              </div>
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </FormControl>
                    <FormDescription className="text-xs">
                      Choose how to authenticate with the MCP server.
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* OAuth 2.0 Fields */}
              {authType === "OAUTH2" && (
                <FormField
                  control={form.control}
                  name="oauth_integration_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>OAuth integration</FormLabel>
                      <FormControl>
                        <Select
                          value={field.value || ""}
                          onValueChange={field.onChange}
                        >
                          <SelectTrigger>
                            <SelectValue placeholder="Select OAuth integration">
                              {field.value
                                ? (() => {
                                    const integration = integrations?.find(
                                      (int) => int.id === field.value
                                    )
                                    if (
                                      !integration ||
                                      integration.provider_id.endsWith("_mcp")
                                    )
                                      return null
                                    const provider = providers?.find(
                                      (p) => p.id === integration.provider_id
                                    )
                                    return (
                                      provider?.name || integration.provider_id
                                    )
                                  })()
                                : null}
                            </SelectValue>
                          </SelectTrigger>
                          <SelectContent>
                            {integrationsIsLoading ? (
                              <div className="px-2 py-1.5 text-xs text-muted-foreground">
                                Loading integrations...
                              </div>
                            ) : (
                              <>
                                {integrations
                                  ?.filter(
                                    (int) =>
                                      int.status === "connected" &&
                                      !int.provider_id.endsWith("_mcp")
                                  )
                                  .map((integration) => {
                                    const provider = providers?.find(
                                      (p) => p.id === integration.provider_id
                                    )
                                    return (
                                      <SelectItem
                                        key={integration.id}
                                        value={integration.id}
                                        textValue={
                                          provider?.name ||
                                          integration.provider_id
                                        }
                                      >
                                        <div className="flex items-center gap-2">
                                          {provider && (
                                            <ProviderIcon
                                              providerId={
                                                integration.provider_id
                                              }
                                              className="h-4 w-4 shrink-0 bg-transparent p-0"
                                            />
                                          )}
                                          <span className="text-sm font-medium">
                                            {provider?.name ||
                                              integration.provider_id}
                                          </span>
                                        </div>
                                      </SelectItem>
                                    )
                                  })}
                                {(!integrations ||
                                  integrations.filter(
                                    (int) =>
                                      int.status === "connected" &&
                                      !int.provider_id.endsWith("_mcp")
                                  ).length === 0) && (
                                  <div className="px-2 py-1.5 text-xs text-muted-foreground">
                                    No OAuth integrations available
                                  </div>
                                )}
                              </>
                            )}
                          </SelectContent>
                        </Select>
                      </FormControl>
                      <FormDescription className="text-xs">
                        Select an existing OAuth integration to use for
                        authentication.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              {/* Custom Credentials Fields */}
              {authType === "CUSTOM" && (
                <FormField
                  control={form.control}
                  name="custom_credentials"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Custom credentials (JSON)</FormLabel>
                      <FormControl>
                        <Textarea
                          placeholder='{"Authorization": "Bearer token123"} or {"X-API-Key": "key123"}'
                          className="font-mono text-sm min-h-[100px]"
                          {...field}
                        />
                      </FormControl>
                      <FormDescription className="text-xs">
                        Enter custom headers as a JSON object.
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              )}

              <div className="flex items-center justify-between pt-4 border-t">
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() => setDeleteDialogOpen(true)}
                  disabled={isPending}
                  className="gap-2 text-muted-foreground hover:text-destructive"
                >
                  <Trash2 className="h-4 w-4" />
                  Delete integration
                </Button>
                <Button type="submit" className="gap-2" disabled={isPending}>
                  {updateMcpIntegrationIsPending && (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  )}
                  <Save className="h-4 w-4" />
                  Save changes
                </Button>
              </div>
            </form>
          </Form>
        </CardContent>
      </Card>

      <AlertDialog
        open={deleteDialogOpen}
        onOpenChange={handleDeleteDialogOpenChange}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete MCP integration</AlertDialogTitle>
            <AlertDialogDescription className="space-y-4">
              <p>
                Are you sure you want to delete{" "}
                <strong>{mcpIntegration.name}</strong>? This action cannot be
                undone. Any agent presets using this integration will need to be
                updated.
              </p>
              <div className="space-y-2">
                <Label htmlFor="delete-confirm">
                  Type <strong>{mcpIntegration.name}</strong> to confirm:
                </Label>
                <Input
                  id="delete-confirm"
                  value={deleteConfirmText}
                  onChange={(e) => setDeleteConfirmText(e.target.value)}
                  onKeyDown={(e) => {
                    if (
                      e.key === "Enter" &&
                      deleteConfirmText === mcpIntegration.name &&
                      !deleteMcpIntegrationIsPending
                    ) {
                      e.preventDefault()
                      handleDelete()
                    }
                  }}
                  placeholder="Enter integration name"
                  disabled={deleteMcpIntegrationIsPending}
                />
              </div>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleteMcpIntegrationIsPending}>
              Cancel
            </AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleDelete}
              disabled={
                deleteMcpIntegrationIsPending ||
                deleteConfirmText !== mcpIntegration.name
              }
              className="gap-2"
            >
              {deleteMcpIntegrationIsPending && (
                <Loader2 className="h-4 w-4 animate-spin" />
              )}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
