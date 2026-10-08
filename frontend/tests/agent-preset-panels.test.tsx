import { zodResolver } from "@hookform/resolvers/zod"
import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useEffect } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import type { AgentPresetReadMinimal, SkillReadMinimal } from "@/client"
import {
  type AgentPresetFormValues,
  AgentPresetSkillsPanel,
  AgentPresetStructuredOutputPanel,
  AgentPresetSubagentsPanel,
  agentPresetSchema,
} from "@/components/agents/agent-presets-builder"
import { Form } from "@/components/ui/form"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useSkills } from "@/hooks/use-skills"

// These unrelated builder panes pull in ESM-only editor/chat dependencies.
jest.mock("@/components/chat/chat-session-pane", () => ({
  ChatSessionPane: () => null,
}))
jest.mock("@/components/tiptap-templates/simple/simple-editor", () => ({
  SimpleEditor: () => null,
}))
jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: ({
    value,
    onChange,
  }: {
    value: string
    onChange: (value: string) => void
  }) => (
    <textarea
      aria-label="JSON schema editor"
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  ),
}))
jest.mock("@/hooks/use-skills", () => ({ useSkills: jest.fn() }))

const preset: AgentPresetReadMinimal = {
  id: "preset-example",
  workspace_id: "workspace-example",
  name: "Example agent",
  slug: "example-agent",
  description: "An example subagent",
  model_provider: "openai",
  model_name: "example-model",
  current_version_id: "preset-version-example",
  capabilities: ["internet_access"],
  current_version_subagent_eligibility: { eligible: true },
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}
const subagent = {
  preset: preset.slug,
  presetId: preset.id,
  presetVersionId: preset.current_version_id ?? "",
  name: "",
  description: "",
  maxTurns: "",
}
const skill: SkillReadMinimal = {
  id: "skill-example",
  workspace_id: "workspace-example",
  name: "Example skill",
  slug: "example-skill",
  description: "Instructions for an example task",
  current_version_id: "skill-version-example",
  created_at: "2026-01-01T00:00:00Z",
  updated_at: "2026-01-01T00:00:00Z",
}

function TestForm({
  panel,
  defaults,
  error,
  presets = [preset],
  isSaving = false,
}: {
  panel: "output" | "skills" | "subagents"
  defaults?: Partial<AgentPresetFormValues>
  error?: "alias" | "output" | "json"
  presets?: AgentPresetReadMinimal[]
  isSaving?: boolean
}) {
  const form = useForm<AgentPresetFormValues>({
    resolver: zodResolver(agentPresetSchema),
    defaultValues: {
      name: "Parent",
      slug: "parent",
      model_provider: "openai",
      model_name: "example-model",
      retries: 3,
      outputTypeKind: "none",
      outputTypeDataType: "",
      subagents: [],
      skills: [],
      ...defaults,
    },
  })
  const subagents = useFieldArray({ control: form.control, name: "subagents" })
  const skills = useFieldArray({ control: form.control, name: "skills" })
  useEffect(() => {
    if (error === "alias") {
      form.setError("subagents.0.name", { message: "Alias must be unique" })
    }
    if (error === "output") {
      form.setError("outputTypeDataType", { message: "Select an output type" })
    }
    if (error === "json") {
      form.setError("outputTypeJson", { message: "Invalid JSON schema" })
    }
  }, [error, form])

  return (
    <TooltipProvider>
      <Form {...form}>
        {panel === "output" && (
          <AgentPresetStructuredOutputPanel form={form} isSaving={isSaving} />
        )}
        {panel === "subagents" && (
          <AgentPresetSubagentsPanel
            form={form}
            isSaving={isSaving}
            parentPreset={null}
            agentPresets={presets}
            subagentFields={subagents.fields}
            onAddSubagent={(binding) =>
              subagents.append(binding, { shouldFocus: false })
            }
            onRemoveSubagent={subagents.remove}
          />
        )}
        {panel === "skills" && (
          <AgentPresetSkillsPanel
            form={form}
            workspaceId="workspace-example"
            isSaving={isSaving}
            skillFields={skills.fields}
            savedBindings={[
              {
                skill_id: skill.id,
                skill_name: skill.name,
                skill_version_id: "saved-version",
                skill_version: 3,
              },
            ]}
            onAddSkillBinding={skills.append}
            onRemoveSkillBinding={skills.remove}
          />
        )}
        <button type="button" onClick={() => form.reset(form.getValues())}>
          Reset saved values
        </button>
        <button type="button" onClick={() => void form.trigger()}>
          Validate
        </button>
        <output data-testid="dirty">{String(form.formState.isDirty)}</output>
        <output data-testid="values">{JSON.stringify(form.watch())}</output>
      </Form>
    </TooltipProvider>
  )
}

function values(): AgentPresetFormValues {
  return JSON.parse(screen.getByTestId("values").textContent ?? "{}")
}

beforeEach(() => {
  jest.mocked(useSkills).mockReturnValue({
    skills: [skill],
    skillsLoading: false,
    skillsError: null,
  })
})

it("defaults structured output to str and toggles list output", async () => {
  const user = userEvent.setup()
  render(<TestForm panel="output" />)
  await user.click(screen.getByRole("button", { name: "Structured" }))
  expect(values().outputTypeDataType).toBe("str")
  expect(screen.getByText("str")).toBeInTheDocument()
  await user.click(screen.getByRole("switch", { name: "List of values" }))
  expect(values().outputTypeDataType).toBe("list[str]")
  expect(screen.getByText("list[str]")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Integer" }))
  expect(values().outputTypeDataType).toBe("list[int]")
  await user.click(screen.getByRole("switch", { name: "List of values" }))
  expect(values().outputTypeDataType).toBe("int")
})

it("preserves a saved type across format changes", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      panel="output"
      defaults={{
        outputTypeKind: "data-type",
        outputTypeDataType: "list[bool]",
      }}
    />
  )
  expect(screen.getByRole("button", { name: "Boolean" })).toHaveAttribute(
    "aria-pressed",
    "true"
  )
  expect(screen.getByRole("switch", { name: "List of values" })).toBeChecked()
  await user.click(screen.getByRole("button", { name: "Text" }))
  await user.click(screen.getByRole("button", { name: "Structured" }))
  expect(values().outputTypeDataType).toBe("list[bool]")
})

it("shows output validation and disables edits during save", () => {
  render(
    <TestForm
      panel="output"
      defaults={{ outputTypeKind: "data-type" }}
      error="output"
      isSaving
    />
  )
  expect(screen.getByText("Select an output type")).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Integer" })).toBeDisabled()
  expect(screen.getByRole("button", { name: "Text" })).toBeDisabled()
  expect(screen.getByRole("switch", { name: "List of values" })).toBeDisabled()
})

it("shows a saved skill version and removes the binding", async () => {
  const user = userEvent.setup()
  render(
    <TestForm panel="skills" defaults={{ skills: [{ skillId: skill.id }] }} />
  )
  expect(screen.getByText("v3")).toBeInTheDocument()
  expect(screen.queryByText(skill.description ?? "")).not.toBeInTheDocument()
  await user.hover(screen.getByRole("button", { name: skill.name }))
  expect(await screen.findByText(skill.description ?? "")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: `Remove ${skill.name}` }))
  expect(values().skills).toEqual([])
  expect(screen.queryByText("v3")).not.toBeInTheDocument()
})

it("adds an unsaved skill without a version badge", async () => {
  const user = userEvent.setup()
  const newSkill = { ...skill, id: "new-skill", name: "New skill" }
  jest.mocked(useSkills).mockReturnValue({
    skills: [newSkill],
    skillsLoading: false,
    skillsError: null,
  })
  render(<TestForm panel="skills" />)
  await user.click(screen.getByRole("button", { name: "Add skill" }))
  await user.click(screen.getByRole("option", { name: /New skill/ }))
  expect(values().skills).toEqual([{ skillId: newSkill.id }])
  expect(screen.queryByText(/^v\d+$/)).not.toBeInTheDocument()
})

it("explains the disabled skill picker in a tooltip", async () => {
  const user = userEvent.setup()
  render(
    <TestForm panel="skills" defaults={{ skills: [{ skillId: skill.id }] }} />
  )
  expect(screen.getByRole("button", { name: "Add skill" })).toBeDisabled()
  expect(
    screen.queryByText(
      "All workspace skills are already attached to this preset."
    )
  ).not.toBeInTheDocument()
  await user.tab()
  expect(await screen.findByRole("tooltip")).toHaveTextContent(
    "All workspace skills are already attached to this preset."
  )
})

it("adds a subagent from the dialog with immutable IDs and expands it", async () => {
  const user = userEvent.setup()
  render(<TestForm panel="subagents" />)
  await user.click(screen.getByRole("button", { name: "Add subagent" }))
  await user.click(screen.getByRole("option", { name: /Example agent/ }))
  expect(values().subagents).toEqual([subagent])
  expect(screen.getByRole("textbox", { name: "Alias" })).toHaveAttribute(
    "placeholder",
    preset.slug
  )
  expect(
    screen.getByRole("button", { name: /Example agent example-agent/ })
  ).toHaveAttribute("aria-expanded", "true")
  expect(screen.getByText("example-model")).toBeInTheDocument()
  expect(screen.getByText("Internet access limited")).toBeInTheDocument()
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  await user.click(
    screen.getByRole("button", { name: `Remove ${preset.name}` })
  )
  expect(values().subagents).toEqual([])
})

it("keeps a row with validation errors expanded", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      panel="subagents"
      defaults={{ subagents: [subagent] }}
      error="alias"
    />
  )
  expect(screen.getByText("Alias must be unique")).toBeInTheDocument()
  const toggle = screen.getByRole("button", {
    name: /Example agent example-agent/,
  })
  expect(toggle).toHaveAttribute("aria-expanded", "true")
  expect(toggle).toBeDisabled()
  expect(toggle).toHaveAttribute("aria-disabled", "true")
  expect(
    document.getElementById(toggle.getAttribute("aria-controls") ?? "")
  ).toBeInTheDocument()
  await user.click(toggle)
  expect(screen.getByRole("textbox", { name: "Alias" })).toBeInTheDocument()
})

it("keeps ineligible presets inspectable and expanded with their alert", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      panel="subagents"
      presets={[
        {
          ...preset,
          current_version_subagent_eligibility: {
            eligible: false,
            message: "This preset has attached subagents.",
          },
        },
      ]}
    />
  )
  await user.click(screen.getByRole("button", { name: "Add subagent" }))
  const option = screen.getByRole("option", { name: /Example agent/ })
  expect(
    within(option).getByLabelText("Cannot attach this preset")
  ).toBeInTheDocument()
  expect(
    within(option).getByText("This preset has attached subagents.")
  ).toBeInTheDocument()
  await user.click(option)
  expect(screen.getByText("Cannot attach this preset")).toBeInTheDocument()
  expect(
    screen.getByText("This preset has attached subagents.")
  ).toBeInTheDocument()
  expect(screen.getByRole("textbox", { name: "Alias" })).toBeInTheDocument()
})

it("shows saved subagents even if the preset catalog is empty", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      panel="subagents"
      presets={[]}
      defaults={{ subagents: [subagent] }}
    />
  )
  expect(screen.getByRole("button", { name: "Add subagent" })).toBeDisabled()
  const toggle = screen.getByRole("button", {
    name: /^example-agent unavailable/,
  })
  expect(toggle).toHaveAttribute("aria-expanded", "false")
  await user.click(toggle)
  expect(screen.getByRole("textbox", { name: "Alias" })).toBeInTheDocument()
})

it("keeps the remaining row's values and expanded state when the first row is removed", async () => {
  const user = userEvent.setup()
  const second = {
    ...subagent,
    name: "second",
    description: "Delegate this task",
    maxTurns: "5",
  }
  render(
    <TestForm
      panel="subagents"
      defaults={{ subagents: [{ ...subagent, name: "first" }, second] }}
    />
  )
  const toggle = screen.getByRole("button", { name: /Example agent second/ })
  expect(toggle).not.toHaveAttribute("aria-controls")
  await user.click(toggle)
  await user.type(screen.getByRole("textbox", { name: "Alias" }), "-edited")
  await user.click(
    screen.getAllByRole("button", { name: `Remove ${preset.name}` })[0]
  )
  expect(values().subagents).toEqual([{ ...second, name: "second-edited" }])
  expect(screen.getByRole("textbox", { name: "Alias" })).toHaveValue(
    "second-edited"
  )
  expect(screen.getByRole("textbox", { name: "Delegate when" })).toHaveValue(
    second.description
  )
  expect(screen.getByRole("spinbutton", { name: "Max turns" })).toHaveValue(5)
  expect(
    screen.getByRole("button", { name: /Example agent second-edited/ })
  ).toHaveAttribute("aria-expanded", "true")
})

it("starts saved rows collapsed and collapses them again after reset", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      panel="subagents"
      defaults={{ subagents: [subagent, { ...subagent, name: "second" }] }}
    />
  )
  expect(
    screen.queryByRole("textbox", { name: "Alias" })
  ).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: /Example agent second/ }))
  await user.click(screen.getByRole("button", { name: "Reset saved values" }))
  expect(
    screen.queryByRole("textbox", { name: "Alias" })
  ).not.toBeInTheDocument()
  expect(
    screen.getByRole("button", { name: /Example agent second/ })
  ).toHaveAttribute("aria-expanded", "false")
})

it("collapses a newly appended row after saving with reset", async () => {
  const user = userEvent.setup()
  render(<TestForm panel="subagents" />)
  await user.click(screen.getByRole("button", { name: "Add subagent" }))
  await user.click(screen.getByRole("option", { name: /Example agent/ }))
  expect(screen.getByRole("textbox", { name: "Alias" })).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Reset saved values" }))
  expect(
    screen.queryByRole("textbox", { name: "Alias" })
  ).not.toBeInTheDocument()
})

it.each([
  { rows: [subagent, subagent], message: "Subagent aliases must be unique" },
  {
    rows: [{ ...subagent, preset: "root", presetId: "" }],
    message: "This alias is reserved",
  },
  { rows: [{ ...subagent, name: "root" }], message: "This alias is reserved" },
])("renders schema error $message under Alias", async ({ rows, message }) => {
  const user = userEvent.setup()
  render(<TestForm panel="subagents" defaults={{ subagents: rows }} />)
  await user.click(screen.getByRole("button", { name: "Validate" }))
  const alias = await screen.findByRole("textbox", { name: "Alias" })
  expect(
    within(alias.parentElement as HTMLElement).getByText(message)
  ).toBeInTheDocument()
  expect(screen.getByText(message).tagName).toBe("P")
})

it("renders a missing preset error beside the row title", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      panel="subagents"
      defaults={{ subagents: [{ ...subagent, preset: "", presetId: "" }] }}
    />
  )
  await user.click(screen.getByRole("button", { name: "Validate" }))
  const toggle = screen.getByRole("button", { name: /^unavailable$/ })
  expect(
    within(toggle.parentElement as HTMLElement).getByText("Select a preset")
  ).toBeInTheDocument()
})

it("leaves the form clean after switching Structured then Text", async () => {
  const user = userEvent.setup()
  render(<TestForm panel="output" />)
  await user.click(screen.getByRole("button", { name: "Structured" }))
  expect(screen.getByTestId("dirty")).toHaveTextContent("true")
  await user.click(screen.getByRole("button", { name: "Text" }))
  expect(screen.getByTestId("dirty")).toHaveTextContent("false")
  expect(values().outputTypeDataType).toBe("")
})

it("preserves typed JSON through format switches and renders schema validation", async () => {
  const user = userEvent.setup()
  render(<TestForm panel="output" />)
  await user.click(screen.getByRole("button", { name: "JSON schema" }))
  const schema = '{"type":"object"}'
  await user.click(screen.getByRole("textbox", { name: "JSON schema editor" }))
  await user.paste(schema)
  await user.click(screen.getByRole("button", { name: "Structured" }))
  await user.click(screen.getByRole("button", { name: "Text" }))
  await user.click(screen.getByRole("button", { name: "JSON schema" }))
  expect(
    screen.getByRole("textbox", { name: "JSON schema editor" })
  ).toHaveValue(schema)
  await user.clear(screen.getByRole("textbox", { name: "JSON schema editor" }))
  await user.type(
    screen.getByRole("textbox", { name: "JSON schema editor" }),
    "invalid"
  )
  await user.click(screen.getByRole("button", { name: "Validate" }))
  expect(await screen.findByText("Invalid JSON")).toHaveClass(
    "text-destructive"
  )
})

it.each(["", "unknown"])("shows no pressed type for stored type %p", (type) => {
  render(
    <TestForm
      panel="output"
      defaults={{ outputTypeKind: "data-type", outputTypeDataType: type }}
    />
  )
  expect(
    within(screen.getByRole("group", { name: "Type" })).queryByRole("button", {
      pressed: true,
    })
  ).not.toBeInTheDocument()
})

it("shows loading skills with a disabled button and no tooltip trigger", () => {
  jest.mocked(useSkills).mockReturnValue({
    skills: undefined,
    skillsLoading: true,
    skillsError: null,
  })
  render(<TestForm panel="skills" />)
  expect(screen.getByText("Loading skills...")).toBeInTheDocument()
  const button = screen.getByRole("button", { name: "Add skill" })
  expect(button).toBeDisabled()
  expect(button.parentElement).not.toHaveAttribute("tabindex")
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument()
})

it("shows a skills loading error", () => {
  jest.mocked(useSkills).mockReturnValue({
    skills: undefined,
    skillsLoading: false,
    skillsError: Object.assign(new Error("Unavailable"), {
      body: { detail: "Unavailable" },
      url: "/skills",
      status: 503,
      statusText: "Service unavailable",
      request: { method: "GET" as const, url: "/skills" },
    }),
  })
  render(<TestForm panel="skills" />)
  expect(screen.getByText("Unable to load skills")).toBeInTheDocument()
  expect(screen.getByRole("button", { name: "Add skill" })).toBeDisabled()
})

it.each([
  { skills: [], bindings: [], reason: "No skills in this workspace yet" },
  {
    skills: [{ ...skill, current_version_id: null }],
    bindings: [],
    reason: "Only skills with published versions can be attached.",
  },
  {
    skills: [skill, { ...skill, id: "draft", current_version_id: null }],
    bindings: [{ skillId: skill.id }],
    reason: "All workspace skills are already attached to this preset.",
  },
])(
  "explains the disabled skills picker: $reason",
  async ({ skills, bindings, reason }) => {
    const user = userEvent.setup()
    jest
      .mocked(useSkills)
      .mockReturnValue({ skills, skillsLoading: false, skillsError: null })
    render(<TestForm panel="skills" defaults={{ skills: bindings }} />)
    expect(screen.getByRole("button", { name: "Add skill" })).toBeDisabled()
    if (!bindings.length)
      expect(screen.getByText("No skills attached yet.")).toBeInTheDocument()
    await user.tab()
    expect(await screen.findByRole("tooltip")).toHaveTextContent(reason)
    expect(document.activeElement).toHaveClass(
      "focus-visible:ring-inset",
      "focus-visible:ring-ring"
    )
  }
)
