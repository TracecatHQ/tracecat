import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { SecretDefinition } from "@/client"
import { TooltipProvider } from "@/components/ui/tooltip"
import { CreateCredentialDialog } from "@/components/workspaces/create-credential-dialog"

const mockCreateSecret = jest.fn()

jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "ws",
}))

jest.mock("@/lib/hooks", () => ({
  useWorkspaceSecrets: () => ({ createSecret: mockCreateSecret }),
  useAwsAssumeRoleAccess: () => ({
    awsAssumeRoleAccess: undefined,
    awsAssumeRoleAccessError: null,
    awsAssumeRoleAccessIsLoading: false,
  }),
}))

jest.mock("@/hooks/use-entitlements", () => ({
  useEntitlements: () => ({ hasEntitlement: () => false }),
}))

jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: () => true,
}))

const template = {
  name: "snowflake",
  keys: ["SNOWFLAKE_OAUTH_CLIENT_ID", "SNOWFLAKE_OAUTH_CLIENT_SECRET"],
  optional_keys: ["SNOWFLAKE_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD"],
  actions: [],
  action_count: 0,
} as unknown as SecretDefinition

function renderDialog() {
  return render(
    <TooltipProvider>
      <CreateCredentialDialog
        template={template}
        open
        onOpenChange={jest.fn()}
      />
    </TooltipProvider>
  )
}

beforeEach(() => {
  mockCreateSecret.mockReset()
})

test("shows each full template key name as its value field label", async () => {
  renderDialog()

  for (const key of [
    "SNOWFLAKE_OAUTH_CLIENT_ID",
    "SNOWFLAKE_OAUTH_CLIENT_SECRET",
    "SNOWFLAKE_OAUTH_TOKEN_ENDPOINT_AUTH_METHOD",
  ]) {
    expect(await screen.findByText(key)).toBeVisible()
    expect(screen.getByLabelText(key)).toHaveAttribute("type", "password")
  }
  expect(screen.queryByPlaceholderText("Key")).not.toBeInTheDocument()
})

test("submits template keys with their values", async () => {
  const user = userEvent.setup()
  renderDialog()

  const [clientIdValue, clientSecretValue] =
    await screen.findAllByPlaceholderText("Value")
  await user.type(clientIdValue, "cid")
  await user.type(clientSecretValue, "csecret")
  await user.click(screen.getByRole("button", { name: /Create secret/ }))

  await waitFor(() => expect(mockCreateSecret).toHaveBeenCalledTimes(1))
  expect(mockCreateSecret.mock.calls[0][0].keys).toEqual([
    { key: "SNOWFLAKE_OAUTH_CLIENT_ID", value: "cid" },
    { key: "SNOWFLAKE_OAUTH_CLIENT_SECRET", value: "csecret" },
  ])
})
