import {
  validateAwsRegion,
  validateAwsRoleArn,
  validateAwsSecretId,
  validateSecretKey,
  validateSecretName,
} from "@/lib/aws-secret-validation"

describe("validateAwsRoleArn", () => {
  it.each([
    "arn:aws:iam::123456789012:role/tracecat-secrets-reader",
    "arn:aws-us-gov:iam::123456789012:role/path/to/reader",
    "  arn:aws-cn:iam::123456789012:role/reader  ",
  ])("accepts %s", (value) => {
    expect(validateAwsRoleArn(value)).toBeNull()
  })

  it.each([
    ["", /Enter the IAM role ARN/],
    [
      "arn:aws:iam::{Account}:role/{RoleNameWithPath}",
      /Replace the placeholders/,
    ],
    ["my-role", /Enter an IAM role ARN/],
    ["arn:aws:iam::123456789012:user/reader", /Enter an IAM role ARN/],
    [
      "arn:aws:secretsmanager:us-east-1:123456789012:secret:x",
      /Enter an IAM role ARN/,
    ],
    ["arn:aws:iam::my-account:role/reader", /12 digits/],
    ["arn:aws:iam::12345:role/reader", /12 digits/],
    ["arn:aws:iam::123456789012:role/bad name", /Check the role ARN format/],
  ])("explains %s", (value, message) => {
    expect(validateAwsRoleArn(value)).toMatch(message)
  })
})

describe("validateAwsRegion", () => {
  it("accepts region codes", () => {
    expect(validateAwsRegion("us-east-1")).toBeNull()
    expect(validateAwsRegion("us-gov-west-1")).toBeNull()
  })

  it.each(["US East (N. Virginia)", "us-east", "useast1"])(
    "rejects %s",
    (value) => {
      expect(validateAwsRegion(value)).toMatch(/region code such as us-east-1/)
    }
  )
})

describe("validateSecretName", () => {
  it("accepts snake case names", () => {
    expect(validateSecretName("hello_world")).toBeNull()
    expect(validateSecretName("secrets_api")).toBeNull()
  })

  it.each([
    ["SECRETS.hello_world.hello", /Enter only the name/],
    ["hello_world.hello", /Enter only the name/],
    ["SECRETS", /Enter only the name/],
    ["Hello", /lowercase/],
    ["1password", /Start with a letter or underscore/],
    ["hello-world", /no spaces or hyphens/],
    ["", /Enter a name/],
  ])("explains %s", (value, message) => {
    expect(validateSecretName(value)).toMatch(message)
  })
})

describe("validateAwsSecretId", () => {
  it.each([
    "prod/app/api-key",
    "arn:aws:secretsmanager:us-east-1:123456789012:secret:prod/app-AbCdEf",
  ])("accepts %s", (value) => {
    expect(validateAwsSecretId(value)).toBeNull()
  })

  it.each([
    ["", /Enter the secret name or ARN/],
    ["arn:aws:secretsmanager:us-east-1:secret:x", /full Secrets Manager/],
    ["prod app key", /no spaces/],
    [
      `arn:aws:secretsmanager:us-east-1:123456789012:secret:${"a".repeat(2048)}`,
      /at most 2048 characters/,
    ],
  ])("explains %s", (value, message) => {
    expect(validateAwsSecretId(value)).toMatch(message)
  })
})

describe("validateSecretKey", () => {
  it("accepts identifiers", () => {
    expect(validateSecretKey("API_TOKEN")).toBeNull()
  })

  it("explains invalid keys", () => {
    expect(validateSecretKey("")).toMatch(/Enter a key name/)
    expect(validateSecretKey("api-token")).toMatch(/starting with a letter/)
  })
})
