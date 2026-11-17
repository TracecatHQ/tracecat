import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useForm } from "react-hook-form"
import { PolymorphicField } from "@/components/builder/panel/action-panel-fields"
import { Form } from "@/components/ui/form"
import { useAgentPresets } from "@/hooks/use-agent-presets"

jest.mock("@/hooks/use-agent-presets", () => ({
  ...jest.requireActual("@/hooks/use-agent-presets"),
  useAgentPresets: jest.fn(),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-test",
}))

const presets = [
  {
    id: "preset-one",
    workspace_id: "workspace-test",
    name: "First preset",
    slug: "first-agent",
    description: null,
    model_provider: "openai",
    model_name: "model-one",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
  {
    id: "preset-two",
    workspace_id: "workspace-test",
    name: "Second preset",
    slug: "searchable-slug",
    description: null,
    model_provider: "openai",
    model_name: "model-two",
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
  },
]

beforeEach(() => {
  jest.mocked(useAgentPresets).mockReturnValue({
    presets,
    presetsIsLoading: false,
    presetsError: null,
    refetchPresets: jest.fn(),
  })
})

function TestField({ value = "" }: { value?: string }) {
  const form = useForm({ defaultValues: { inputs: { preset: value } } })
  return (
    <Form {...form}>
      <PolymorphicField
        label="Preset"
        fieldName="inputs.preset"
        fieldDefn={{
          type: "string",
          "x-tracecat-component": [{ component_id: "agent-preset" }],
        }}
      />
      <output data-testid="value">{form.watch("inputs.preset")}</output>
    </Form>
  )
}

it("searches presets by slug and writes the slug", async () => {
  const user = userEvent.setup()
  render(<TestField />)
  await user.click(screen.getByRole("combobox"))
  await user.type(
    screen.getByPlaceholderText("Search agent presets..."),
    "searchable-slug"
  )
  await user.click(screen.getByRole("option", { name: /Second preset/ }))
  expect(screen.getByTestId("value")).toHaveTextContent("searchable-slug")
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "Second presetsearchable-slug"
  )
})

it("shows an unknown saved slug with a not-found hint", () => {
  render(<TestField value="removed-preset" />)
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "removed-presetNot found"
  )
})

it("highlights the selected preset when the list is opened", async () => {
  const user = userEvent.setup()
  render(<TestField value="searchable-slug" />)
  await user.click(screen.getByRole("combobox"))
  expect(screen.getByRole("option", { name: /Second preset/ })).toHaveAttribute(
    "data-selected",
    "true"
  )
  expect(screen.getByRole("option", { name: /First preset/ })).toHaveAttribute(
    "data-selected",
    "false"
  )
})
