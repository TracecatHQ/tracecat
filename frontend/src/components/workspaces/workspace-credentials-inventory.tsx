"use client"

import {
  AlertTriangleIcon,
  ChevronRight,
  Link2,
  SquareAsterisk,
  Unlink2,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { SecretDefinition } from "@/client"
import {
  CatalogHeader,
  type CatalogHeaderSelectFilter,
} from "@/components/catalog/catalog-header"
import { SecretIcon } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import {
  Item,
  ItemActions,
  ItemContent,
  ItemMedia,
  ItemTitle,
} from "@/components/ui/item"
import { ScrollArea } from "@/components/ui/scroll-area"
import { CreateCredentialDialog } from "@/components/workspaces/create-credential-dialog"
import {
  buildCredentialGroups,
  type CredentialConnectionFilter,
  type CredentialSecretTypeFilter,
  credentialSecretTypeLabels,
  getCredentialSecretTypeSummary,
  normalizeSecretEnvironment,
} from "@/components/workspaces/credentials-utils"
import {
  DeleteSecretAlertDialog,
  DeleteSecretAlertDialogTrigger,
} from "@/components/workspaces/delete-workspace-secret"
import {
  EditCredentialsDialog,
  EditCredentialsDialogTrigger,
} from "@/components/workspaces/edit-workspace-secret"
import {
  useSecretDefinitions,
  useWorkspaceSecrets,
  type WorkspaceSecretListItem,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const SECRET_TYPE_OPTIONS: Array<{
  value: CredentialSecretTypeFilter
  label: string
}> = [
  { value: "all", label: "All types" },
  { value: "custom", label: "Custom" },
  { value: "ssh-key", label: "SSH key" },
  { value: "mtls", label: "mTLS" },
  { value: "ca-cert", label: "CA certificate" },
  { value: "github-app", label: "GitHub app" },
]

export function WorkspaceCredentialsInventory() {
  const workspaceId = useWorkspaceId()
  const {
    secretDefinitions,
    secretDefinitionsIsLoading,
    secretDefinitionsError,
  } = useSecretDefinitions(workspaceId)
  const { secrets, secretsIsLoading, secretsError } =
    useWorkspaceSecrets(workspaceId)
  const [searchQuery, setSearchQuery] = useState("")
  const [connectionFilter, setConnectionFilter] =
    useState<CredentialConnectionFilter>("all")
  const [hasInitializedConnectionFilter, setHasInitializedConnectionFilter] =
    useState(false)
  const [environmentFilter, setEnvironmentFilter] = useState("all")
  const [secretTypeFilter, setSecretTypeFilter] =
    useState<CredentialSecretTypeFilter>("all")
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(
    {}
  )
  const [selectedSecret, setSelectedSecret] =
    useState<WorkspaceSecretListItem | null>(null)
  const [activeTemplate, setActiveTemplate] = useState<SecretDefinition | null>(
    null
  )

  const allSecrets = secrets ?? []
  const allSecretDefinitions = secretDefinitions ?? []

  const credentialGroups = useMemo(
    () => buildCredentialGroups(allSecretDefinitions, allSecrets),
    [allSecretDefinitions, allSecrets]
  )

  useEffect(() => {
    if (hasInitializedConnectionFilter) {
      return
    }

    if (credentialGroups.some((group) => group.isConnected)) {
      setConnectionFilter("connected")
    }

    setHasInitializedConnectionFilter(true)
  }, [credentialGroups, hasInitializedConnectionFilter])

  const availableEnvironments = useMemo(
    () =>
      Array.from(
        new Set(
          allSecrets.map((secret) =>
            normalizeSecretEnvironment(secret.environment)
          )
        )
      ).sort((a, b) => a.localeCompare(b)),
    [allSecrets]
  )

  const corruptedSecrets = useMemo(
    () => allSecrets.filter((secret) => secret.is_corrupted),
    [allSecrets]
  )

  const filteredGroups = useMemo(() => {
    const normalizedSearch = searchQuery.trim().toLowerCase()

    return credentialGroups.filter((group) => {
      const matchesSearch =
        normalizedSearch.length === 0 ||
        group.name.toLowerCase().includes(normalizedSearch)
      const matchesConnection =
        connectionFilter === "all"
          ? true
          : connectionFilter === "connected"
            ? group.isConnected
            : !group.isConnected
      const matchesEnvironment =
        environmentFilter === "all"
          ? true
          : group.environments.includes(environmentFilter)
      const matchesSecretType =
        secretTypeFilter === "all" ||
        group.secretTypes.includes(secretTypeFilter)

      return (
        matchesSearch &&
        matchesConnection &&
        matchesEnvironment &&
        matchesSecretType
      )
    })
  }, [
    connectionFilter,
    credentialGroups,
    environmentFilter,
    searchQuery,
    secretTypeFilter,
  ])

  const selectFilters: CatalogHeaderSelectFilter[] = [
    {
      key: "connection",
      value: connectionFilter,
      onValueChange: (value) =>
        setConnectionFilter(value as CredentialConnectionFilter),
      placeholder: "Connection",
      allValue: "all",
      options: [
        { value: "all", label: "All connections" },
        { value: "connected", label: "Connected", icon: Link2 },
        {
          value: "not_connected",
          label: "Not connected",
          icon: Unlink2,
        },
      ],
    },
    {
      key: "environment",
      value: environmentFilter,
      onValueChange: setEnvironmentFilter,
      placeholder: "Environment",
      allValue: "all",
      widthClassName: "w-[190px]",
      options: [
        { value: "all", label: "All environments" },
        ...availableEnvironments.map((environment) => ({
          value: environment,
          label: environment,
        })),
      ],
    },
    {
      key: "secret-type",
      value: secretTypeFilter,
      onValueChange: (value) =>
        setSecretTypeFilter(value as CredentialSecretTypeFilter),
      placeholder: "Secret type",
      allValue: "all",
      widthClassName: "w-[170px]",
      options: SECRET_TYPE_OPTIONS,
    },
  ]

  if (secretDefinitionsIsLoading || secretsIsLoading) {
    return <CenteredSpinner />
  }

  if (secretDefinitionsError || secretsError) {
    return (
      <AlertNotification
        level="error"
        message={
          secretDefinitionsError?.message ||
          secretsError?.message ||
          "Error loading credentials."
        }
      />
    )
  }

  return (
    <>
      <DeleteSecretAlertDialog
        selectedSecret={selectedSecret}
        setSelectedSecret={setSelectedSecret}
      >
        <EditCredentialsDialog
          selectedSecret={selectedSecret}
          setSelectedSecret={setSelectedSecret}
        >
          <div className="flex h-full min-h-0 flex-col">
            {corruptedSecrets.length > 0 ? (
              <Alert className="mx-6 mb-4 mt-6">
                <AlertTriangleIcon className="size-4 !text-amber-600" />
                <AlertTitle>Some secrets could not be decrypted</AlertTitle>
                <AlertDescription>
                  Failed to decrypt key names and values for{" "}
                  {corruptedSecrets.map((secret) => secret.name).join(", ")}.
                  Secret names are still available, but you must re-enter all
                  key names and values to recover these secrets. For SSH keys,
                  delete and recreate the secret.
                </AlertDescription>
              </Alert>
            ) : null}

            <CatalogHeader
              searchQuery={searchQuery}
              onSearchChange={setSearchQuery}
              searchPlaceholder="Search credentials..."
              selectFilters={selectFilters}
              displayCount={filteredGroups.length}
              countLabel="credentials"
            />

            <ScrollArea className="flex-1 min-h-0 [&>[data-radix-scroll-area-viewport]]:[scrollbar-width:none] [&>[data-radix-scroll-area-viewport]::-webkit-scrollbar]:hidden [&>[data-orientation=vertical]]:!hidden [&>[data-orientation=horizontal]]:!hidden">
              <div className="w-full pb-10">
                {filteredGroups.map((group) => {
                  const isExpandable = group.secrets.length > 0
                  const isExpanded = expandedGroups[group.name] ?? false

                  return (
                    <Collapsible
                      key={group.name}
                      open={isExpanded}
                      onOpenChange={(nextOpen) =>
                        setExpandedGroups((prev) => ({
                          ...prev,
                          [group.name]: nextOpen,
                        }))
                      }
                    >
                      <div className="border-b border-border/50">
                        <div
                          className={cn(
                            "flex items-center gap-2 px-3 py-1.5 transition-colors",
                            isExpandable && "hover:bg-muted/50"
                          )}
                        >
                          {isExpandable ? (
                            <CollapsibleTrigger asChild>
                              <button
                                type="button"
                                className="flex min-w-0 flex-1 items-center gap-2 text-left [&[data-state=open]_.chevron]:rotate-90"
                              >
                                <div className="flex h-7 w-7 shrink-0 items-center justify-center">
                                  <ChevronRight className="chevron size-4 text-muted-foreground transition-transform duration-200" />
                                </div>
                                <CredentialGroupContent
                                  name={group.name}
                                  label={
                                    group.isPrebuilt
                                      ? "Credential"
                                      : credentialSecretTypeLabels[
                                          group.secretType
                                        ]
                                  }
                                  environments={group.environments}
                                  isConnected={group.isConnected}
                                  secretTypeSummary={getCredentialSecretTypeSummary(
                                    group
                                  )}
                                />
                              </button>
                            </CollapsibleTrigger>
                          ) : (
                            <>
                              <div className="flex h-7 w-7 shrink-0 items-center justify-center" />
                              <div className="min-w-0 flex-1">
                                <CredentialGroupContent
                                  name={group.name}
                                  label="Credential"
                                  environments={group.environments}
                                  isConnected={group.isConnected}
                                  secretTypeSummary={getCredentialSecretTypeSummary(
                                    group
                                  )}
                                />
                              </div>
                            </>
                          )}

                          <ItemActions className="ml-auto flex shrink-0 items-center gap-1.5 pl-3">
                            {group.template ? (
                              <Button
                                variant="outline"
                                size="sm"
                                className="h-6 border-input bg-white px-2.5 text-[11px] text-foreground hover:bg-muted"
                                onClick={(event) => {
                                  event.stopPropagation()
                                  setActiveTemplate(group.template)
                                }}
                              >
                                Configure
                              </Button>
                            ) : null}

                            {group.isConnected ? (
                              <HoverCard openDelay={100} closeDelay={100}>
                                <HoverCardTrigger asChild>
                                  <button
                                    type="button"
                                    className="flex h-6 w-6 items-center justify-center"
                                    aria-label={`View configured environments for ${group.name}`}
                                  >
                                    <SquareAsterisk className="icon-success size-3.5" />
                                  </button>
                                </HoverCardTrigger>
                                <HoverCardContent
                                  className="w-auto max-w-[240px] p-3"
                                  align="end"
                                  side="top"
                                  sideOffset={6}
                                >
                                  <div className="space-y-2 text-xs">
                                    <div className="font-medium text-foreground">
                                      Configured environments
                                    </div>
                                    <div className="flex flex-wrap gap-1">
                                      {group.environments.map((environment) => (
                                        <Badge
                                          key={environment}
                                          variant="secondary"
                                          className="text-[10px]"
                                        >
                                          {environment}
                                        </Badge>
                                      ))}
                                    </div>
                                  </div>
                                </HoverCardContent>
                              </HoverCard>
                            ) : null}
                          </ItemActions>
                        </div>

                        {isExpandable ? (
                          <CollapsibleContent>
                            <div className="divide-y divide-border/50">
                              {group.secrets.map((secret) => (
                                <Item
                                  key={secret.id}
                                  variant="default"
                                  size="sm"
                                  className="w-full flex-nowrap rounded-none border-none px-3 py-1.5 pl-12 text-left"
                                >
                                  <ItemMedia className="translate-y-0 self-center">
                                    <SecretIcon
                                      secretName={group.name}
                                      className="size-6 rounded"
                                    />
                                  </ItemMedia>
                                  <ItemContent className="min-w-0 gap-1">
                                    <ItemTitle className="flex w-full min-w-0 flex-wrap items-center gap-2 text-xs">
                                      <span className="truncate font-medium">
                                        {normalizeSecretEnvironment(
                                          secret.environment
                                        )}
                                      </span>
                                      {secret.is_corrupted ? (
                                        <Badge
                                          variant="secondary"
                                          className="text-[10px] text-amber-700"
                                        >
                                          Reconfigure required
                                        </Badge>
                                      ) : null}
                                    </ItemTitle>
                                    <div className="flex flex-wrap gap-1">
                                      {secret.keys.length > 0 ? (
                                        secret.keys.map((key) => (
                                          <Badge
                                            key={`${secret.id}-${key}`}
                                            variant="secondary"
                                            className="font-mono text-[10px]"
                                          >
                                            {key}
                                          </Badge>
                                        ))
                                      ) : (
                                        <span className="text-xs text-muted-foreground">
                                          No keys available
                                        </span>
                                      )}
                                    </div>
                                  </ItemContent>
                                  <ItemActions className="ml-auto flex shrink-0 items-center gap-1.5 pl-3">
                                    <EditCredentialsDialogTrigger asChild>
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        className="h-6 border-input bg-white px-2.5 text-[11px] text-foreground hover:bg-muted"
                                        onClick={(event) => {
                                          event.stopPropagation()
                                          setSelectedSecret(secret)
                                        }}
                                      >
                                        Edit
                                      </Button>
                                    </EditCredentialsDialogTrigger>
                                    <DeleteSecretAlertDialogTrigger asChild>
                                      <Button
                                        variant="outline"
                                        size="sm"
                                        className="h-6 border-input bg-white px-2.5 text-[11px] text-foreground hover:border-destructive hover:bg-destructive hover:text-destructive-foreground"
                                        onClick={(event) => {
                                          event.stopPropagation()
                                          setSelectedSecret(secret)
                                        }}
                                      >
                                        Delete
                                      </Button>
                                    </DeleteSecretAlertDialogTrigger>
                                  </ItemActions>
                                </Item>
                              ))}
                            </div>
                          </CollapsibleContent>
                        ) : null}
                      </div>
                    </Collapsible>
                  )
                })}
              </div>

              {filteredGroups.length === 0 ? (
                <div className="py-12 text-center">
                  <p className="text-sm text-muted-foreground">
                    No credentials found matching your criteria.
                  </p>
                </div>
              ) : null}
            </ScrollArea>
          </div>
        </EditCredentialsDialog>
      </DeleteSecretAlertDialog>

      <CreateCredentialDialog
        open={Boolean(activeTemplate)}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) {
            setActiveTemplate(null)
          }
        }}
        template={activeTemplate}
      />
    </>
  )
}

function CredentialGroupContent({
  name,
  label,
  environments,
  isConnected,
  secretTypeSummary,
}: {
  name: string
  label: string
  environments: string[]
  isConnected: boolean
  secretTypeSummary: string
}) {
  return (
    <Item className="w-full flex-nowrap rounded-none border-none px-0 py-0">
      <ItemMedia className="translate-y-0 self-center">
        <SecretIcon secretName={name} className="size-6 rounded" />
      </ItemMedia>
      <ItemContent className="min-w-0 gap-0">
        <ItemTitle className="flex w-full min-w-0 items-center gap-2 text-xs">
          <span className="min-w-0 truncate">{name}</span>
          <span className="text-xs text-muted-foreground">{label}</span>
        </ItemTitle>
        <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
          <span>{isConnected ? "Connected" : "Not connected"}</span>
          {environments.length > 0 ? (
            <span>{environments.length} environment(s)</span>
          ) : null}
          <span>{secretTypeSummary}</span>
        </div>
      </ItemContent>
    </Item>
  )
}
