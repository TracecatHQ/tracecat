"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2, Plus } from "lucide-react"
import React, { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { ProviderIcon } from "@/components/icons"
import { Button, type ButtonProps } from "@/components/ui/button"
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
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import {
  useCreateMcpIntegration,
  useGetMcpIntegration,
  useIntegrations,
  useUpdateMcpIntegration,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"
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
    // OAuth fields
    oauth_integration_id: z.string().uuid().optional().or(z.literal("")),
    // Custom credentials (JSON string for API key, bearer token, or custom headers)
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
        // Validate JSON format
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

const DEFAULT_VALUES: MCPIntegrationFormValues = {
  name: "",
  description: "",
  server_uri: "",
  auth_type: "OAUTH2",
  oauth_integration_id: "",
  custom_credentials: "",
}

export function MCPIntegrationDialog({
  triggerProps,
  mcpIntegrationId,
  onOpenChange,
  open: controlledOpen,
  hideTrigger = false,
}: {
  triggerProps?: ButtonProps
  mcpIntegrationId?: string | null
  onOpenChange?: (open: boolean) => void
  open?: boolean
  hideTrigger?: boolean
}) {
  const workspaceId = useWorkspaceId()
  const isEditMode = Boolean(mcpIntegrationId)
  const { createMcpIntegration, createMcpIntegrationIsPending } =
    useCreateMcpIntegration(workspaceId)
  const { updateMcpIntegration, updateMcpIntegrationIsPending } =
    useUpdateMcpIntegration(workspaceId)
  const { integrations, providers, integrationsIsLoading } =
    useIntegrations(workspaceId)
  const { mcpIntegration, mcpIntegrationIsLoading } = useGetMcpIntegration(
    workspaceId,
    mcpIntegrationId ?? null
  )
  const [internalOpen, setInternalOpen] = useState(false)
  const open = controlledOpen ?? internalOpen
  const { className: triggerClassName, ...restTriggerProps } =
    triggerProps ?? {}

  // Open dialog when mcpIntegrationId is set (edit mode)
  React.useEffect(() => {
    if (mcpIntegrationId && controlledOpen === undefined) {
      setInternalOpen(true)
    }
  }, [mcpIntegrationId, controlledOpen])

  const form = useForm<MCPIntegrationFormValues>({
    resolver: zodResolver(formSchema),
    defaultValues: DEFAULT_VALUES,
  })

  const authType = form.watch("auth_type")

  // Load integration data when in edit mode
  React.useEffect(() => {
    if (isEditMode && mcpIntegration && !mcpIntegrationIsLoading) {
      form.reset({
        name: mcpIntegration.name,
        description: mcpIntegration.description || "",
        server_uri: mcpIntegration.server_uri,
        auth_type: mcpIntegration.auth_type,
        oauth_integration_id: mcpIntegration.oauth_integration_id || "",
        custom_credentials: "", // Don't populate for security
      })
    }
  }, [isEditMode, mcpIntegration, mcpIntegrationIsLoading, form])

  const resetForm = () => {
    if (isEditMode && mcpIntegration) {
      form.reset({
        name: mcpIntegration.name,
        description: mcpIntegration.description || "",
        server_uri: mcpIntegration.server_uri,
        auth_type: mcpIntegration.auth_type,
        oauth_integration_id: mcpIntegration.oauth_integration_id || "",
        custom_credentials: "",
      })
    } else {
      form.reset(DEFAULT_VALUES)
    }
  }

  const handleOpenChange = (nextOpen: boolean) => {
    if (controlledOpen === undefined) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
    if (!nextOpen) {
      resetForm()
    }
  }

  const onSubmit = async (values: MCPIntegrationFormValues) => {
    try {
      if (isEditMode && mcpIntegrationId) {
        await updateMcpIntegration({
          mcpIntegrationId,
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
      } else {
        await createMcpIntegration({
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
        })
      }
      handleOpenChange(false)
    } catch (error) {
      // Error is handled by the hook's onError callback
      console.error(
        `Failed to ${isEditMode ? "update" : "create"} MCP integration:`,
        error
      )
    }
  }

  const isPending =
    createMcpIntegrationIsPending || updateMcpIntegrationIsPending

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      {!isEditMode && !hideTrigger && (
        <DialogTrigger asChild>
          <Button
            size="sm"
            variant="outline"
            className={cn("h-7 bg-white", triggerClassName)}
            {...restTriggerProps}
          >
            <Plus className="mr-1 h-3.5 w-3.5" />
            Add MCP Integration
          </Button>
        </DialogTrigger>
      )}
      <DialogContent className="max-h-[85vh] max-w-lg overflow-y-auto">
        <DialogHeader>
          <DialogTitle>
            {isEditMode ? "Edit MCP Integration" : "Add MCP Integration"}
          </DialogTitle>
          <DialogDescription>
            {isEditMode
              ? "Update your MCP (Model Context Protocol) server integration."
              : "Configure a custom MCP (Model Context Protocol) server integration."}
          </DialogDescription>
        </DialogHeader>
        {(mcpIntegrationIsLoading || integrationsIsLoading) && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
          </div>
        )}
        {!mcpIntegrationIsLoading && !integrationsIsLoading && (
          <Form {...form}>
            <form
              className="space-y-5 overflow-y-auto px-1"
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
                      <FormLabel>OAuth Integration</FormLabel>
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
                      <FormLabel>Custom Credentials (JSON)</FormLabel>
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

              <DialogFooter className="flex flex-col gap-2 sm:flex-row">
                <div className="flex w-full items-center justify-end gap-2">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => handleOpenChange(false)}
                    disabled={isPending}
                  >
                    Cancel
                  </Button>
                  <Button type="submit" className="gap-2" disabled={isPending}>
                    {isPending && <Loader2 className="h-4 w-4 animate-spin" />}
                    {isEditMode ? "Update integration" : "Save integration"}
                  </Button>
                </div>
              </DialogFooter>
            </form>
          </Form>
        )}
      </DialogContent>
    </Dialog>
  )
}
