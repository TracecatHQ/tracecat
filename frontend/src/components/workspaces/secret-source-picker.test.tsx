import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { WorkspaceSecretStoreRead } from "@/client"
import { SecretSourcePicker } from "@/components/workspaces/secret-source-picker"

let mockStores: WorkspaceSecretStoreRead[] | undefined = []

jest.mock("@/hooks/use-secret-stores", () => ({
  useAuthorizedSecretStores: () => ({
    stores: mockStores,
    isLoading: mockStores === undefined,
    error: null,
  }),
}))

function store(
  name: string,
  region: string,
  enabled = true
): WorkspaceSecretStoreRead {
  return {
    id: `id-${name}`,
    name,
    provider: "aws_secrets_manager",
    region,
    enabled,
  }
}

beforeAll(() => {
  // cmdk scrolls the active item into view, which jsdom does not implement.
  Element.prototype.scrollIntoView = jest.fn()
})

beforeEach(() => {
  mockStores = []
})

test("lists each enabled store under its provider and selects one", async () => {
  mockStores = [
    store("production-secrets", "us-east-1"),
    store("eu-secrets", "eu-west-2"),
    store("legacy-secrets", "us-east-1", false),
  ]
  const onChange = jest.fn()
  const user = userEvent.setup()
  render(
    <SecretSourcePicker workspaceId="ws" value={null} onChange={onChange} />
  )

  await user.click(screen.getByRole("combobox"))
  expect(screen.getByText("AWS Secrets Manager")).toBeInTheDocument()
  expect(screen.getByRole("option", { name: /Tracecat/ })).toBeInTheDocument()
  expect(screen.queryByText("legacy-secrets")).not.toBeInTheDocument()
  expect(
    screen.queryByPlaceholderText("Search stores or regions")
  ).not.toBeInTheDocument()

  await user.click(screen.getByRole("option", { name: /eu-secrets/ }))
  expect(onChange).toHaveBeenCalledWith("id-eu-secrets")
})

test("shows the chosen store and its region on the trigger", () => {
  mockStores = [store("eu-secrets", "eu-west-2")]
  render(
    <SecretSourcePicker
      workspaceId="ws"
      value="id-eu-secrets"
      onChange={jest.fn()}
    />
  )

  expect(screen.getByRole("combobox")).toHaveTextContent(
    "eu-secrets · eu-west-2"
  )
})

test("searches by store name or region once the list is long", async () => {
  mockStores = Array.from({ length: 7 }, (_, n) =>
    store(`team-${n}`, n === 3 ? "ap-southeast-2" : "us-east-1")
  )
  const user = userEvent.setup()
  render(
    <SecretSourcePicker workspaceId="ws" value={null} onChange={jest.fn()} />
  )

  await user.click(screen.getByRole("combobox"))
  await user.type(
    screen.getByPlaceholderText("Search stores or regions"),
    "ap-"
  )

  expect(screen.getByRole("option", { name: /team-3/ })).toBeInTheDocument()
  expect(
    screen.queryByRole("option", { name: /team-1/ })
  ).not.toBeInTheDocument()
})

test("explains when no store is authorized", async () => {
  const user = userEvent.setup()
  render(
    <SecretSourcePicker workspaceId="ws" value={null} onChange={jest.fn()} />
  )

  await user.click(screen.getByRole("combobox"))
  expect(
    screen.getByText(/No stores are authorized for this workspace/)
  ).toBeInTheDocument()
})

test("resets to Tracecat when the selected store is no longer enabled", () => {
  mockStores = [store("eu-secrets", "eu-west-2", false)]
  const onChange = jest.fn()
  render(
    <SecretSourcePicker
      workspaceId="ws"
      value="id-eu-secrets"
      onChange={onChange}
    />
  )

  expect(onChange).toHaveBeenCalledWith(null)
})

test("keeps the selection while stores are loading", () => {
  mockStores = undefined
  const onChange = jest.fn()
  render(
    <SecretSourcePicker
      workspaceId="ws"
      value="id-eu-secrets"
      onChange={onChange}
    />
  )

  expect(onChange).not.toHaveBeenCalled()
})
