import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useForm } from "react-hook-form"
import { ApiError } from "@/client"
import { PolymorphicField } from "@/components/builder/panel/action-panel-fields"
import { Form } from "@/components/ui/form"
import { useWorkspaceAgentModels } from "@/lib/hooks"

jest.mock("@/lib/hooks", () => ({
  ...jest.requireActual("@/lib/hooks"),
  useWorkspaceAgentModels: jest.fn(),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-test",
}))

const models = [
  {
    id: "catalog-one",
    custom_provider_id: null,
    organization_id: null,
    model_provider: "openai",
    model_name: "model-one",
    model_metadata: {},
  },
  {
    id: "catalog-two",
    custom_provider_id: null,
    organization_id: null,
    model_provider: "anthropic",
    model_name: "model-two",
    model_metadata: {},
  },
]

beforeEach(() => {
  jest.mocked(useWorkspaceAgentModels).mockReturnValue({
    models,
    providers: [],
    catalogLoading: false,
    catalogError: null,
    providersLoading: false,
    providersError: null,
    modelsLoading: false,
    modelsError: null,
  })
})

function TestField({ value }: { value: Record<string, string> }) {
  const form = useForm({ defaultValues: { inputs: { model: value } } })
  return (
    <Form {...form}>
      <PolymorphicField
        label="Model"
        fieldName="inputs.model"
        fieldDefn={{
          type: ["object", "null"],
          "x-tracecat-component": [{ component_id: "agent-model" }],
        }}
      />
      <output data-testid="value">
        {JSON.stringify(form.watch("inputs.model"))}
      </output>
    </Form>
  )
}

it("matches by catalog id and writes the three-key model object", async () => {
  const user = userEvent.setup()
  render(
    <TestField
      value={{
        catalog_id: "catalog-two",
        model_name: "model-one",
        model_provider: "openai",
      }}
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent("model-twoAnthropic")
  await user.click(screen.getByRole("combobox"))
  await user.click(screen.getByRole("option", { name: /model-one/ }))
  expect(JSON.parse(screen.getByTestId("value").textContent ?? "{}")).toEqual({
    model_name: "model-one",
    model_provider: "openai",
    catalog_id: "catalog-one",
  })
})

it("shows the unavailable state for an unmatched saved model", () => {
  render(
    <TestField value={{ model_name: "removed", model_provider: "openai" }} />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "openai / removedUnavailable in this workspace"
  )
})

it("matches a saved model without a catalog id against a custom source", () => {
  jest.mocked(useWorkspaceAgentModels).mockReturnValue({
    models: [
      ...models,
      {
        id: "catalog-custom",
        custom_provider_id: "provider-one",
        organization_id: "organization-test",
        model_provider: "openai",
        model_name: "model-custom",
        model_metadata: {},
      },
    ],
    providers: [
      {
        id: "provider-one",
        organization_id: "organization-test",
        display_name: "Synthetic source",
        base_url: "https://example.com/v1",
        passthrough: false,
        api_key_header: null,
        last_refreshed_at: null,
      },
    ],
    catalogLoading: false,
    catalogError: null,
    providersLoading: false,
    providersError: null,
    modelsLoading: false,
    modelsError: null,
  })
  render(
    <TestField
      value={{ model_name: "model-custom", model_provider: "openai" }}
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "model-customSynthetic source"
  )
})

it("keeps the saved model visible, not unavailable, while models load", () => {
  jest.mocked(useWorkspaceAgentModels).mockReturnValue({
    models: undefined,
    providers: undefined,
    catalogLoading: true,
    catalogError: null,
    providersLoading: true,
    providersError: null,
    modelsLoading: true,
    modelsError: null,
  })
  const value = { model_name: "model-one", model_provider: "openai" }
  render(<TestField value={value} />)
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "openai / model-oneLoading models..."
  )
  expect(
    screen.queryByText("Unavailable in this workspace")
  ).not.toBeInTheDocument()
  expect(JSON.parse(screen.getByTestId("value").textContent ?? "{}")).toEqual(
    value
  )
})

it("keeps the saved model visible when the catalog fails to load", () => {
  const error = new ApiError(
    { method: "GET", url: "/agent/models" },
    {
      url: "/agent/models",
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      body: null,
    },
    "Internal Server Error"
  )
  jest.mocked(useWorkspaceAgentModels).mockReturnValue({
    models: undefined,
    providers: undefined,
    catalogLoading: false,
    catalogError: error,
    providersLoading: false,
    providersError: null,
    modelsLoading: false,
    modelsError: error,
  })
  const value = {
    catalog_id: "catalog-one",
    model_name: "model-one",
    model_provider: "openai",
  }
  render(<TestField value={value} />)
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "openai / model-oneFailed to load models"
  )
  expect(
    screen.queryByText("Unavailable in this workspace")
  ).not.toBeInTheDocument()
  expect(JSON.parse(screen.getByTestId("value").textContent ?? "{}")).toEqual(
    value
  )
})

it("shows only the load failure when no model is saved", () => {
  jest.mocked(useWorkspaceAgentModels).mockReturnValue({
    models: undefined,
    providers: undefined,
    catalogLoading: false,
    catalogError: null,
    providersLoading: false,
    providersError: null,
    modelsLoading: false,
    modelsError: new ApiError(
      { method: "GET", url: "/agent/models" },
      {
        url: "/agent/models",
        ok: false,
        status: 500,
        statusText: "Internal Server Error",
        body: null,
      },
      "Internal Server Error"
    ),
  })
  render(<TestField value={{}} />)
  expect(screen.getByRole("combobox")).toHaveTextContent(
    /^Failed to load models$/
  )
})
