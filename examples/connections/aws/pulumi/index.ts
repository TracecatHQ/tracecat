import * as aws from "@pulumi/aws";
import * as pulumi from "@pulumi/pulumi";
import { readFileSync } from "node:fs";
import { join } from "node:path";

type Organization = {
  targetOuId: string;
  memberAccountIds: string[];
  managementAccountId?: string;
  previousEntryRoleArn?: string;
};

const config = new pulumi.Config();
const region = config.require("region");
const organization = config.getObject<Organization>("organization");
if (organization && (!organization.targetOuId || !organization.memberAccountIds?.length)) {
  throw new Error("Organization mode requires a root or OU ID and at least one member account.");
}

const entryProvider = new aws.Provider("entry", { profile: "aws-security", region });
const management = organization?.managementAccountId;
const targetAccounts = [...(organization?.memberAccountIds ?? []), ...(management ? [management] : [])];
const partition = aws.getPartitionOutput({}, { provider: entryProvider });
const targetRoleArns = partition.partition.apply((value) =>
  targetAccounts.map((id) => `arn:${value}:iam::${id}:role/TracecatSecOpsAuditRole`).join(","),
);

const entry = new aws.cloudformation.Stack("entry", {
  name: config.get("entryStackName") ?? "tracecat-secops-entry",
  templateBody: readFileSync(join(__dirname, "../cloudformation/entry-role.yaml"), "utf8"),
  capabilities: ["CAPABILITY_NAMED_IAM"],
  parameters: {
    EntryRoleName: config.get("entryRoleName") ?? "TracecatSecOpsAccessRole",
    TracecatPrincipalArn: config.require("tracecatPrincipalArn"),
    ExternalId: config.requireSecret("externalId"),
    TargetRoleArns: targetRoleArns,
    EnableGuardDutyTriage: String(config.getBoolean("enableGuardDutyTriage") ?? false),
  },
}, { provider: entryProvider });

export const roleArn = entry.outputs.apply((outputs) => outputs.RoleArn);
if (organization) {
  const provider = new aws.Provider("stacksets", { profile: "aws-stacksets", region });
  const auditTemplate = readFileSync(join(__dirname, "../cloudformation/audit-role.yaml"), "utf8");
  const entryRoleArns = roleArn.apply((arn) =>
    [arn, organization.previousEntryRoleArn].filter(Boolean).join(","),
  );
  const audit = new aws.cloudformation.StackSet("audit", {
    name: "tracecat-secops-audit",
    permissionModel: "SERVICE_MANAGED",
    callAs: "DELEGATED_ADMIN",
    capabilities: ["CAPABILITY_NAMED_IAM"],
    autoDeployment: { enabled: false },
    templateBody: auditTemplate,
    parameters: { EntryRoleArns: entryRoleArns },
  }, { provider });

  new aws.cloudformation.StackInstances("members", {
    stackSetName: audit.name,
    callAs: "DELEGATED_ADMIN",
    regions: [region],
    deploymentTargets: {
      organizationalUnitIds: [organization.targetOuId],
      accountFilterType: "INTERSECTION",
      accounts: organization.memberAccountIds,
    },
  }, { provider });

  if (management) {
    const managementProvider = new aws.Provider("management", {
      profile: "aws-management",
      region,
      allowedAccountIds: [management],
    });
    new aws.cloudformation.Stack("management", {
      name: "tracecat-secops-audit",
      templateBody: auditTemplate,
      capabilities: ["CAPABILITY_NAMED_IAM"],
      parameters: { EntryRoleArns: entryRoleArns },
    }, { provider: managementProvider });
  }
}
