import type { SecretStoreRead } from "@/client"
import { buildStorePermissionPolicy } from "@/components/organization/secret-store-providers"

describe("AWS secret store permissions", () => {
  test.each([
    ["aws", "us-east-1"],
    ["aws-us-gov", "us-gov-west-1"],
    ["aws-cn", "cn-north-1"],
  ])("uses the configured %s partition", (partition, region) => {
    const store: SecretStoreRead = {
      id: "00000000-0000-0000-0000-000000000001",
      organization_id: "00000000-0000-0000-0000-000000000002",
      name: "test-store",
      provider: "aws_secrets_manager",
      enabled: true,
      all_workspaces: false,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
      config: {
        provider: "aws_secrets_manager",
        role_arn: `arn:${partition}:iam::123456789012:role/test-reader`,
        region,
        external_id: "synthetic-external-id",
      },
    }
    expect(JSON.parse(buildStorePermissionPolicy(store))).toEqual({
      Version: "2012-10-17",
      Statement: [
        {
          Effect: "Allow",
          Action: ["secretsmanager:GetSecretValue"],
          Resource: `arn:${partition}:secretsmanager:${region}:*:secret:*`,
        },
        {
          Effect: "Allow",
          Action: ["kms:Decrypt"],
          Resource: "*",
          Condition: {
            StringEquals: {
              "kms:ViaService": `secretsmanager.${region}.amazonaws.com`,
            },
          },
        },
      ],
    })
  })
})
