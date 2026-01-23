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

const SERVER_TYPES = [
  {
    value: "url",
    label: "URL (HTTP/SSE)",
    description: "Connect to an MCP server via HTTP or SSE endpoint",
  },
  {
    value: "command",
    label: "Command (stdio)",
    description: "Run a command that spawns an MCP server (e.g., npx)",
  },
] as const

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

const ALLOWED_COMMANDS = ["npx", "uvx", "python", "python3", "node"] as const

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
    // Server type
    server_type: z.enum(["url", "command"]),
    // URL-type fields
    server_uri: z.string().trim().optional().or(z.literal("")),
    auth_type: z.enum(["OAUTH2", "CUSTOM", "NONE"]),
    oauth_integration_id: z.string().uuid().optional().or(z.literal("")),
    custom_credentials: z.string().trim().optional().or(z.literal("")),
    // Command-type fields
    command: z.string().trim().optional().or(z.literal("")),
    command_args: z.string().trim().optional().or(z.literal("")),
    command_env: z.string().trim().optional().or(z.literal("")),
    // General fields
    timeout: z.coerce.number().int().min(1).max(300).optional(),
  })
  // URL-type validation
  .refine(
    (data) => {
      if (data.server_type === "url") {
        if (!data.server_uri || data.server_uri.trim() === "") {
          return false
        }
        try {
          new URL(data.server_uri)
          return true
        } catch {
          return false
        }
      }
      return true
    },
    {
      message: "Valid server URL is required for URL-type servers",
      path: ["server_uri"],
    }
  )
  .refine(
    (data) => {
      if (data.server_type === "url" && data.auth_type === "OAUTH2") {
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
      if (data.server_type === "url" && data.auth_type === "CUSTOM") {
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
  // Command-type validation
  .refine(
    (data) => {
      if (data.server_type === "command") {
        if (!data.command || data.command.trim() === "") {
          return false
        }
        const cmd = data.command.trim() as (typeof ALLOWED_COMMANDS)[number]
        return ALLOWED_COMMANDS.includes(cmd)
      }
      return true
    },
    {
      message: `Command must be one of: ${ALLOWED_COMMANDS.join(", ")}`,
      path: ["command"],
    }
  )
  .refine(
    (data) => {
      if (
        data.server_type === "command" &&
        data.command_env &&
        data.command_env.trim() !== ""
      ) {
        try {
          const parsed = JSON.parse(data.command_env)
          return (
            typeof parsed === "object" &&
            parsed !== null &&
            !Array.isArray(parsed)
          )
        } catch {
          return false
        }
      }
      return true
    },
    {
      message: "Environment variables must be a valid JSON object",
      path: ["command_env"],
    }
  )

type MCPIntegrationFormValues = z.infer<typeof formSchema>

const DEFAULT_VALUES: MCPIntegrationFormValues = {
  name: "",
  description: "",
  server_type: "url",
  server_uri: "",
  auth_type: "NONE",
  oauth_integration_id: "",
  custom_credentials: "",
  command: "",
  command_args: "",
  command_env: "",
  timeout: 30,
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

  const serverType = form.watch("server_type")
  const authType = form.watch("auth_type")

  // Load integration data when in edit mode
  React.useEffect(() => {
    if (isEditMode && mcpIntegration && !mcpIntegrationIsLoading) {
      form.reset({
        name: mcpIntegration.name,
        description: mcpIntegration.description || "",
        server_type: mcpIntegration.server_type,
        server_uri: mcpIntegration.server_uri || "",
        auth_type: mcpIntegration.auth_type,
        oauth_integration_id: mcpIntegration.oauth_integration_id || "",
        custom_credentials: "", // Don't populate for security
        command: mcpIntegration.command || "",
        command_args: mcpIntegration.command_args?.join("\n") || "",
        command_env: "", // Env vars are not returned from API for security
        timeout: mcpIntegration.timeout || 30,
      })
    }
  }, [isEditMode, mcpIntegration, mcpIntegrationIsLoading, form])

  const resetForm = () => {
    if (isEditMode && mcpIntegration) {
      form.reset({
        name: mcpIntegration.name,
        description: mcpIntegration.description || "",
        server_type: mcpIntegration.server_type,
        server_uri: mcpIntegration.server_uri || "",
        auth_type: mcpIntegration.auth_type,
        oauth_integration_id: mcpIntegration.oauth_integration_id || "",
        custom_credentials: "",
        command: mcpIntegration.command || "",
        command_args: mcpIntegration.command_args?.join("\n") || "",
        command_env: "", // Env vars are not returned from API for security
        timeout: mcpIntegration.timeout || 30,
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
      // Parse command_args from newline-separated string to array
      const commandArgs = values.command_args?.trim()
        ? values.command_args
            .split("\n")
            .map((arg) => arg.trim())
            .filter(Boolean)
        : undefined

      // Parse command_env from JSON string to object
      const commandEnv = values.command_env?.trim()
        ? (JSON.parse(values.command_env) as Record<string, string>)
        : undefined

      const baseParams = {
        name: values.name.trim(),
        description: values.description?.trim() || undefined,
        server_type: values.server_type,
        timeout: values.timeout,
      }

      const urlParams =
        values.server_type === "url"
          ? {
              server_uri: values.server_uri?.trim(),
              auth_type: values.auth_type,
              oauth_integration_id:
                values.auth_type === "OAUTH2" && values.oauth_integration_id
                  ? values.oauth_integration_id
                  : undefined,
              custom_credentials:
                values.auth_type === "CUSTOM" && values.custom_credentials
                  ? values.custom_credentials.trim()
                  : undefined,
            }
          : {}

      const commandParams =
        values.server_type === "command"
          ? {
              command: values.command?.trim(),
              command_args: commandArgs,
              command_env: commandEnv,
            }
          : {}

      const params = { ...baseParams, ...urlParams, ...commandParams }

      if (isEditMode && mcpIntegrationId) {
        await updateMcpIntegration({
          mcpIntegrationId,
          params,
        })
      } else {
        await createMcpIntegration(params)
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

              {/* Server type selector */}
              <FormField
                control={form.control}
                name="server_type"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Server type</FormLabel>
                    <FormControl>
                      <Select
                        value={field.value}
                        onValueChange={field.onChange}
                        disabled={isEditMode}
                      >
                        <SelectTrigger>
                          <SelectValue placeholder="Select server type">
                            {field.value
                              ? SERVER_TYPES.find(
                                  (opt) => opt.value === field.value
                                )?.label
                              : null}
                          </SelectValue>
                        </SelectTrigger>
                        <SelectContent>
                          {SERVER_TYPES.map((option) => (
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
                      {isEditMode
                        ? "Server type cannot be changed after creation."
                        : "Choose how to connect to the MCP server."}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* URL-type fields */}
              {serverType === "url" && (
                <>
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
                </>
              )}

              {/* Command-type fields */}
              {serverType === "command" && (
                <>
                  <FormField
                    control={form.control}
                    name="command"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Command</FormLabel>
                        <FormControl>
                          <Select
                            value={field.value || ""}
                            onValueChange={field.onChange}
                          >
                            <SelectTrigger>
                              <SelectValue placeholder="Select command">
                                {field.value || null}
                              </SelectValue>
                            </SelectTrigger>
                            <SelectContent>
                              {ALLOWED_COMMANDS.map((cmd) => (
                                <SelectItem key={cmd} value={cmd}>
                                  {cmd}
                                </SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </FormControl>
                        <FormDescription className="text-xs">
                          Command to run the MCP server. Only{" "}
                          {ALLOWED_COMMANDS.join(", ")} are allowed.
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="command_args"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Arguments</FormLabel>
                        <FormControl>
                          <Textarea
                            placeholder="@modelcontextprotocol/server-github"
                            className="font-mono text-sm min-h-[80px]"
                            {...field}
                          />
                        </FormControl>
                        <FormDescription className="text-xs">
                          One argument per line (e.g., package name for npx).
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={form.control}
                    name="command_env"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel>Environment variables</FormLabel>
                        <FormControl>
                          <Textarea
                            placeholder='{"GITHUB_TOKEN": "ghp_xxx"}'
                            className="font-mono text-sm min-h-[80px]"
                            {...field}
                          />
                        </FormControl>
                        <FormDescription className="text-xs">
                          JSON object with environment variables for the
                          command.
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                </>
              )}

              {/* Timeout field for both types */}
              <FormField
                control={form.control}
                name="timeout"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Timeout (seconds)</FormLabel>
                    <FormControl>
                      <Input
                        type="number"
                        min={1}
                        max={300}
                        placeholder="30"
                        {...field}
                      />
                    </FormControl>
                    <FormDescription className="text-xs">
                      {serverType === "command"
                        ? "Process timeout in seconds (1-300)."
                        : "HTTP request timeout in seconds (1-300)."}
                    </FormDescription>
                    <FormMessage />
                  </FormItem>
                )}
              />

              {/* OAuth 2.0 Fields (only for URL type) */}
              {serverType === "url" && authType === "OAUTH2" && (
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

              {/* Custom Credentials Fields (only for URL type) */}
              {serverType === "url" && authType === "CUSTOM" && (
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
