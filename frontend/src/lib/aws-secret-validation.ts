/**
 * Client-side checks for AWS Secrets Manager store and reference forms.
 * Each validator returns a specific message, or null when the value is valid.
 */

// Keep these patterns in sync with tracecat/secrets/schemas.py.
const AWS_ROLE_ARN_REGEX =
  /^arn:aws(?:-[a-z]+)*:iam::\d{12}:role\/[\w+=,.@/-]+$/
const AWS_REGION_REGEX = /^[a-z]{2}(?:-[a-z]+)+-\d$/
const AWS_SECRET_ARN_REGEX =
  /^arn:aws(?:-[a-z]+)*:secretsmanager:[a-z0-9-]+:\d{12}:secret:\S+$/
const AWS_SECRET_NAME_REGEX = /^[A-Za-z0-9/_+=.@-]{1,512}$/
const SECRET_NAME_REGEX = /^[a-z_][a-z0-9_]*$/
const SECRET_KEY_REGEX = /^[A-Za-z_][A-Za-z0-9_]*$/

/** Synthetic example shown in role ARN hints and placeholders. */
export const EXAMPLE_ROLE_ARN =
  "arn:aws:iam::123456789012:role/tracecat-secrets-reader"
const EXAMPLE_SECRET_ARN =
  "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/app/api-key-AbCdEf"

/** Explain why a value is not an IAM role ARN the backend accepts. */
export function validateAwsRoleArn(value: string): string | null {
  const arn = value.trim()
  if (AWS_ROLE_ARN_REGEX.test(arn)) {
    return null
  }
  if (!arn) {
    return `Enter the IAM role ARN, e.g. ${EXAMPLE_ROLE_ARN}`
  }
  if (/[{}<>]/.test(arn)) {
    return `Replace the placeholders such as {Account} with real values, e.g. ${EXAMPLE_ROLE_ARN}`
  }
  const [prefix, , service, , account, resource] = arn.split(":")
  if (prefix !== "arn" || service !== "iam" || !resource?.startsWith("role/")) {
    return `Enter an IAM role ARN (not a user, policy, or secret ARN), e.g. ${EXAMPLE_ROLE_ARN}`
  }
  if (!/^\d{12}$/.test(account ?? "")) {
    return "The account ID must be exactly 12 digits, e.g. arn:aws:iam::123456789012:role/…"
  }
  return `Check the role ARN format, e.g. ${EXAMPLE_ROLE_ARN}`
}

/** Explain why a value is not an AWS region code. */
export function validateAwsRegion(value: string): string | null {
  const region = value.trim()
  if (AWS_REGION_REGEX.test(region)) {
    return null
  }
  return "Enter a region code such as us-east-1 or eu-west-2, not the region display name."
}

/** Explain why a value cannot be used as a `SECRETS.<name>` identifier. */
export function validateSecretName(value: string): string | null {
  if (SECRET_NAME_REGEX.test(value) && value.length <= 100) {
    return null
  }
  if (!value) {
    return "Enter a name, e.g. hello_world"
  }
  if (value.includes(".") || value.startsWith("SECRETS")) {
    return "Enter only the name, e.g. hello_world, not the full SECRETS.<name>.<key> reference."
  }
  if (value.length > 100) {
    return "Use 100 characters or fewer."
  }
  if (/[A-Z]/.test(value)) {
    return "Use lowercase letters only, e.g. hello_world"
  }
  if (/^\d/.test(value)) {
    return "Start with a letter or underscore, e.g. hello_world"
  }
  return "Use only lowercase letters, digits, and underscores (no spaces or hyphens), e.g. hello_world"
}

/** Explain why a value is not a Secrets Manager secret name or ARN. */
export function validateAwsSecretId(value: string): string | null {
  const secretId = value.trim()
  if (
    AWS_SECRET_ARN_REGEX.test(secretId) ||
    AWS_SECRET_NAME_REGEX.test(secretId)
  ) {
    return null
  }
  if (!secretId) {
    return "Enter the secret name or ARN, e.g. prod/app/api-key"
  }
  if (secretId.startsWith("arn:")) {
    return `Enter a full Secrets Manager secret ARN, e.g. ${EXAMPLE_SECRET_ARN}`
  }
  if (secretId.length > 512) {
    return "Secret names are at most 512 characters."
  }
  return "Secret names can only contain letters, digits, and / _ + = . @ - (no spaces), e.g. prod/app/api-key"
}

/** Explain why a value cannot be used as an output key. */
export function validateSecretKey(value: string): string | null {
  const key = value.trim()
  if (SECRET_KEY_REGEX.test(key)) {
    return null
  }
  if (!key) {
    return "Enter a key name, e.g. API_TOKEN"
  }
  return "Use letters, digits, and underscores, starting with a letter or underscore, e.g. API_TOKEN"
}
