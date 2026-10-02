"use client"

import type * as React from "react"
import type {
  SecretStoreCreate,
  SecretStoreProvider,
  SecretStoreRead,
  SecretStoreUpdate,
} from "@/client"
import { CopyButton } from "@/components/copy-button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Textarea } from "@/components/ui/textarea"
import {
  EXAMPLE_ROLE_ARN,
  validateAwsRegion,
  validateAwsRoleArn,
} from "@/lib/aws-secret-validation"

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
    ConfigFieldsProps & { store: SecretStoreRead }
  >
  validateSetup: (config: CreateConfigState) => CreateConfigErrors
  toSetupConfig: (config: CreateConfigState) => SecretStoreUpdate["config"]
  EditFields: React.ComponentType<ConfigFieldsProps>
  validateEdit: (
    config: CreateConfigState,
    store: SecretStoreRead
  ) => CreateConfigErrors
  toUpdateConfig: (config: CreateConfigState) => SecretStoreUpdate["config"]
  Details: React.ComponentType<{ store: SecretStoreRead }>
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

/** Best-effort AWS partition for a region; the role ARN wins once set. */
function regionPartition(region: string): string {
  if (region.startsWith("cn-")) return "aws-cn"
  if (region.startsWith("us-gov-")) return "aws-us-gov"
  return "aws"
}

/**
 * Minimal read-only permissions the store role needs. No ListSecrets,
 * write, delete, or rotation permissions are requested.
 */
export function buildStorePermissionPolicy(store: SecretStoreRead): string {
  const partition =
    store.config.role_arn?.split(":")[1] ?? regionPartition(store.config.region)
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
}: {
  id: string
  value: string
  onChange: (value: string) => void
  error?: string
}) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>Role ARN</Label>
      <Input
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={EXAMPLE_ROLE_ARN}
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
}: ConfigFieldsProps & { store: SecretStoreRead }) {
  return (
    <>
      <StorePolicies store={store} />
      <RoleArnField
        id="setup-role-arn"
        value={config.role_arn ?? ""}
        onChange={(role_arn) => onChange({ ...config, role_arn })}
        error={errors.role_arn}
      />
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

function StorePolicies({ store }: { store: SecretStoreRead }) {
  const trustPolicy = buildStoreTrustPolicy(store)
  const permissionPolicy = buildStorePermissionPolicy(store)

  return (
    <div className="grid gap-5 md:grid-cols-2">
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor={`trust-policy-${store.id}`} className="text-xs">
            Trust policy
          </Label>
          <CopyButton
            value={trustPolicy}
            toastMessage="Copied trust policy"
            tooltipMessage="Copy trust policy"
          />
        </div>
        <p
          id={`trust-policy-help-${store.id}`}
          className="text-xs text-muted-foreground md:min-h-8"
        >
          Attach to the role in AWS. Includes the external ID for this store.
        </p>
        <Textarea
          id={`trust-policy-${store.id}`}
          aria-describedby={`trust-policy-help-${store.id}`}
          readOnly
          className="h-48 resize-none bg-muted/30 font-mono text-xs"
          value={trustPolicy}
        />
      </div>
      <div className="space-y-2">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor={`permissions-policy-${store.id}`} className="text-xs">
            Permissions policy
          </Label>
          <CopyButton
            value={permissionPolicy}
            toastMessage="Copied permissions policy"
            tooltipMessage="Copy permissions policy"
          />
        </div>
        <p
          id={`permissions-policy-help-${store.id}`}
          className="text-xs text-muted-foreground md:min-h-8"
        >
          Read-only access. Limit the resource ARNs to the secrets you want to
          share.
        </p>
        <Textarea
          id={`permissions-policy-${store.id}`}
          aria-describedby={`permissions-policy-help-${store.id}`}
          readOnly
          className="h-48 resize-none bg-muted/30 font-mono text-xs"
          value={permissionPolicy}
        />
      </div>
    </div>
  )
}

function AwsSecretsManagerDetails({ store }: { store: SecretStoreRead }) {
  return (
    <>
      <dl className="space-y-4 border-b pb-5">
        <div className="space-y-1.5">
          <dt className="flex items-center gap-2 text-xs font-medium">
            Role ARN
            {store.config.role_arn && (
              <CopyButton
                value={store.config.role_arn}
                toastMessage="Copied role ARN"
                tooltipMessage="Copy role ARN"
              />
            )}
          </dt>
          <dd className="break-all font-mono text-xs text-muted-foreground">
            {store.config.role_arn ?? "Not set"}
          </dd>
        </div>
        <div className="space-y-1.5">
          <dt className="flex items-center gap-2 text-xs font-medium">
            External ID
            <CopyButton
              value={store.config.external_id}
              toastMessage="Copied external ID"
              tooltipMessage="Copy external ID"
            />
          </dt>
          <dd className="break-all font-mono text-xs text-muted-foreground">
            {store.config.external_id}
          </dd>
        </div>
      </dl>
      <StorePolicies store={store} />
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
    summary: (store) => store.config.region,
    createTitle: "Add AWS Secrets Manager store",
    createDescription:
      "Choose a name and the region that holds your secrets. Next, Tracecat generates the trust policy for the AWS role.",
    CreateFields: AwsSecretsManagerCreateFields,
    validateCreate: (config) =>
      compactErrors({ region: validateAwsRegion(config.region ?? "") }),
    toCreateConfig: (config) => ({
      provider: "aws_secrets_manager",
      region: (config.region ?? "").trim(),
    }),
    setupTitle: "Connect the AWS role",
    setupDescription:
      "Create an IAM role in AWS with these policies, then enter its ARN. Saving enables the store.",
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
