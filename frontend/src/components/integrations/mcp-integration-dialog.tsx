"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Loader2, Plus, Trash2 } from "lucide-react"
import React, { useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import type {
  MCPHttpIntegrationCreate,
  MCPIntegrationUpdate,
  MCPStdioIntegrationCreate,
} from "@/client/types.gen"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { ProviderIcon } from "@/components/icons"
import {
  ALLOWED_COMMANDS,
  AUTH_TYPES,
  isAllowedCommand,
  MCP_INTEGRATION_FORM_DEFAULTS,
  type MCPIntegrationFormValues,
  mcpIntegrationFormSchema,
  SERVER_TYPES,
} from "@/components/integrations/mcp-integration-schema"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
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
  useDeleteMcpIntegration,
  useGetMcpIntegration,
  useIntegrations,
  useUpdateMcpIntegration,
} from "@/lib/hooks"
import { isMcpProvider } from "@/lib/integrations"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

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
  const { deleteMcpIntegration, deleteMcpIntegrationIsPending } =
    useDeleteMcpIntegration(workspaceId)
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
    resolver: zodResolver(mcpIntegrationFormSchema),
    defaultValues: MCP_INTEGRATION_FORM_DEFAULTS,
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
      form.reset(MCP_INTEGRATION_FORM_DEFAULTS)
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
      const trimmedCustomCredentials = values.custom_credentials?.trim() ?? ""
      const customCredentialsWasEdited = Boolean(
        form.formState.dirtyFields.custom_credentials
      )

      let customCredentialsForCreate: string | undefined
      if (values.auth_type !== "NONE" && trimmedCustomCredentials !== "") {
        customCredentialsForCreate = trimmedCustomCredentials
      }

      let customCredentialsForUpdate: string | undefined
      if (values.auth_type !== "NONE") {
        if (trimmedCustomCredentials !== "") {
          customCredentialsForUpdate = trimmedCustomCredentials
        } else if (customCredentialsWasEdited) {
          // Explicitly send an empty string when a user clears the editor in edit mode.
          customCredentialsForUpdate = ""
        }
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
                custom_credentials: customCredentialsForUpdate,
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
            custom_credentials: customCredentialsForCreate,
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
                                          className="font-mono text-xs"
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
                            <CodeEditor
                              value={field.value || ""}
                              onChange={field.onChange}
                              language="json"
                              className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[80px]"
                            />
                          </FormControl>
                          <FormDescription className="text-xs">
                            JSON object with environment variables for the stdio
                            command. Template expressions are supported, for
                            example{" "}
                            <code>
                              {
                                '{"GITHUB_TOKEN": "${{ SECRETS.github.TOKEN }}"}'
                              }
                            </code>
                            .
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
                                        isMcpProvider(integration.provider_id)
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
                                        !isMcpProvider(int.provider_id)
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
                                        !isMcpProvider(int.provider_id)
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

                {/* Header credentials field (HTTP auth types) */}
                {serverType === "http" &&
                  (authType === "CUSTOM" || authType === "OAUTH2") && (
                    <FormField
                      control={form.control}
                      name="custom_credentials"
                      render={({ field }) => (
                        <FormItem>
                          <FormLabel>
                            {authType === "OAUTH2"
                              ? "Additional headers (JSON)"
                              : "Custom credentials (JSON)"}
                          </FormLabel>
                          <FormControl>
                            <CodeEditor
                              value={field.value || ""}
                              onChange={field.onChange}
                              language="json"
                              className="font-mono text-xs [&_.cm-content]:text-xs [&_.cm-editor]:min-h-[120px]"
                            />
                          </FormControl>
                          <FormDescription className="text-xs">
                            {authType === "OAUTH2"
                              ? "Optional: add extra request headers as JSON. Authorization is set from OAuth and cannot be overridden."
                              : "Enter headers as a JSON object, for example "}
                            {authType !== "OAUTH2" && (
                              <code>{`{"Authorization":"Bearer token123"}`}</code>
                            )}
                          </FormDescription>
                          <FormMessage />
                        </FormItem>
                      )}
                    />
                  )}

                <DialogFooter className="flex flex-col gap-2 sm:flex-row sm:justify-between">
                  {isEditMode && mcpIntegrationId ? (
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button
                          type="button"
                          variant="outline"
                          className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                          disabled={isPending || deleteMcpIntegrationIsPending}
                        >
                          <Trash2 className="mr-1.5 h-3.5 w-3.5" />
                          Delete
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>
                            Remove MCP server?
                          </AlertDialogTitle>
                          <AlertDialogDescription>
                            Agents will no longer be able to call{" "}
                            <span className="font-medium">
                              {mcpIntegration?.name ?? "this server"}
                            </span>
                            .
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction
                            className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            disabled={deleteMcpIntegrationIsPending}
                            onClick={async (event) => {
                              event.preventDefault()
                              await deleteMcpIntegration(mcpIntegrationId)
                              handleOpenChange(false)
                            }}
                          >
                            {deleteMcpIntegrationIsPending && (
                              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            )}
                            Remove
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  ) : (
                    <span />
                  )}
                  <div className="flex items-center justify-end gap-2">
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
