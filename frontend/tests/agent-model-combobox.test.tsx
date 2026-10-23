import { render, screen } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { createRef } from "react"
import { useForm } from "react-hook-form"
import {
  AgentModelCombobox,
  type EnabledModelOption,
  getModelLifecycleNote,
} from "@/components/agents/agent-model-combobox"

import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"

const options: EnabledModelOption[] = [
  {
    catalogId: "catalog-one",
    sourceId: null,
    modelName: "model-one",
    modelProvider: "openai",
    iconId: "openai",
    displayName: "Model one",
    label: "Model one",
    metadata: "openai",
    sourceName: "OpenAI",
    sourceType: "platform",
    lifecycle: null,
  },
  {
    catalogId: "catalog-two",
    sourceId: "source-two",
    modelName: "model-two",
    modelProvider: "anthropic",
    iconId: "anthropic",
    displayName: "Model two",
    label: "Model two",
    metadata: "anthropic",
    sourceName: "Synthetic source",
    sourceType: "custom",
    lifecycle: null,
    baseUrl: "https://example.com/v1",
  },
]

it("matches saved models by catalog id before legacy fields", () => {
  render(
    <AgentModelCombobox
      options={options}
      value={{
        catalogId: "catalog-two",
        modelName: "model-one",
        modelProvider: "openai",
      }}
      onChange={jest.fn()}
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "Model twoSynthetic source"
  )
})

it("shows an unavailable saved value", () => {
  render(
    <AgentModelCombobox
      options={options}
      value={{ modelName: "removed", modelProvider: "openai" }}
      onChange={jest.fn()}
      unavailableLabel="Unavailable in this workspace"
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "openai / removedUnavailable in this workspace"
  )
})

it("searches and emits the selected model option", async () => {
  const user = userEvent.setup()
  const onChange = jest.fn()
  render(
    <AgentModelCombobox options={options} value={{}} onChange={onChange} />
  )
  await user.click(screen.getByRole("combobox"))
  await user.type(screen.getByPlaceholderText("Search models..."), "Synthetic")
  await user.click(screen.getByRole("option", { name: /Model two/ }))
  expect(onChange).toHaveBeenCalledWith(options[1])
})

it("shows deprecation notes and blocks picking a retired model", async () => {
  const user = userEvent.setup()
  const onChange = jest.fn()
  const lifecycleOptions: EnabledModelOption[] = [
    {
      ...options[0],
      lifecycle: {
        status: "deprecated",
        deprecated_on: "2026-09-30",
        retires_on: "2026-11-30",
        replacement: "model-two",
      },
    },
    {
      ...options[1],
      modelProvider: "openai",
      lifecycle: {
        status: "retired",
        retires_on: "2026-07-23",
        replacement: "model-one",
      },
    },
  ]
  render(
    <AgentModelCombobox
      options={lifecycleOptions}
      value={{ catalogId: "catalog-one" }}
      onChange={onChange}
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "Deprecated · retires Nov 30, 2026"
  )
  await user.click(screen.getByRole("combobox"))
  const retired = screen.getByRole("option", { name: /Model two/ })
  expect(retired).toHaveTextContent("Retired · runs on model-one")
  expect(retired).toHaveAttribute("aria-disabled", "true")
  await user.click(retired)
  expect(onChange).not.toHaveBeenCalled()
  // Without its replacement enabled, a retired model cannot run.
  expect(
    getModelLifecycleNote(lifecycleOptions[1], [lifecycleOptions[1]])
  ).toBe("Retired · enable model-one")
})

it("forwards form control props and the ref to the trigger", () => {
  const ref = createRef<HTMLButtonElement>()
  function LabelledCombobox() {
    const form = useForm({ defaultValues: { model_name: "" } })
    return (
      <Form {...form}>
        <FormField
          control={form.control}
          name="model_name"
          render={() => (
            <FormItem>
              <FormLabel>Model</FormLabel>
              <FormControl>
                <AgentModelCombobox
                  ref={ref}
                  options={options}
                  value={{}}
                  onChange={jest.fn()}
                />
              </FormControl>
            </FormItem>
          )}
        />
      </Form>
    )
  }
  render(<LabelledCombobox />)
  const trigger = screen.getByRole("combobox")
  expect(screen.getByLabelText("Model")).toBe(trigger)
  expect(trigger).toHaveAttribute("aria-describedby")
  expect(trigger).toHaveAttribute("aria-invalid", "false")
  expect(ref.current).toBe(trigger)
})

it("shows the placeholder, not the unavailable state, until options load", () => {
  const value = { modelName: "model-one", modelProvider: "openai" }
  const { rerender } = render(
    <AgentModelCombobox
      options={[]}
      value={value}
      onChange={jest.fn()}
      loaded={false}
      placeholder="No enabled models"
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent("No enabled models")
  expect(screen.queryByText("Legacy")).not.toBeInTheDocument()
  rerender(
    <AgentModelCombobox
      options={[]}
      value={value}
      onChange={jest.fn()}
      loaded
      placeholder="No enabled models"
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "openai / model-oneLegacy"
  )
})

it.each(["Loading models...", "Failed to load models"])(
  "keeps a saved value visible beside '%s' when options are missing",
  (unverifiedLabel) => {
    const onChange = jest.fn()
    render(
      <AgentModelCombobox
        options={[]}
        value={{
          catalogId: "catalog-one",
          modelName: "model-one",
          modelProvider: "openai",
        }}
        onChange={onChange}
        loaded={false}
        unverifiedLabel={unverifiedLabel}
        placeholder="Select a model"
        unavailableLabel="Unavailable in this workspace"
      />
    )
    const trigger = screen.getByRole("combobox")
    expect(trigger).toHaveTextContent(`openai / model-one${unverifiedLabel}`)
    expect(trigger).not.toHaveTextContent("Select a model")
    expect(trigger).not.toHaveTextContent("Unavailable in this workspace")
    expect(trigger).not.toHaveTextContent("Legacy")
    expect(onChange).not.toHaveBeenCalled()
  }
)

it("ignores the unverified label once options have loaded", () => {
  render(
    <AgentModelCombobox
      options={options}
      value={{ modelName: "removed", modelProvider: "openai" }}
      onChange={jest.fn()}
      unverifiedLabel="Failed to load models"
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "openai / removedLegacy"
  )
})

it("titles the trigger and option names so truncated text stays readable", async () => {
  const user = userEvent.setup()
  render(
    <AgentModelCombobox
      options={options}
      value={{ catalogId: "catalog-one" }}
      onChange={jest.fn()}
    />
  )
  expect(screen.getByText("Model one")).toHaveAttribute("title", "Model one")
  await user.click(screen.getByRole("combobox"))
  expect(
    screen.getByRole("option", { name: /Model two/ }).querySelector("[title]")
  ).toHaveAttribute("title", "Model two")
})

it("matches a custom-source model by name and provider only when asked", () => {
  const value = { modelName: "model-two", modelProvider: "anthropic" }
  const { rerender } = render(
    <AgentModelCombobox options={options} value={value} onChange={jest.fn()} />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "anthropic / model-twoLegacy"
  )
  rerender(
    <AgentModelCombobox
      options={options}
      value={value}
      onChange={jest.fn()}
      matchAnySource
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "Model twoSynthetic source"
  )
})

it("does not match a stale catalog id by name in any source", () => {
  render(
    <AgentModelCombobox
      options={options}
      value={{
        catalogId: "catalog-removed",
        modelName: "model-one",
        modelProvider: "openai",
      }}
      onChange={jest.fn()}
      matchAnySource
      unavailableLabel="Unavailable in this workspace"
    />
  )
  expect(screen.getByRole("combobox")).toHaveTextContent(
    "openai / model-oneUnavailable in this workspace"
  )
})

it("keeps rows that share a source, provider and name distinct", async () => {
  const user = userEvent.setup()
  const onChange = jest.fn()
  const organizationRow: EnabledModelOption = {
    ...options[0],
    catalogId: "catalog-organization",
    sourceType: "organization",
  }
  render(
    <AgentModelCombobox
      options={[options[0], organizationRow]}
      value={{ catalogId: "catalog-organization" }}
      onChange={onChange}
    />
  )
  await user.click(screen.getByRole("combobox"))
  const rows = screen.getAllByRole("option", { name: /Model one/ })
  expect(rows).toHaveLength(2)
  expect(rows[0]).toHaveAttribute("data-selected", "false")
  expect(rows[1]).toHaveAttribute("data-selected", "true")
  await user.click(rows[0])
  expect(onChange).toHaveBeenCalledWith(options[0])
})

it("highlights the selected model when the list is reopened", async () => {
  const user = userEvent.setup()
  render(
    <AgentModelCombobox
      options={options}
      value={{ catalogId: "catalog-two" }}
      onChange={jest.fn()}
    />
  )
  await user.click(screen.getByRole("combobox"))
  expect(screen.getByRole("option", { name: /Model two/ })).toHaveAttribute(
    "data-selected",
    "true"
  )
  expect(screen.getByRole("option", { name: /Model one/ })).toHaveAttribute(
    "data-selected",
    "false"
  )
})
