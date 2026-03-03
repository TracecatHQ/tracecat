"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2, Plus, Trash2 } from "lucide-react"
import React, { useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type {
  MCPHttpIntegrationCreate,
  MCPIntegrationUpdate,
  MCPStdioIntegrationCreate,
} from "@/client/types.gen"
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
    value: "http",
    label: "URL (HTTP/SSE)",
    description: "Connect to an MCP server via HTTP or SSE endpoint",
  },
  {
    value: "stdio",
    label: "Stdio",
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

function isAllowedCommand(
  command: string
): command is (typeof ALLOWED_COMMANDS)[number] {
  return ALLOWED_COMMANDS.includes(command as (typeof ALLOWED_COMMANDS)[number])
}

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
    server_type: z.enum(["http", "stdio"]),
    // HTTP-type fields
    server_uri: z.string().trim().optional().or(z.literal("")),
    auth_type: z.enum(["OAUTH2", "CUSTOM", "NONE"]),
    oauth_integration_id: z.string().uuid().optional().or(z.literal("")),
    custom_credentials: z.string().trim().optional().or(z.literal("")),
    // Stdio-type fields
    stdio_command: z.string().trim().optional().or(z.literal("")),
    stdio_args: z.array(
      z.object({
        value: z.string(),
      })
    ),
    stdio_env: z.string().trim().optional().or(z.literal("")),
    // General fields
    timeout: z.coerce.number().int().min(1).max(300).optional(),
  })
  // HTTP-type validation
  .refine(
    (data) => {
      if (data.server_type === "http") {
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
      message: "Valid server URL is required for HTTP-type servers",
      path: ["server_uri"],
    }
  )
  .refine(
    (data) => {
      if (data.server_type === "http" && data.auth_type === "OAUTH2") {
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
      if (data.server_type === "http" && data.auth_type === "CUSTOM") {
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
  // Stdio-type validation
  .refine(
    (data) => {
      if (data.server_type === "stdio") {
        if (!data.stdio_command || data.stdio_command.trim() === "") {
          return false
        }
        const cmd = data.stdio_command.trim()
        return isAllowedCommand(cmd)
      }
      return true
    },
    {
      message: `Command must be one of: ${ALLOWED_COMMANDS.join(", ")}`,
      path: ["stdio_command"],
    }
  )
  .refine(
    (data) => {
      if (
        data.server_type === "stdio" &&
        data.stdio_env &&
        data.stdio_env.trim() !== ""
      ) {
        try {
          const parsed = JSON.parse(data.stdio_env) as unknown
          if (
            typeof parsed !== "object" ||
            parsed === null ||
            Array.isArray(parsed)
          ) {
            return false
          }
          // Validate all values are strings (API expects Record<string, string>)
          for (const value of Object.values(parsed)) {
            if (typeof value !== "string") {
              return false
            }
          }
          return true
        } catch {
          return false
        }
      }
      return true
    },
    {
      message:
        "Environment variables must be a valid JSON object with string values only",
      path: ["stdio_env"],
    }
  )

type MCPIntegrationFormValues = z.infer<typeof formSchema>

const DEFAULT_VALUES: MCPIntegrationFormValues = {
  name: "",
  description: "",
  server_type: "http",
  server_uri: "",
  auth_type: "NONE",
  oauth_integration_id: "",
  custom_credentials: "",
  stdio_command: "",
  stdio_args: [],
  stdio_env: "",
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
  const [isEditHydrated, setIsEditHydrated] = useState(false)
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
  const {
    fields: stdioArgFields,
    append: appendStdioArg,
    remove: removeStdioArg,
    replace: replaceStdioArgs,
  } = useFieldArray({
    control: form.control,
    name: "stdio_args",
  })

  const serverType = form.watch("server_type")
  const authType = form.watch("auth_type")

  React.useEffect(() => {
    if (!isEditMode) {
      setIsEditHydrated(true)
      return
    }
    if (open) {
      setIsEditHydrated(false)
    }
  }, [isEditMode, open, mcpIntegrationId])

  // Load integration data when in edit mode
  React.useEffect(() => {
    if (isEditMode && mcpIntegration && !mcpIntegrationIsLoading) {
      if (mcpIntegration.id !== mcpIntegrationId) {
        return
      }
      const hydratedStdioArgs =
        mcpIntegration.stdio_args?.map((arg) => ({ value: arg })) || []
      form.reset({
        name: mcpIntegration.name,
        description: mcpIntegration.description || "",
        server_type: mcpIntegration.server_type,
        server_uri: mcpIntegration.server_uri || "",
        auth_type: mcpIntegration.auth_type,
        oauth_integration_id: mcpIntegration.oauth_integration_id || "",
        custom_credentials: "", // Don't populate for security
        stdio_command: mcpIntegration.stdio_command || "",
        stdio_args: hydratedStdioArgs,
        stdio_env: "", // Env vars are not returned from API for security
        timeout: mcpIntegration.timeout || 30,
      })
      // Explicitly sync field-array state; reset() can lag on first mount.
      replaceStdioArgs(hydratedStdioArgs)
      setIsEditHydrated(true)
    }
  }, [
    isEditMode,
    mcpIntegration,
    mcpIntegrationIsLoading,
    mcpIntegrationId,
    form,
    replaceStdioArgs,
  ])

  const resetForm = () => {
    if (
      isEditMode &&
      mcpIntegration &&
      mcpIntegration.id === mcpIntegrationId
    ) {
      const hydratedStdioArgs =
        mcpIntegration.stdio_args?.map((arg) => ({ value: arg })) || []
      form.reset({
        name: mcpIntegration.name,
        description: mcpIntegration.description || "",
        server_type: mcpIntegration.server_type,
        server_uri: mcpIntegration.server_uri || "",
        auth_type: mcpIntegration.auth_type,
        oauth_integration_id: mcpIntegration.oauth_integration_id || "",
        custom_credentials: "",
        stdio_command: mcpIntegration.stdio_command || "",
        stdio_args: hydratedStdioArgs,
        stdio_env: "", // Env vars are not returned from API for security
        timeout: mcpIntegration.timeout || 30,
      })
      replaceStdioArgs(hydratedStdioArgs)
      setIsEditHydrated(true)
    } else {
      form.reset(DEFAULT_VALUES)
      replaceStdioArgs([])
      setIsEditHydrated(false)
    }
  }

  const handleOpenChange = (nextOpen: boolean) => {
    if (controlledOpen === undefined) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
    if (!nextOpen) {
      resetForm()
      setIsEditHydrated(false)
    }
  }

  const onSubmit = async (values: MCPIntegrationFormValues) => {
    try {
      // Parse stdio_args list to string array
      const stdioArgs = values.stdio_args
        .map((arg) => arg.value.trim())
        .filter(Boolean)

      // Parse stdio_env from JSON string to object (only for stdio-type servers)
      const stdioEnv =
        values.server_type === "stdio" && values.stdio_env?.trim()
          ? (JSON.parse(values.stdio_env) as Record<string, string>)
          : undefined

      const baseParams = {
        name: values.name.trim(),
        description: values.description?.trim() || undefined,
        timeout: values.timeout,
      }
      if (isEditMode && mcpIntegrationId) {
        const params: MCPIntegrationUpdate =
          values.server_type === "stdio"
            ? {
                ...baseParams,
                stdio_command: values.stdio_command?.trim() ?? "",
                stdio_args: stdioArgs,
                stdio_env: stdioEnv,
              }
            : {
                ...baseParams,
                server_uri: values.server_uri?.trim() ?? "",
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
        await updateMcpIntegration({
          mcpIntegrationId,
          params,
        })
      } else {
        if (values.server_type === "stdio") {
          const params: MCPStdioIntegrationCreate = {
            ...baseParams,
            server_type: "stdio",
            stdio_command: values.stdio_command?.trim() ?? "",
            stdio_args: stdioArgs.length > 0 ? stdioArgs : undefined,
            stdio_env: stdioEnv,
          }
          await createMcpIntegration(params)
        } else {
          const params: MCPHttpIntegrationCreate = {
            ...baseParams,
            server_type: "http",
            server_uri: values.server_uri?.trim() ?? "",
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
          await createMcpIntegration(params)
        }
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
        {(integrationsIsLoading ||
          (isEditMode &&
            (!isEditHydrated ||
              mcpIntegrationIsLoading ||
              mcpIntegration?.id !== mcpIntegrationId))) && (
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
          </div>
        )}
        {!integrationsIsLoading &&
          (!isEditMode ||
            (isEditHydrated &&
              !mcpIntegrationIsLoading &&
              mcpIntegration?.id === mcpIntegrationId)) && (
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

                {/* HTTP-type fields */}
                {serverType === "http" && (
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
                            The MCP server endpoint URL. Must use HTTPS
                            (localhost allowed with HTTP).
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

                {/* Stdio-type fields */}
                {serverType === "stdio" && (
                  <>
                    <FormField
                      control={form.control}
                      name="stdio_command"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Stdio command</FormLabel>
                          <FormControl>
                            {(() => {
                              const currentCommand = (field.value || "").trim()
                              const hasLegacyValue =
                                currentCommand !== "" &&
                                !isAllowedCommand(currentCommand)
                              const selectedValue = hasLegacyValue
                                ? "__legacy__"
                                : currentCommand
                              return (
                                <Select
                                  value={selectedValue}
                                  onValueChange={(value) =>
                                    field.onChange(
                                      value === "__legacy__"
                                        ? currentCommand
                                        : value
                                    )
                                  }
                                >
                                  <SelectTrigger>
                                    <SelectValue placeholder="Select stdio command">
                                      {hasLegacyValue
                                        ? `Legacy (${currentCommand})`
                                        : currentCommand || null}
                                    </SelectValue>
                                  </SelectTrigger>
                                  <SelectContent>
                                    {hasLegacyValue && (
                                      <SelectItem value="__legacy__">
                                        Legacy ({currentCommand})
                                      </SelectItem>
                                    )}
                                    {ALLOWED_COMMANDS.map((cmd) => (
                                      <SelectItem key={cmd} value={cmd}>
                                        {cmd}
                                      </SelectItem>
                                    ))}
                                  </SelectContent>
                                </Select>
                              )
                            })()}
                          </FormControl>
                          <FormDescription className="text-xs">
                            Stdio command to run the MCP server. Only{" "}
                            {ALLOWED_COMMANDS.join(", ")} are allowed.
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="stdio_args"
                      render={() => (
                        <FormItem>
                          <FormLabel>Stdio arguments</FormLabel>
                          <div className="space-y-2">
                            {stdioArgFields.length === 0 && (
                              <p className="text-xs text-muted-foreground">
                                No arguments configured.
                              </p>
                            )}
                            {stdioArgFields.map((argField, index) => (
                              <div
                                key={argField.id}
                                className="grid grid-cols-[1fr_auto] gap-2"
                              >
                                <FormField
                                  control={form.control}
                                  name={`stdio_args.${index}.value`}
                                  render={({ field }) => (
                                    <FormItem>
                                      <FormControl>
                                        <Input
                                          {...field}
                                          placeholder={
                                            index === 0
                                              ? "@modelcontextprotocol/server-github"
                                              : "Additional argument"
                                          }
                                          className="font-mono text-sm"
                                        />
                                      </FormControl>
                                      <FormMessage />
                                    </FormItem>
                                  )}
                                />
                                <Button
                                  type="button"
                                  variant="ghost"
                                  size="icon"
                                  onClick={() => removeStdioArg(index)}
                                >
                                  <Trash2 className="size-4" />
                                  <span className="sr-only">
                                    Remove argument
                                  </span>
                                </Button>
                              </div>
                            ))}
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => appendStdioArg({ value: "" })}
                            >
                              <Plus className="mr-2 size-4" />
                              Add argument
                            </Button>
                          </div>
                          <FormDescription className="text-xs">
                            Add each command argument as a separate list item,
                            in order.
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={form.control}
                      name="stdio_env"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>Stdio environment variables</FormLabel>
                          <FormControl>
                            <Textarea
                              placeholder='{"GITHUB_TOKEN": "${{ SECRETS.github.TOKEN }}"}'
                              className="font-mono text-sm min-h-[80px]"
                              {...field}
                            />
                          </FormControl>
                          <FormDescription className="text-xs">
                            JSON object with environment variables for the stdio
                            command. Template expressions are supported.
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
                        {serverType === "stdio"
                          ? "Process timeout in seconds (1-300)."
                          : "HTTP request timeout in seconds (1-300)."}
                      </FormDescription>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {/* OAuth 2.0 Fields (only for HTTP type) */}
                {serverType === "http" && authType === "OAUTH2" && (
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
                                        provider?.name ||
                                        integration.provider_id
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

                {/* Custom Credentials Fields (only for HTTP type) */}
                {serverType === "http" && authType === "CUSTOM" && (
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
                    <Button
                      type="submit"
                      className="gap-2"
                      disabled={isPending}
                    >
                      {isPending && (
                        <Loader2 className="h-4 w-4 animate-spin" />
                      )}
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
