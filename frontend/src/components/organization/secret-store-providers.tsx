"use client"

import { CopyIcon } from "lucide-react"
import React from "react"
import type {
  SecretStoreCreate,
  SecretStoreProvider,
  SecretStoreRead,
  SecretStoreUpdate,
} from "@/client"
import { CopyButton } from "@/components/copy-button"
import { AwsIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  EXAMPLE_ROLE_ARN,
  validateAwsRegion,
  validateAwsRoleArn,
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
  toSetupConfig: (config: CreateConfigState) => SecretStoreUpdate["config"]
  EditFields: React.ComponentType<ConfigFieldsProps>
  validateEdit: (
    config: CreateConfigState,
    store: SecretStoreRead
  ) => CreateConfigErrors
  toUpdateConfig: (config: CreateConfigState) => SecretStoreUpdate["config"]
  Details: React.ComponentType<{
    store: SecretStoreRead
    usage: React.ReactNode
  }>
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
            StringEquals: { "sts:ExternalId": store.config.external_id },
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
  const partition =
    store.config.role_arn?.split(":")[1] ?? store.aws_partition ?? "aws"
  return JSON.stringify(
    {
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Action: ["secretsmanager:GetSecretValue"],
          Resource: `arn:${partition}:secretsmanager:${store.config.region}:*:secret:*`,
        },
        {
          Effect: "Allow",
          Action: ["kms:Decrypt"],
          Resource: "*",
          Condition: {
            StringEquals: {
              // KMS ViaService uses amazonaws.com in every partition, including China.
              // https://docs.aws.amazon.com/kms/latest/developerguide/conditions-kms.html#conditions-kms-via-service
              "kms:ViaService": `secretsmanager.${store.config.region}.amazonaws.com`,
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
  return (
    <Tabs
      value={active.id}
      onValueChange={setActiveId}
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
          onClick={() =>
            copyToClipboard({
              value: active.policy,
              message: `Copied ${active.title.toLowerCase()}`,
            })
          }
        >
          <CopyIcon className="size-3.5" />
          Copy
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
          value={store.config.role_arn}
          copyLabel="role ARN"
        />
        <DetailItem
          label="External ID"
          value={store.config.external_id}
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
    summary: (store) => store.config.region,
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
    isSetupComplete: (store) => Boolean(store.config.role_arn),
    SetupFields: AwsSecretsManagerSetupFields,
    validateSetup: (config) =>
      compactErrors({ role_arn: validateAwsRoleArn(config.role_arn ?? "") }),
    toSetupConfig: (config) => ({ role_arn: (config.role_arn ?? "").trim() }),
    EditFields: AwsSecretsManagerEditFields,
    validateEdit: (config, store) =>
      compactErrors({
        // A store still in setup may keep an empty role ARN.
        role_arn:
          store.config.role_arn || config.role_arn?.trim()
            ? validateAwsRoleArn(config.role_arn ?? "")
            : null,
        region: validateAwsRegion(config.region ?? ""),
      }),
    toUpdateConfig: (config) => ({
      role_arn: config.role_arn?.trim() || undefined,
      region: (config.region ?? "").trim(),
    }),
    Details: AwsSecretsManagerDetails,
  },
}
