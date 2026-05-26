"use client"

import { useMutation } from "@tanstack/react-query"
import { KeyRound, Loader2, Lock, Plus, Trash2, Wrench } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { CatalogAuthOption, CatalogCredentialField } from "@/client"
import { integrationsConnectProvider } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import type { TracecatApiError } from "@/lib/errors"
import { useCreateConnection } from "@/lib/hooks/integrations-catalog"
import { cn } from "@/lib/utils"

interface ConnectDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
  integrationId: string
  namespace: string
  displayName: string
  authOptions: CatalogAuthOption[]
  defaultAuthMethod?: CatalogAuthOption["auth_method"]
  onConfigure?: (option: CatalogAuthOption) => void
}

function optionKey(option: CatalogAuthOption): string {
  return [
    option.auth_method,
    option.provider_id ?? "static",
    option.grant_type ?? "none",
  ].join(":")
}

function isVisibleAuthOption(option: CatalogAuthOption): boolean {
  return (
    option.enabled !== false &&
    (option.auth_method === "oauth_auth_code" ||
      option.auth_method === "oauth_client_credentials" ||
      option.auth_method === "service_account" ||
      option.auth_method === "static_kv")
  )
}

function isBlockedByMissingConfig(option: CatalogAuthOption): boolean {
  return option.requires_config === true && option.status === "not_configured"
}

function isActionableAuthOption(option: CatalogAuthOption): boolean {
  if (option.auth_method === "static_kv") {
    return true
  }
  if (option.auth_method === "oauth_auth_code") {
    return !isBlockedByMissingConfig(option)
  }
  return option.status === "connected" || option.status === "configured"
}

function authOptionOrder(option: CatalogAuthOption): number {
  if (isActionableAuthOption(option)) {
    return 0
  }
  if (isBlockedByMissingConfig(option)) {
    return 1
  }
  return 2
}

function AuthOptionIcon({ option }: { option: CatalogAuthOption }) {
  if (option.auth_method.startsWith("oauth")) {
    return <Lock className="size-3.5" />
  }
  return <KeyRound className="size-3.5" />
}

function optionStatusLabel(option: CatalogAuthOption): string | null {
  if (option.requires_config === true && option.status === "not_configured") {
    return null
  }
  if (option.status === "connected") {
    return "Connected"
  }
  if (option.status === "configured") {
    return "Configured"
  }
  return null
}

function AuthOptionStatusBadge({ option }: { option: CatalogAuthOption }) {
  const label = optionStatusLabel(option)
  if (!label) return null

  const connected = option.status === "connected"

  return (
    <Badge
      variant="outline"
      className={cn(
        "h-5 shrink-0 px-1.5 text-[10px] font-medium",
        connected && "border-emerald-400/50 bg-emerald-500/10 text-emerald-700"
      )}
    >
      {label}
    </Badge>
  )
}

export function ConnectDialog({
  open,
  onOpenChange,
  workspaceId,
  integrationId,
  namespace,
  displayName,
  authOptions,
  defaultAuthMethod,
  onConfigure,
}: ConnectDialogProps) {
  const visibleOptions = useMemo(
    () =>
      [...authOptions]
        .filter(isVisibleAuthOption)
        .sort((a, b) => authOptionOrder(a) - authOptionOrder(b)),
    [authOptions]
  )
  const defaultKey = useMemo(() => {
    if (visibleOptions.length === 0) {
      return ""
    }
    const preferred = visibleOptions.find(
      (option) => option.auth_method === defaultAuthMethod
    )
    const actionable = visibleOptions.find(isActionableAuthOption)
    return optionKey(preferred ?? actionable ?? visibleOptions[0])
  }, [visibleOptions, defaultAuthMethod])
  const [selectedKey, setSelectedKey] = useState(defaultKey)

  useEffect(() => {
    if (open) {
      setSelectedKey(defaultKey)
    }
  }, [defaultKey, open])

  const selectedOption =
    visibleOptions.find((option) => optionKey(option) === selectedKey) ??
    visibleOptions[0]

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle>Connect {displayName}</DialogTitle>
          <DialogDescription>
            Choose an authentication method supported by this integration.
          </DialogDescription>
        </DialogHeader>

        {visibleOptions.length === 0 || !selectedOption ? (
          <div className="space-y-3">
            <p className="text-sm text-muted-foreground">
              This integration does not expose a user connection flow.
            </p>
            <DialogFooter>
              <Button variant="outline" onClick={() => onOpenChange(false)}>
                Close
              </Button>
            </DialogFooter>
          </div>
        ) : visibleOptions.length === 1 ? (
          <ConnectOptionPanel
            option={selectedOption}
            workspaceId={workspaceId}
            integrationId={integrationId}
            namespace={namespace}
            displayName={displayName}
            onConfigure={onConfigure}
            onSuccess={() => onOpenChange(false)}
          />
        ) : (
          <div className="space-y-4">
            <div className="grid gap-2">
              {visibleOptions.map((option) => {
                const key = optionKey(option)
                const selected = key === selectedKey
                return (
                  <button
                    key={key}
                    type="button"
                    className={cn(
                      "flex items-start gap-3 rounded-md border px-3 py-2 text-left transition-colors",
                      selected
                        ? "border-foreground/50 bg-muted/40"
                        : "border-border hover:border-foreground/30 hover:bg-muted/20"
                    )}
                    onClick={() => setSelectedKey(key)}
                    aria-pressed={selected}
                  >
                    <span className="mt-0.5 text-muted-foreground">
                      <AuthOptionIcon option={option} />
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block text-sm font-medium leading-5">
                        {option.label}
                      </span>
                      {option.description ? (
                        <span className="line-clamp-2 text-xs text-muted-foreground">
                          {option.description}
                        </span>
                      ) : null}
                    </span>
                    <AuthOptionStatusBadge option={option} />
                  </button>
                )
              })}
            </div>
            <ConnectOptionPanel
              option={selectedOption}
              workspaceId={workspaceId}
              integrationId={integrationId}
              namespace={namespace}
              displayName={displayName}
              onConfigure={onConfigure}
              onSuccess={() => onOpenChange(false)}
            />
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

function ConnectOptionPanel({
  option,
  workspaceId,
  integrationId,
  namespace,
  displayName,
  onConfigure,
  onSuccess,
}: {
  option: CatalogAuthOption
  workspaceId: string
  integrationId: string
  namespace: string
  displayName: string
  onConfigure?: (option: CatalogAuthOption) => void
  onSuccess: () => void
}) {
  if (option.auth_method === "oauth_auth_code") {
    return (
      <OAuthOptionPanel
        option={option}
        workspaceId={workspaceId}
        namespace={namespace}
        displayName={displayName}
        onConfigure={onConfigure}
        onSuccess={onSuccess}
      />
    )
  }
  if (option.auth_method === "static_kv") {
    return (
      <StaticKVOptionPanel
        option={option}
        workspaceId={workspaceId}
        integrationId={integrationId}
        onSuccess={onSuccess}
      />
    )
  }
  return (
    <ProviderConfigOptionPanel
      option={option}
      displayName={displayName}
      onConfigure={onConfigure}
    />
  )
}

function ProviderConfigOptionPanel({
  option,
  displayName,
  onConfigure,
}: {
  option: CatalogAuthOption
  displayName: string
  onConfigure?: (option: CatalogAuthOption) => void
}) {
  const needsConfiguration =
    option.requires_config === true && option.status === "not_configured"
  return (
    <div className="space-y-3">
      {option.description ? (
        <p className="text-sm text-muted-foreground">{option.description}</p>
      ) : (
        <p className="text-sm text-muted-foreground">
          This method uses workspace-level credentials for {displayName}.
        </p>
      )}
      <DialogFooter>
        <Button
          onClick={() => onConfigure?.(option)}
          disabled={!onConfigure}
          className="gap-2"
        >
          <Wrench className="size-4" />
          {needsConfiguration ? "Configure" : "Edit configuration"}
        </Button>
      </DialogFooter>
    </div>
  )
}

function OAuthOptionPanel({
  option,
  workspaceId,
  namespace,
  displayName,
  onConfigure,
  onSuccess,
}: {
  option: CatalogAuthOption
  workspaceId: string
  namespace: string
  displayName: string
  onConfigure?: (option: CatalogAuthOption) => void
  onSuccess: () => void
}) {
  const providerId = option.provider_id ?? namespace
  const needsConfiguration =
    option.requires_config === true && option.status === "not_configured"
  const { mutate, isPending } = useMutation({
    mutationFn: async () =>
      await integrationsConnectProvider({
        workspaceId,
        providerId,
      }),
    onSuccess: (result) => {
      onSuccess()
      window.location.href = result.auth_url
    },
    onError: (error: TracecatApiError) => {
      toast({
        title: "Failed to start OAuth",
        description: String(error.body?.detail ?? error.message),
        variant: "destructive",
      })
    },
  })

  return (
    <div className="space-y-3">
      {option.description ? (
        <p className="text-sm text-muted-foreground">{option.description}</p>
      ) : (
        <p className="text-sm text-muted-foreground">
          You will be redirected to {displayName} to authorize access.
        </p>
      )}
      <DialogFooter>
        {needsConfiguration ? (
          <Button
            onClick={() => onConfigure?.(option)}
            disabled={!onConfigure}
            className="gap-2"
          >
            <Wrench className="size-4" />
            Configure
          </Button>
        ) : (
          <Button onClick={() => mutate()} disabled={isPending}>
            {isPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Lock className="mr-2 size-4" />
            )}
            Continue to {displayName}
          </Button>
        )}
      </DialogFooter>
    </div>
  )
}

function StaticKVOptionPanel({
  option,
  workspaceId,
  integrationId,
  onSuccess,
}: {
  option: CatalogAuthOption
  workspaceId: string
  integrationId: string
  onSuccess: () => void
}) {
  const fields = option.fields ?? []
  const [environment, setEnvironment] = useState("default")
  const [values, setValues] = useState<Record<string, string>>({})
  const [extraKeys, setExtraKeys] = useState<
    Array<{ id: number; key: string; value: string }>
  >([])
  const { createConnection, createConnectionIsPending } = useCreateConnection(
    workspaceId,
    integrationId
  )

  useEffect(() => {
    setEnvironment("default")
    setValues({})
    setExtraKeys([])
  }, [option])

  const submit = async () => {
    const missing = fields.filter(
      (field) => field.required !== false && !values[field.key]?.trim()
    )
    if (missing.length > 0) {
      toast({
        title: "Missing credential fields",
        description: missing.map((field) => field.label).join(", "),
        variant: "destructive",
      })
      return
    }

    const declaredKeys = Object.fromEntries(
      fields
        .map((field) => [field.key, values[field.key]?.trim() ?? ""] as const)
        .filter(([, value]) => value.length > 0)
    )
    const completeExtraKeys = extraKeys
      .map((item) => ({
        key: item.key.trim(),
        value: item.value.trim(),
      }))
      .filter((item) => item.key.length > 0 || item.value.length > 0)
    const incompleteExtraKey = completeExtraKeys.find(
      (item) => item.key.length === 0 || item.value.length === 0
    )
    if (incompleteExtraKey) {
      toast({
        title: "Incomplete credential key",
        description: "Additional keys require both a key and a value.",
        variant: "destructive",
      })
      return
    }
    const declaredFieldKeys = new Set(fields.map((field) => field.key))
    const duplicateKey = completeExtraKeys.find(
      (item, index) =>
        declaredFieldKeys.has(item.key) ||
        completeExtraKeys.findIndex((other) => other.key === item.key) !== index
    )
    if (duplicateKey) {
      toast({
        title: "Duplicate credential key",
        description: `${duplicateKey.key} is already defined.`,
        variant: "destructive",
      })
      return
    }

    const keys = {
      ...declaredKeys,
      ...Object.fromEntries(
        completeExtraKeys.map((item) => [item.key, item.value] as const)
      ),
    }
    if (Object.keys(keys).length === 0) {
      toast({
        title: "No credential keys",
        description: "Add at least one credential key before saving.",
        variant: "destructive",
      })
      return
    }

    await createConnection({
      auth_method: "static_kv",
      environment: environment.trim() || "default",
      keys,
    })
    setEnvironment("default")
    setValues({})
    setExtraKeys([])
    onSuccess()
  }

  return (
    <div className="space-y-3">
      {option.description ? (
        <p className="text-sm text-muted-foreground">{option.description}</p>
      ) : null}
      <div className="space-y-1.5">
        <Label htmlFor={`${optionKey(option)}-environment`}>Environment</Label>
        <Input
          id={`${optionKey(option)}-environment`}
          placeholder="default"
          value={environment}
          onChange={(event) => setEnvironment(event.target.value)}
        />
        <p className="text-[11px] text-muted-foreground">
          Workflows use this environment to select the right credentials.
        </p>
      </div>
      {fields.map((field) => (
        <CredentialFieldInput
          key={field.key}
          field={field}
          value={values[field.key] ?? ""}
          inputId={`${optionKey(option)}-${field.key}`}
          onChange={(value) =>
            setValues((prev) => ({ ...prev, [field.key]: value }))
          }
        />
      ))}
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Label>Additional keys</Label>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="h-7 gap-1.5 px-2 text-xs"
            onClick={() =>
              setExtraKeys((prev) => [
                ...prev,
                { id: Date.now() + Math.random(), key: "", value: "" },
              ])
            }
          >
            <Plus className="size-3.5" />
            Add key
          </Button>
        </div>
        {extraKeys.length > 0 ? (
          <div className="flex flex-col gap-2">
            {extraKeys.map((item) => (
              <div key={item.id} className="flex items-center gap-2">
                <Input
                  placeholder="Key"
                  value={item.key}
                  onChange={(event) =>
                    setExtraKeys((prev) =>
                      prev.map((prevItem) =>
                        prevItem.id === item.id
                          ? { ...prevItem, key: event.target.value }
                          : prevItem
                      )
                    )
                  }
                />
                <Input
                  type="password"
                  placeholder="Value"
                  value={item.value}
                  onChange={(event) =>
                    setExtraKeys((prev) =>
                      prev.map((prevItem) =>
                        prevItem.id === item.id
                          ? { ...prevItem, value: event.target.value }
                          : prevItem
                      )
                    )
                  }
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-8 shrink-0"
                  onClick={() =>
                    setExtraKeys((prev) =>
                      prev.filter((prevItem) => prevItem.id !== item.id)
                    )
                  }
                  aria-label={`Remove additional key ${item.key || "row"}`}
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-[11px] text-muted-foreground">
            Add optional keys that are not part of the default template.
          </p>
        )}
      </div>
      <DialogFooter>
        <Button
          onClick={submit}
          disabled={
            (fields.length === 0 && extraKeys.length === 0) ||
            createConnectionIsPending
          }
        >
          {createConnectionIsPending ? (
            <Loader2 className="mr-2 size-4 animate-spin" />
          ) : null}
          Save connection
        </Button>
      </DialogFooter>
    </div>
  )
}

function CredentialFieldInput({
  field,
  value,
  inputId,
  onChange,
}: {
  field: CatalogCredentialField
  value: string
  inputId: string
  onChange: (value: string) => void
}) {
  const label =
    field.required === false ? `${field.label} (optional)` : field.label
  return (
    <div className="space-y-1.5">
      <Label htmlFor={inputId}>{label}</Label>
      {field.multiline ? (
        <Textarea
          id={inputId}
          rows={8}
          className="font-mono text-xs"
          placeholder={field.placeholder ?? field.key}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      ) : (
        <Input
          id={inputId}
          type={field.secret === false ? "text" : "password"}
          placeholder={field.placeholder ?? field.key}
          value={value}
          onChange={(event) => onChange(event.target.value)}
        />
      )}
      {field.description ? (
        <p className="text-[11px] text-muted-foreground">{field.description}</p>
      ) : null}
    </div>
  )
}
