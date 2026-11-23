"use client"

import { CheckCheckIcon, CopyIcon } from "lucide-react"
import React from "react"
import type {
  AwsSecretsManagerStoreConfig,
  AzureKeyVaultStoreConfig,
  BitwardenSecretsManagerStoreConfig,
  SecretResolutionErrorCode,
  SecretSource,
  SecretStoreCreate,
  SecretStoreProvider,
  SecretStoreRead,
  SecretStoreUpdate,
  WorkspaceSecretStoreRead,
} from "@/client"
import { CopyButton } from "@/components/copy-button"
import { AwsIcon, AzureManagementIcon, BitwardenIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  EXAMPLE_ROLE_ARN,
  validateAwsRegion,
  validateAwsRoleArn,
  validateAwsSecretId,
} from "@/lib/aws-secret-validation"
import { cn, copyToClipboard } from "@/lib/utils"

/** Draft provider config held by the create dialog before submission. */
export type CreateConfigState = Record<string, string>

/** Inline validation messages keyed by config field. */
export type CreateConfigErrors = Partial<Record<string, string>>

type ConfigFieldsProps = {
  config: CreateConfigState
  onChange: (next: CreateConfigState) => void
  errors: CreateConfigErrors
}

/**
 * Provider-specific labels, fields, and detail rendering for a secret store.
 * Setup runs in two phases: create issues server-owned fields such as the
 * external ID, then setup collects what depends on them and enables the store.
 */
export type SecretStoreProviderEntry = {
  label: string
  /** How Tracecat authenticates, shown in the provider picker. */
  method: string
  /** Column heading for `summary`, e.g. the store region. */
  locationLabel: string
  Icon: React.ComponentType<{ className?: string }>
  summary: (store: SecretStoreRead) => string
  createTitle: string
  createDescription: string
  CreateFields: React.ComponentType<ConfigFieldsProps>
  validateCreate: (config: CreateConfigState) => CreateConfigErrors
  toCreateConfig: (config: CreateConfigState) => SecretStoreCreate["config"]
  setupTitle: string
  setupDescription: string
  isSetupComplete: (store: SecretStoreRead) => boolean
  SetupFields: React.ComponentType<
    ConfigFieldsProps & {
      store: SecretStoreRead
      // `undefined` while scopes load: render neither the field nor the notice.
      canEdit: boolean | undefined
    }
  >
  validateSetup: (config: CreateConfigState) => CreateConfigErrors
  /** The update that finishes setup; the caller also enables the store. */
  toSetupUpdate: (config: CreateConfigState) => SecretStoreUpdate
  editDescription: string
  /** Draft edit state for an existing store. */
  editState: (store: SecretStoreRead) => CreateConfigState
  EditFields: React.ComponentType<ConfigFieldsProps>
  validateEdit: (
    config: CreateConfigState,
    store: SecretStoreRead
  ) => CreateConfigErrors
  /** The update an edit sends, apart from the name. */
  toUpdate: (config: CreateConfigState) => SecretStoreUpdate
  Details: React.ComponentType<{
    store: SecretStoreRead
    usage: React.ReactNode
  }>
  /** How a workspace secret points at a secret in this store. */
  reference: SecretReferenceProviderEntry
}

/** Provider-specific parts of the workspace secret reference form and check. */
export type SecretReferenceProviderEntry = {
  /** Label of the remote reference input, e.g. "Secret name or ARN". */
  label: string
  description: string
  placeholder: string
  /** Explain why a remote reference is malformed, or null when it is valid. */
  validate: (value: string) => string | null
  /** Short store detail shown next to its name in pickers, e.g. the region. */
  storeHint: (store: WorkspaceSecretStoreRead) => string | null
  /** Fix hints that replace the generic hint for a failed check. */
  checkHints: Partial<Record<SecretResolutionErrorCode, string>>
}

/** The store's config, which must be an AWS Secrets Manager config. */
function awsConfig(store: SecretStoreRead): AwsSecretsManagerStoreConfig {
  if (store.provider !== "aws_secrets_manager") {
    throw new Error("Expected an AWS Secrets Manager store")
  }
  // The store's provider decides its config's shape.
  return store.config as AwsSecretsManagerStoreConfig
}

/** The store's config, which must be a Bitwarden Secrets Manager config. */
function bitwardenConfig(
  store: SecretStoreRead
): BitwardenSecretsManagerStoreConfig {
  if (store.provider !== "bitwarden_secrets_manager") {
    throw new Error("Expected a Bitwarden Secrets Manager store")
  }
  return store.config as BitwardenSecretsManagerStoreConfig
}

/** The store's config, which must be an Azure Key Vault config. */
function azureConfig(store: SecretStoreRead): AzureKeyVaultStoreConfig {
  if (store.provider !== "azure_key_vault") {
    throw new Error("Expected an Azure Key Vault store")
  }
  return store.config as AzureKeyVaultStoreConfig
}

/**
 * Builds the IAM trust policy an org admin attaches to the store role so the
 * Tracecat principal can assume it with the persisted external ID.
 */
function buildStoreTrustPolicy(store: SecretStoreRead): string {
  return JSON.stringify(
    {
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Principal: {
            AWS: store.tracecat_aws_principal_arn ?? "<tracecat-principal-arn>",
          },
          Action: "sts:AssumeRole",
          Condition: {
            StringEquals: { "sts:ExternalId": awsConfig(store).external_id },
          },
        },
      ],
    },
    null,
    2
  )
}

/**
 * Minimal read-only permissions the store role needs. No ListSecrets,
 * write, delete, or rotation permissions are requested.
 */
export function buildStorePermissionPolicy(store: SecretStoreRead): string {
  const config = awsConfig(store)
  const partition =
    config.role_arn?.split(":")[1] ?? store.aws_partition ?? "aws"
  return JSON.stringify(
    {
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Action: ["secretsmanager:GetSecretValue"],
          Resource: `arn:${partition}:secretsmanager:${config.region}:*:secret:*`,
        },
        {
          Effect: "Allow",
          Action: ["kms:Decrypt"],
          Resource: "*",
          Condition: {
            StringEquals: {
              // KMS ViaService uses amazonaws.com in every partition, including China.
              // https://docs.aws.amazon.com/kms/latest/developerguide/conditions-kms.html#conditions-kms-via-service
              "kms:ViaService": `secretsmanager.${config.region}.amazonaws.com`,
            },
          },
        },
      ],
    },
    null,
    2
  )
}

function RoleArnField({
  id,
  value,
  onChange,
  error,
  autoFocus,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  error?: string
  autoFocus?: boolean
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>Role ARN</Label>
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={EXAMPLE_ROLE_ARN}
        autoFocus={autoFocus}
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : undefined}
      />
      {error && (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

function RegionField({
  id,
  value,
  onChange,
  error,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  error?: string
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>Region</Label>
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="us-east-1"
        aria-invalid={Boolean(error)}
        aria-describedby={error ? `${id}-error` : undefined}
        required
      />
      {error && (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  )
}

function AwsSecretsManagerCreateFields({
  config,
  onChange,
  errors,
}: ConfigFieldsProps) {
  return (
    <RegionField
      id="store-region"
      value={config.region ?? ""}
      onChange={(region) => onChange({ ...config, region })}
      error={errors.region}
    />
  )
}

function AwsSecretsManagerSetupFields({
  store,
  config,
  onChange,
  errors,
  canEdit,
}: ConfigFieldsProps & {
  store: SecretStoreRead
  canEdit: boolean | undefined
}) {
  return (
    <>
      {canEdit === true && (
        <RoleArnField
          id="setup-role-arn"
          // Otherwise the dialog focuses the first copy button and opens its tooltip.
          autoFocus
          value={config.role_arn ?? ""}
          onChange={(role_arn) => onChange({ ...config, role_arn })}
          error={errors.role_arn}
        />
      )}
      {canEdit === false && (
        <p className="text-xs text-muted-foreground">
          You need permission to update secret stores to enter the role ARN.
          Share these policies with an organization admin to finish setup.
        </p>
      )}
      <StorePolicies store={store} />
    </>
  )
}

function AwsSecretsManagerEditFields({
  config,
  onChange,
  errors,
}: ConfigFieldsProps) {
  return (
    <>
      <RoleArnField
        id="store-role-arn"
        value={config.role_arn ?? ""}
        onChange={(role_arn) => onChange({ ...config, role_arn })}
        error={errors.role_arn}
      />
      <RegionField
        id="store-region"
        value={config.region ?? ""}
        onChange={(region) => onChange({ ...config, region })}
        error={errors.region}
      />
    </>
  )
}

function compactErrors(
  errors: Record<string, string | null>
): CreateConfigErrors {
  return Object.fromEntries(
    Object.entries(errors).filter((entry): entry is [string, string] =>
      Boolean(entry[1])
    )
  )
}

function PolicyBlock({
  description,
  policy,
  highlight,
}: {
  description: string
  policy: string
  highlight: (line: string) => boolean
}) {
  // Lines repeat (braces), so position is the only stable key.
  const lines = policy.split("\n").map((text, position) => ({ text, position }))
  return (
    <>
      <p className="border-b px-3 py-2 text-xs text-muted-foreground">
        {description}
      </p>
      <pre className="max-h-80 overflow-auto rounded-b-md bg-muted/40 py-2 font-mono text-xs leading-relaxed">
        {lines.map((line) => (
          <span
            key={line.position}
            className={cn(
              "block px-3",
              highlight(line.text)
                ? "bg-primary/10 text-foreground"
                : "text-muted-foreground"
            )}
          >
            {line.text}
          </span>
        ))}
      </pre>
    </>
  )
}

function StorePolicies({ store }: { store: SecretStoreRead }) {
  const [activeId, setActiveId] = React.useState("trust")
  const [copiedId, setCopiedId] = React.useState<string | null>(null)
  React.useEffect(() => {
    if (!copiedId) return
    const timer = setTimeout(() => setCopiedId(null), 2000)
    return () => clearTimeout(timer)
  }, [copiedId])
  const policies = [
    {
      id: "trust",
      title: "Trust policy",
      description:
        "Attach to the role. The highlighted line is this store's external ID.",
      policy: buildStoreTrustPolicy(store),
      highlight: (line: string) => line.includes("sts:ExternalId"),
    },
    {
      id: "permissions",
      title: "Permissions policy",
      description:
        "Read-only. Narrow the highlighted Resource to the secrets you share.",
      policy: buildStorePermissionPolicy(store),
      highlight: (line: string) => line.includes('"Resource": "arn:'),
    },
  ]
  const active = policies.find((item) => item.id === activeId) ?? policies[0]
  const copied = copiedId === active.id
  return (
    <Tabs
      value={active.id}
      onValueChange={(id) => {
        setActiveId(id)
        setCopiedId(null)
      }}
      className="min-w-0 rounded-md border"
    >
      <div className="flex items-center justify-between gap-2 border-b pr-2">
        <TabsList className="h-9 justify-start rounded-none bg-transparent p-0">
          {policies.map((item) => (
            <TabsTrigger
              key={item.id}
              value={item.id}
              className="h-full rounded-none text-xs"
            >
              {item.title}
            </TabsTrigger>
          ))}
        </TabsList>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          className="h-7 gap-1.5 px-2 text-xs text-muted-foreground"
          aria-label={`Copy ${active.title.toLowerCase()}`}
          onClick={async () => {
            const policyId = active.id
            const landed = await copyToClipboard({
              value: active.policy,
              message: `Copied ${active.title.toLowerCase()}`,
            })
            if (landed) setCopiedId(policyId)
          }}
        >
          {copied ? (
            <CheckCheckIcon className="size-3.5" />
          ) : (
            <CopyIcon className="size-3.5" />
          )}
          {copied ? "Copied" : "Copy"}
        </Button>
      </div>
      {policies.map((item) => (
        <TabsContent key={item.id} value={item.id} className="mt-0">
          <PolicyBlock {...item} />
        </TabsContent>
      ))}
    </Tabs>
  )
}

function DetailItem({
  label,
  value,
  copyLabel,
}: {
  label: string
  value: string | null | undefined
  copyLabel: string
}) {
  return (
    <div className="min-w-0 space-y-1">
      <dt className="text-xs font-medium">{label}</dt>
      <dd className="flex items-center gap-1.5">
        <code className="break-all font-mono text-xs text-muted-foreground">
          {value ?? "Not set"}
        </code>
        {value && (
          <CopyButton
            value={value}
            toastMessage={`Copied ${copyLabel}`}
            tooltipMessage={`Copy ${copyLabel}`}
          />
        )}
      </dd>
    </div>
  )
}

function AwsSecretsManagerDetails({
  store,
  usage,
}: {
  store: SecretStoreRead
  usage: React.ReactNode
}) {
  return (
    <>
      <StorePolicies store={store} />
      <dl className="grid gap-x-8 gap-y-4 border-t pt-4 sm:grid-cols-3">
        <DetailItem
          label="Role ARN"
          value={awsConfig(store).role_arn}
          copyLabel="role ARN"
        />
        <DetailItem
          label="External ID"
          value={awsConfig(store).external_id}
          copyLabel="external ID"
        />
        <div className="min-w-0 space-y-1">
          <dt className="text-xs font-medium">Used by</dt>
          <dd>{usage}</dd>
        </div>
      </dl>
    </>
  )
}

const BITWARDEN_CLOUD_SERVERS: Record<string, string> = {
  us: "US cloud (bitwarden.com)",
  eu: "EU cloud (bitwarden.eu)",
}
const SELF_HOSTED = "self_hosted"
// Bitwarden shows and returns secret IDs in lowercase, and secrets are matched
// by it. Organization IDs are normalized by the backend, so any case is fine.
const SECRET_ID_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
const UUID_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i
const EXAMPLE_UUID = "3f2a1b4c-5d6e-4f70-8a9b-0c1d2e3f4a5b"

/** Explain why a value is not a Bitwarden secret ID. */
export function validateBitwardenSecretId(value: string): string | null {
  return SECRET_ID_REGEX.test(value.trim())
    ? null
    : `Enter the secret ID in lowercase, e.g. ${EXAMPLE_UUID}`
}

/** Explain why a value is not a Bitwarden organization ID. */
export function validateBitwardenOrganizationId(value: string): string | null {
  return UUID_REGEX.test(value.trim())
    ? null
    : `Enter the organization ID, e.g. ${EXAMPLE_UUID}`
}

/** Explain why a self-hosted server address is not one the backend accepts. */
export function validateBitwardenServer(
  config: CreateConfigState
): string | null {
  const choice = config.server_choice ?? "us"
  if (choice !== SELF_HOSTED) return null
  const raw = (config.server_url ?? "").trim()
  const example = "e.g. https://vault.example.com"
  if (/[?#@\\\s]/.test(raw)) {
    return `Enter only the server's base address, without credentials, a query or a fragment, ${example}`
  }
  try {
    const url = new URL(raw)
    return url.protocol === "https:" && url.hostname
      ? null
      : `Enter the https:// address of your Bitwarden server, ${example}`
  } catch {
    return `Enter the https:// address of your Bitwarden server, ${example}`
  }
}

function bitwardenServer(config: CreateConfigState): string {
  const choice = config.server_choice ?? "us"
  return choice === SELF_HOSTED
    ? (config.server_url ?? "").trim().replace(/\/+$/, "")
    : choice
}

function describeBitwardenServer(server: string): string {
  return BITWARDEN_CLOUD_SERVERS[server] ?? server
}

function BitwardenServerFields({
  config,
  onChange,
  errors,
}: ConfigFieldsProps) {
  const choice = config.server_choice ?? "us"
  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="bitwarden-server">Server</Label>
        <Select
          value={choice}
          onValueChange={(server_choice) =>
            onChange({ ...config, server_choice })
          }
        >
          <SelectTrigger id="bitwarden-server">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {Object.entries(BITWARDEN_CLOUD_SERVERS).map(([value, label]) => (
              <SelectItem key={value} value={value}>
                {label}
              </SelectItem>
            ))}
            <SelectItem value={SELF_HOSTED}>Self-hosted</SelectItem>
          </SelectContent>
        </Select>
      </div>
      {choice === SELF_HOSTED && (
        <div className="space-y-2">
          <Label htmlFor="bitwarden-server-url">Server address</Label>
          <Input
            id="bitwarden-server-url"
            value={config.server_url ?? ""}
            onChange={(e) =>
              onChange({ ...config, server_url: e.target.value })
            }
            placeholder="https://vault.example.com"
            aria-invalid={Boolean(errors.server_url)}
            aria-describedby={
              errors.server_url ? "bitwarden-server-url-error" : undefined
            }
          />
          {errors.server_url && (
            <p
              id="bitwarden-server-url-error"
              className="text-xs text-destructive"
            >
              {errors.server_url}
            </p>
          )}
        </div>
      )}
      <div className="space-y-2">
        <Label htmlFor="bitwarden-organization-id">Organization ID</Label>
        <Input
          id="bitwarden-organization-id"
          value={config.organization_id ?? ""}
          onChange={(e) =>
            onChange({ ...config, organization_id: e.target.value })
          }
          placeholder={EXAMPLE_UUID}
          aria-invalid={Boolean(errors.organization_id)}
          aria-describedby={
            errors.organization_id
              ? "bitwarden-organization-id-error"
              : "bitwarden-organization-id-hint"
          }
        />
        <p
          id="bitwarden-organization-id-hint"
          className="text-xs text-muted-foreground"
        >
          Shown in the Secrets Manager URL and under organization settings in
          Bitwarden.
        </p>
        {errors.organization_id && (
          <p
            id="bitwarden-organization-id-error"
            className="text-xs text-destructive"
          >
            {errors.organization_id}
          </p>
        )}
      </div>
    </>
  )
}

/** A store credential held in the write-only access token column. */
type AccessTokenCredential = {
  id: string
  label: string
  /** Lowercase name, e.g. "client secret". */
  name: string
}

const BITWARDEN_ACCESS_TOKEN: AccessTokenCredential = {
  id: "bitwarden-access-token",
  label: "Access token",
  name: "access token",
}

const AZURE_CLIENT_SECRET: AccessTokenCredential = {
  id: "azure-client-secret",
  label: "Client secret",
  name: "client secret",
}

function AccessTokenField({
  config,
  onChange,
  errors,
  credential: { id, label, name },
}: ConfigFieldsProps & { credential: AccessTokenCredential }) {
  // Set by editState: a token is stored, and an empty field keeps it.
  const replacing = config.access_token_set === "true"
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{replacing ? `Replace ${name}` : label}</Label>
      <Input
        id={id}
        type="password"
        autoComplete="off"
        value={config.access_token ?? ""}
        onChange={(e) => onChange({ ...config, access_token: e.target.value })}
        placeholder={replacing ? `Leave empty to keep the current ${name}` : ""}
        aria-invalid={Boolean(errors.access_token)}
        aria-describedby={errors.access_token ? `${id}-error` : `${id}-hint`}
      />
      <p id={`${id}-hint`} className="text-xs text-muted-foreground">
        Stored encrypted in Tracecat and never shown again. It is only read when
        a workflow resolves a secret.
      </p>
      {errors.access_token && (
        <p id={`${id}-error`} className="text-xs text-destructive">
          {errors.access_token}
        </p>
      )}
    </div>
  )
}

function AccessTokenSetupFields({
  config,
  onChange,
  errors,
  canEdit,
  credential,
  steps,
}: ConfigFieldsProps & {
  canEdit: boolean | undefined
  credential: AccessTokenCredential
  steps: React.ReactNode
}) {
  return (
    <>
      <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
        {steps}
      </ol>
      {canEdit === true && (
        <AccessTokenField
          config={config}
          onChange={onChange}
          errors={errors}
          credential={credential}
        />
      )}
      {canEdit === false && (
        <p className="text-xs text-muted-foreground">
          You need the <code>org:secret:update</code> permission to set the{" "}
          {credential.name}. Share these steps with an organization admin to
          finish setup.
        </p>
      )}
    </>
  )
}

function AccessTokenDetail({
  store,
  credential,
}: {
  store: SecretStoreRead
  credential: AccessTokenCredential
}) {
  return (
    <div className="min-w-0 space-y-1">
      <dt className="text-xs font-medium">{credential.label}</dt>
      <dd className="text-xs text-muted-foreground">
        {store.access_token_set ? "Set" : "Not set"}
      </dd>
    </div>
  )
}

/** Entry fields shared by providers whose setup step sets the access token. */
function accessTokenEntryFields(missing: string) {
  return {
    isSetupComplete: (store: SecretStoreRead) =>
      Boolean(store.access_token_set),
    validateSetup: (config: CreateConfigState) =>
      compactErrors({
        access_token: config.access_token?.trim() ? null : missing,
      }),
    toSetupUpdate: (config: CreateConfigState): SecretStoreUpdate => ({
      access_token: (config.access_token ?? "").trim(),
    }),
  }
}

/** Edit state for the token: the field starts empty, and empty keeps it. */
function accessTokenEditState(store: SecretStoreRead): CreateConfigState {
  return {
    access_token: "",
    access_token_set: store.access_token_set ? "true" : "",
  }
}

function accessTokenUpdate(
  config: CreateConfigState
): Pick<SecretStoreUpdate, "access_token"> {
  const token = config.access_token?.trim()
  return token ? { access_token: token } : {}
}

function BitwardenSecretsManagerSetupFields(
  props: ConfigFieldsProps & { canEdit: boolean | undefined }
) {
  return (
    <AccessTokenSetupFields
      {...props}
      credential={BITWARDEN_ACCESS_TOKEN}
      steps={
        <>
          <li>
            In Bitwarden Secrets Manager, create a machine account and give it
            read access to the projects whose secrets Tracecat should use.
          </li>
          <li>Create an access token for the machine account.</li>
          <li>Paste the token below.</li>
        </>
      }
    />
  )
}

function BitwardenSecretsManagerEditFields(props: ConfigFieldsProps) {
  return (
    <>
      <BitwardenServerFields {...props} />
      <AccessTokenField {...props} credential={BITWARDEN_ACCESS_TOKEN} />
    </>
  )
}

function BitwardenSecretsManagerDetails({
  store,
  usage,
}: {
  store: SecretStoreRead
  usage: React.ReactNode
}) {
  const config = bitwardenConfig(store)
  return (
    <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-3">
      <div className="min-w-0 space-y-1">
        <dt className="text-xs font-medium">Server</dt>
        <dd className="break-all text-xs text-muted-foreground">
          {describeBitwardenServer(config.server)}
        </dd>
      </div>
      <DetailItem
        label="Organization ID"
        value={config.organization_id}
        copyLabel="organization ID"
      />
      <AccessTokenDetail store={store} credential={BITWARDEN_ACCESS_TOKEN} />
      <div className="min-w-0 space-y-1">
        <dt className="text-xs font-medium">Used by</dt>
        <dd>{usage}</dd>
      </div>
    </dl>
  )
}

function bitwardenServerErrors(config: CreateConfigState): CreateConfigErrors {
  return compactErrors({
    server_url: validateBitwardenServer(config),
    organization_id: validateBitwardenOrganizationId(
      config.organization_id ?? ""
    ),
  })
}

// Mirrors AZURE_KEY_VAULT_AUTHORITY_BY_SUFFIX in tracecat/secrets/schemas.py.
const AZURE_VAULT_URL_REGEX =
  /^https:\/\/(?=[a-z0-9-]{3,24}\.)[a-z](?:-?[a-z0-9])*\.(?:vault\.azure\.net|vault\.usgovcloudapi\.net|vault\.azure\.cn)(?::443)?\/?$/i
const AZURE_SECRET_NAME_REGEX = /^[0-9A-Za-z-]{1,127}$/
const EXAMPLE_VAULT_URL = "https://my-vault.vault.azure.net"

/** Explain why a value is not a Key Vault URI. */
export function validateAzureVaultUrl(value: string): string | null {
  return AZURE_VAULT_URL_REGEX.test(value.trim())
    ? null
    : `Enter the vault URI from the vault's overview page, e.g. ${EXAMPLE_VAULT_URL}`
}

/** Explain why a value is not a Key Vault secret name. */
export function validateAzureSecretName(value: string): string | null {
  return AZURE_SECRET_NAME_REGEX.test(value.trim())
    ? null
    : "Enter the secret's name: 1 to 127 letters, digits and hyphens."
}

function validateAzureId(value: string, what: string): string | null {
  return UUID_REGEX.test(value.trim())
    ? null
    : `Enter the ${what}, e.g. ${EXAMPLE_UUID}`
}

function azureConfigErrors(config: CreateConfigState): CreateConfigErrors {
  return compactErrors({
    vault_url: validateAzureVaultUrl(config.vault_url ?? ""),
    tenant_id: validateAzureId(config.tenant_id ?? "", "directory (tenant) ID"),
    client_id: validateAzureId(
      config.client_id ?? "",
      "application (client) ID"
    ),
  })
}

function azureConfigFromState(config: CreateConfigState) {
  return {
    provider: "azure_key_vault" as const,
    vault_url: (config.vault_url ?? "").trim(),
    tenant_id: (config.tenant_id ?? "").trim(),
    client_id: (config.client_id ?? "").trim(),
  }
}

const AZURE_FIELDS = [
  {
    key: "vault_url",
    label: "Vault URI",
    placeholder: EXAMPLE_VAULT_URL,
    hint: "Shown on the vault's overview page in the Azure portal.",
  },
  {
    key: "tenant_id",
    label: "Directory (tenant) ID",
    placeholder: EXAMPLE_UUID,
    hint: "Shown on the app registration's overview page.",
  },
  {
    key: "client_id",
    label: "Application (client) ID",
    placeholder: EXAMPLE_UUID,
    hint: "The app registration Tracecat signs in as.",
  },
] as const

function AzureKeyVaultFields({ config, onChange, errors }: ConfigFieldsProps) {
  return (
    <>
      {AZURE_FIELDS.map(({ key, label, placeholder, hint }) => {
        const id = `azure-${key.replaceAll("_", "-")}`
        return (
          <div key={key} className="space-y-2">
            <Label htmlFor={id}>{label}</Label>
            <Input
              id={id}
              value={config[key] ?? ""}
              onChange={(e) => onChange({ ...config, [key]: e.target.value })}
              placeholder={placeholder}
              aria-invalid={Boolean(errors[key])}
              aria-describedby={errors[key] ? `${id}-error` : `${id}-hint`}
            />
            <p id={`${id}-hint`} className="text-xs text-muted-foreground">
              {hint}
            </p>
            {errors[key] && (
              <p id={`${id}-error`} className="text-xs text-destructive">
                {errors[key]}
              </p>
            )}
          </div>
        )
      })}
    </>
  )
}

function AzureKeyVaultSetupFields(
  props: ConfigFieldsProps & { canEdit: boolean | undefined }
) {
  return (
    <AccessTokenSetupFields
      {...props}
      credential={AZURE_CLIENT_SECRET}
      steps={
        <>
          <li>
            In Microsoft Entra ID, open the app registration and create a client
            secret under Certificates &amp; secrets.
          </li>
          <li>
            On the vault, assign the app the Key Vault Secrets User role, or an
            access policy with Get on secrets.
          </li>
          <li>Paste the client secret's value below.</li>
        </>
      }
    />
  )
}

function AzureKeyVaultEditFields(props: ConfigFieldsProps) {
  return (
    <>
      <AzureKeyVaultFields {...props} />
      <AccessTokenField {...props} credential={AZURE_CLIENT_SECRET} />
    </>
  )
}

function AzureKeyVaultDetails({
  store,
  usage,
}: {
  store: SecretStoreRead
  usage: React.ReactNode
}) {
  const config = azureConfig(store)
  return (
    <dl className="grid gap-x-8 gap-y-4 sm:grid-cols-3">
      <DetailItem
        label="Vault URI"
        value={config.vault_url}
        copyLabel="vault URI"
      />
      <DetailItem
        label="Directory (tenant) ID"
        value={config.tenant_id}
        copyLabel="tenant ID"
      />
      <DetailItem
        label="Application (client) ID"
        value={config.client_id}
        copyLabel="client ID"
      />
      <AccessTokenDetail store={store} credential={AZURE_CLIENT_SECRET} />
      <div className="min-w-0 space-y-1">
        <dt className="text-xs font-medium">Used by</dt>
        <dd>{usage}</dd>
      </div>
    </dl>
  )
}

/** Provider-specific rendering and payload building, keyed by provider. */
export const SECRET_STORE_PROVIDERS: Record<
  SecretStoreProvider,
  SecretStoreProviderEntry
> = {
  aws_secrets_manager: {
    label: "AWS Secrets Manager",
    method: "IAM role with an external ID",
    locationLabel: "Region",
    Icon: ({ className }) => <AwsIcon className={className} />,
    summary: (store) => awsConfig(store).region,
    createTitle: "Add AWS Secrets Manager store",
    createDescription:
      "Enter a name and the region that holds your secrets. Saving generates the external ID and trust policy.",
    CreateFields: AwsSecretsManagerCreateFields,
    validateCreate: (config) =>
      compactErrors({ region: validateAwsRegion(config.region ?? "") }),
    toCreateConfig: (config) => ({
      provider: "aws_secrets_manager",
      region: (config.region ?? "").trim(),
    }),
    setupTitle: "Connect the AWS role",
    setupDescription:
      "Create an IAM role in AWS with these policies, then enter its ARN.",
    isSetupComplete: (store) => Boolean(awsConfig(store).role_arn),
    SetupFields: AwsSecretsManagerSetupFields,
    validateSetup: (config) =>
      compactErrors({ role_arn: validateAwsRoleArn(config.role_arn ?? "") }),
    toSetupUpdate: (config) => ({
      config: {
        provider: "aws_secrets_manager",
        role_arn: (config.role_arn ?? "").trim(),
      },
    }),
    editDescription:
      "Update the store connection. The external ID stays the same, so the trust policy keeps working.",
    editState: (store) => ({
      role_arn: awsConfig(store).role_arn ?? "",
      region: awsConfig(store).region,
    }),
    EditFields: AwsSecretsManagerEditFields,
    validateEdit: (config, store) =>
      compactErrors({
        // A store still in setup may keep an empty role ARN.
        role_arn:
          awsConfig(store).role_arn || config.role_arn?.trim()
            ? validateAwsRoleArn(config.role_arn ?? "")
            : null,
        region: validateAwsRegion(config.region ?? ""),
      }),
    toUpdate: (config) => ({
      config: {
        provider: "aws_secrets_manager",
        role_arn: config.role_arn?.trim() || undefined,
        region: (config.region ?? "").trim(),
      },
    }),
    Details: AwsSecretsManagerDetails,
    reference: {
      label: "Secret name or ARN",
      description:
        "The secret name as shown in the AWS console (looked up in the store's region) or its full ARN.",
      placeholder: "prod/app/api-key",
      validate: validateAwsSecretId,
      storeHint: (store) => store.region ?? null,
      checkHints: {
        assume_role_failed:
          "Tracecat could not assume the store's IAM role. Check that the role's trust policy allows the Tracecat principal and includes the store's external ID.",
        access_denied:
          "The store's IAM role cannot read this secret. Allow secretsmanager:GetSecretValue on the secret, and kms:Decrypt if it uses a customer managed key.",
        not_found:
          "No secret with this name or ARN exists in the store's region. Check the spelling and region.",
        decryption_failed:
          "AWS could not decrypt the secret. Allow kms:Decrypt on the secret's KMS key for the store's IAM role.",
        throttled: "AWS throttled the request. Try again in a moment.",
        timeout: "AWS did not respond in time. Try again in a moment.",
        binary_value:
          "The secret stores a binary value. Only text (SecretString) values are supported.",
        invalid_mapping:
          "AWS rejected the request. The secret may be scheduled for deletion, or the name or ARN is invalid.",
        region_mismatch:
          "The secret ARN is in a different region than the store. Use a secret from the store's region.",
      },
    },
  },
  bitwarden_secrets_manager: {
    label: "Bitwarden Secrets Manager",
    method: "Machine account access token",
    locationLabel: "Server",
    Icon: ({ className }) => <BitwardenIcon className={className} />,
    summary: (store) => describeBitwardenServer(bitwardenConfig(store).server),
    createTitle: "Add Bitwarden Secrets Manager store",
    createDescription:
      "Enter a name, your Bitwarden server and the organization that holds your secrets. You add the access token next.",
    CreateFields: BitwardenServerFields,
    validateCreate: bitwardenServerErrors,
    toCreateConfig: (config) => ({
      provider: "bitwarden_secrets_manager",
      server: bitwardenServer(config),
      organization_id: (config.organization_id ?? "").trim(),
    }),
    setupTitle: "Add the access token",
    setupDescription:
      "Tracecat reads secrets with a Bitwarden machine account. Paste its access token here.",
    ...accessTokenEntryFields("Paste the machine account's access token."),
    SetupFields: BitwardenSecretsManagerSetupFields,
    editDescription:
      "Update the store connection. The server and organization cannot change while secrets reference this store.",
    editState: (store) => {
      const { server, organization_id } = bitwardenConfig(store)
      const cloud = server in BITWARDEN_CLOUD_SERVERS
      return {
        server_choice: cloud ? server : SELF_HOSTED,
        server_url: cloud ? "" : server,
        organization_id,
        ...accessTokenEditState(store),
      }
    },
    EditFields: BitwardenSecretsManagerEditFields,
    validateEdit: (config) => bitwardenServerErrors(config),
    toUpdate: (config) => ({
      config: {
        provider: "bitwarden_secrets_manager",
        server: bitwardenServer(config),
        organization_id: (config.organization_id ?? "").trim(),
      },
      ...accessTokenUpdate(config),
    }),
    Details: BitwardenSecretsManagerDetails,
    reference: {
      label: "Secret ID",
      description:
        "The secret's ID, shown in Bitwarden Secrets Manager next to the secret.",
      placeholder: EXAMPLE_UUID,
      validate: validateBitwardenSecretId,
      storeHint: () => null,
      checkHints: {
        auth_failed:
          "Bitwarden rejected the access token. Check that the machine account's token is current, and replace it in the store if not.",
        not_found:
          "Bitwarden found no secret with this ID that the machine account can read. Check the ID, and that the machine account can read the secret's project.",
        access_denied:
          "Bitwarden denied the read, or the secret belongs to another organization than the store. Check that the machine account can read the secret's project, and that the secret is in the store's organization.",
        invalid_reference:
          "Bitwarden rejected the secret ID. Copy the ID again from Bitwarden Secrets Manager.",
        unavailable:
          "Bitwarden could not be reached, or a self-hosted server no longer resolves to a public address. Try again in a moment.",
        throttled: "Bitwarden rate-limited the request. Try again in a moment.",
        timeout: "Bitwarden did not respond in time. Try again in a moment.",
        credential_missing:
          "The store has no usable access token. Ask an organization admin to add or replace it.",
        unknown:
          "Bitwarden returned an error Tracecat does not recognize. Try again; if it persists, check the Bitwarden server's status.",
      },
    },
  },
  azure_key_vault: {
    label: "Azure Key Vault",
    method: "Entra ID app with a client secret",
    locationLabel: "Vault",
    Icon: ({ className }) => <AzureManagementIcon className={className} />,
    summary: (store) => azureConfig(store).vault_url.replace(/^https:\/\//, ""),
    createTitle: "Add Azure Key Vault store",
    createDescription:
      "Enter a name, the vault, and the Entra ID app registration Tracecat signs in as. You add the client secret next.",
    CreateFields: AzureKeyVaultFields,
    validateCreate: azureConfigErrors,
    toCreateConfig: azureConfigFromState,
    setupTitle: "Add the client secret",
    setupDescription:
      "Tracecat reads secrets as an Entra ID app registration. Paste one of its client secrets here.",
    ...accessTokenEntryFields("Paste the app registration's client secret."),
    SetupFields: AzureKeyVaultSetupFields,
    editDescription:
      "Update the store connection. The vault cannot change while secrets reference this store.",
    editState: (store) => {
      const { vault_url, tenant_id, client_id } = azureConfig(store)
      return { vault_url, tenant_id, client_id, ...accessTokenEditState(store) }
    },
    EditFields: AzureKeyVaultEditFields,
    validateEdit: azureConfigErrors,
    toUpdate: (config) => ({
      config: azureConfigFromState(config),
      ...accessTokenUpdate(config),
    }),
    Details: AzureKeyVaultDetails,
    reference: {
      label: "Secret name",
      description:
        "The secret's name in the vault. Tracecat always reads its current version.",
      placeholder: "api-key",
      validate: validateAzureSecretName,
      storeHint: () => null,
      checkHints: {
        auth_failed:
          "Microsoft Entra ID rejected the app's sign-in. Check the tenant ID, client ID and that the client secret has not expired; replace it in the store if needed.",
        not_found:
          "The vault has no secret with this name. Check the spelling.",
        access_denied:
          "The vault denied the read. Give the app the Key Vault Secrets User role (or a Get access policy), check the vault's firewall allows Tracecat, and that the secret is enabled.",
        unavailable:
          "Azure Key Vault could not be reached. Check the vault URI and try again in a moment.",
        throttled: "Azure throttled the request. Try again in a moment.",
        timeout: "Azure did not respond in time. Try again in a moment.",
        credential_missing:
          "The store has no usable client secret. Ask an organization admin to add or replace it.",
        unknown:
          "Azure returned an error Tracecat does not recognize. Try again; if it persists, check the vault in the Azure portal.",
      },
    },
  },
}

/** The provider entry for a secret's `source`, or null for local secrets. */
export function providerForSource(
  source: SecretSource | null | undefined
): SecretStoreProviderEntry | null {
  if (!source || source === "local") return null
  return SECRET_STORE_PROVIDERS[source as SecretStoreProvider] ?? null
}
