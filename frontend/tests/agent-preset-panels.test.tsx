import { zodResolver } from "@hookform/resolvers/zod"
import { act, render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { useEffect, useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import {
  type AgentPresetReadMinimal,
  type AgentPresetToolPolicyRead,
  agentPresetsPreviewToolPolicy,
  CancelablePromise,
  type SkillReadMinimal,
} from "@/client"
import {
  AgentPresetConfigurationPanel,
  type AgentPresetFormValues,
  AgentPresetSkillsPanel,
  AgentPresetStructuredOutputPanel,
  AgentPresetSubagentDetail,
  AgentPresetSubagentsPanel,
  agentPresetSchema,
  formValuesToPayload,
} from "@/components/agents/agent-presets-builder"
import { Form } from "@/components/ui/form"
import { TooltipProvider } from "@/components/ui/tooltip"
import { useSkills } from "@/hooks/use-skills"
import { QueryClient, QueryClientProvider } from "@/lib/query"
import { registryTool } from "./fixtures/agent-preset-tools"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  agentPresetsPreviewToolPolicy: jest.fn(),
}))

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
jest.mock("@/hooks/use-skill-library", () => ({
  useSkillLibrary: () => ({
    librarySkills: [],
    librarySkillsIsLoading: false,
    librarySkillsError: null,
  }),
}))

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
  openIndex: initialOpenIndex = null,
}: {
  panel: "output" | "skills" | "subagents"
  defaults?: Partial<AgentPresetFormValues>
  error?: "alias" | "output" | "json"
  presets?: AgentPresetReadMinimal[]
  isSaving?: boolean
  openIndex?: number | null
}) {
  const [openIndex, setOpenIndex] = useState(initialOpenIndex)
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
            onAddSubagent={(binding) => {
              setOpenIndex(subagents.fields.length)
              subagents.append(binding, { shouldFocus: false })
            }}
            onOpenSubagent={setOpenIndex}
          />
        )}
        {panel === "subagents" &&
        openIndex !== null &&
        subagents.fields[openIndex] ? (
          <AgentPresetSubagentDetail
            key={subagents.fields[openIndex].id}
            form={form}
            index={openIndex}
            isSaving={isSaving}
            parentPreset={null}
            agentPresets={presets}
            onRemove={(index) => {
              subagents.remove(index)
              setOpenIndex(null)
            }}
          />
        ) : null}
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

it("shows a saved skill without a version badge and removes the binding", async () => {
  const user = userEvent.setup()
  render(
    <TestForm panel="skills" defaults={{ skills: [{ skillId: skill.id }] }} />
  )
  expect(screen.queryByText("v3")).not.toBeInTheDocument()
  expect(screen.queryByText(skill.description ?? "")).not.toBeInTheDocument()
  await user.hover(screen.getByRole("button", { name: skill.name }))
  expect(
    await screen.findByText(skill.description ?? "", {}, { timeout: 2000 })
  ).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: `Remove ${skill.name}` }))
  expect(values().skills).toEqual([])
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
  expect(
    await screen.findByRole("tooltip", {}, { timeout: 2000 })
  ).toHaveTextContent(
    "All workspace skills are already attached to this preset."
  )
})

it("adds a subagent from the dialog with immutable IDs and opens it", async () => {
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
    screen.getByRole("button", { name: "Example agent example-agent" })
  ).toBeInTheDocument()
  expect(screen.getAllByText("example-model").length).toBeGreaterThan(0)
  expect(screen.getByText("Internet access limited")).toBeInTheDocument()
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Remove subagent" }))
  expect(values().subagents).toEqual([])
  expect(
    screen.queryByRole("textbox", { name: "Alias" })
  ).not.toBeInTheDocument()
})

it("flags a row with validation errors and shows them in its detail", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      panel="subagents"
      defaults={{ subagents: [subagent] }}
      error="alias"
    />
  )
  const row = screen.getByRole("button", {
    name: "Example agent example-agent",
  })
  expect(within(row).getByLabelText("Needs attention")).toBeInTheDocument()
  await user.click(row)
  expect(screen.getByText("Alias must be unique")).toBeInTheDocument()
})

it("keeps ineligible presets inspectable and opens them with their alert", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      panel="subagents"
      presets={[
        {
          ...preset,
          id: "eligible-preset",
          name: "Eligible agent",
          slug: "eligible-agent",
        },
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
  const reason = "This preset has attached subagents."
  expect(option).toHaveAttribute("aria-description", reason)
  expect(screen.queryByText(reason)).not.toBeInTheDocument()
  for (const row of screen.getAllByRole("option")) {
    expect(row).not.toHaveClass("flex-col")
    expect(row).toHaveClass("items-center")
  }
  await user.hover(option)
  expect(
    await screen.findByText(reason, {}, { timeout: 2000 })
  ).toBeInTheDocument()
  expect(within(option).queryByText(reason)).not.toBeInTheDocument()
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
  await user.click(
    screen.getByRole("button", { name: /^example-agent unavailable/ })
  )
  expect(screen.getByRole("textbox", { name: "Alias" })).toBeInTheDocument()
})

it("keeps the remaining subagent's values when another is removed", async () => {
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
  expect(
    screen.queryByRole("textbox", { name: "Alias" })
  ).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: /Example agent first/ }))
  await user.click(screen.getByRole("button", { name: "Remove subagent" }))
  expect(values().subagents).toEqual([second])
  await user.click(screen.getByRole("button", { name: /Example agent second/ }))
  await user.type(screen.getByRole("textbox", { name: "Alias" }), "-edited")
  expect(values().subagents).toEqual([{ ...second, name: "second-edited" }])
  expect(screen.getByRole("textbox", { name: "Delegate when" })).toHaveValue(
    second.description
  )
  expect(screen.getByRole("spinbutton", { name: "Max turns" })).toHaveValue(5)
})

it.each([
  {
    rows: [subagent, subagent],
    open: 1,
    message: "Subagent aliases must be unique",
  },
  {
    rows: [{ ...subagent, preset: "root", presetId: "" }],
    open: 0,
    message: "This alias is reserved",
  },
  {
    rows: [{ ...subagent, name: "root" }],
    open: 0,
    message: "This alias is reserved",
  },
])(
  "renders schema error $message under Alias",
  async ({ rows, open, message }) => {
    const user = userEvent.setup()
    render(
      <TestForm
        panel="subagents"
        defaults={{ subagents: rows }}
        openIndex={open}
      />
    )
    await user.click(screen.getByRole("button", { name: "Validate" }))
    const alias = await screen.findByRole("textbox", { name: "Alias" })
    expect(
      within(alias.parentElement as HTMLElement).getByText(message)
    ).toBeInTheDocument()
    expect(screen.getByText(message).tagName).toBe("P")
  }
)

it("renders a missing preset error in the subagent detail", async () => {
  const user = userEvent.setup()
  render(
    <TestForm
      panel="subagents"
      defaults={{ subagents: [{ ...subagent, preset: "", presetId: "" }] }}
      openIndex={0}
    />
  )
  await user.click(screen.getByRole("button", { name: "Validate" }))
  expect(await screen.findByText("Select a preset")).toBeInTheDocument()
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
  expect(
    screen.getByText("Unable to load workspace skills")
  ).toBeInTheDocument()
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
    reason: "Only skills with published versions can be attached.",
  },
  {
    skills: [skill],
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
    if (!bindings.length) expect(screen.getByText("None")).toBeInTheDocument()
    await user.tab()
    expect(
      await screen.findByRole("tooltip", {}, { timeout: 2000 })
    ).toHaveTextContent(reason)
    expect(document.activeElement).toHaveClass(
      "focus-visible:ring-inset",
      "focus-visible:ring-ring"
    )
  }
)

it("remembers Integer after Structured, Text, and Structured", async () => {
  const user = userEvent.setup()
  render(<TestForm panel="output" />)
  await user.click(screen.getByRole("button", { name: "Structured" }))
  await user.click(screen.getByRole("button", { name: "Integer" }))
  await user.click(screen.getByRole("button", { name: "Text" }))
  expect(values().outputTypeDataType).toBe("")
  expect(screen.getByTestId("dirty")).toHaveTextContent("false")
  await user.click(screen.getByRole("button", { name: "Structured" }))
  expect(screen.getByRole("button", { name: "Integer" })).toHaveAttribute(
    "aria-pressed",
    "true"
  )
  expect(values().outputTypeDataType).toBe("int")
})

it("preserves false approval entries in the form-to-payload mapping", () => {
  const formValues = agentPresetSchema.parse({
    name: "Example",
    slug: "example",
    outputTypeKind: "none",
    retries: 3,
    model_provider: "openai",
    model_name: "example-model",
    toolApprovals: [
      { tool: "tools.test.ask", allow: false },
      { tool: "tools.test.auto", allow: true },
    ],
  })
  expect(formValuesToPayload(formValues).tool_approvals).toEqual({
    "tools.test.ask": false,
    "tools.test.auto": true,
  })
})

function ConfigurationTestForm({
  savedSkillIds,
  skillIds = savedSkillIds ?? [],
  savedNamespaces = ["tools.test"],
  namespaces = savedNamespaces,
}: {
  savedSkillIds?: string[]
  skillIds?: string[]
  savedNamespaces?: string[]
  namespaces?: string[]
}) {
  const form = useForm<AgentPresetFormValues>({
    defaultValues: {
      actions: ["tools.test.authored"],
      namespaces,
      mcpIntegrations: ["mcp-example"],
      toolApprovals: [],
      skills: skillIds.map((skillId) => ({ skillId })),
    },
  })
  return (
    <TooltipProvider>
      <Form {...form}>
        <AgentPresetConfigurationPanel
          workspaceId="workspace-example"
          savedSkillIds={savedSkillIds}
          savedNamespaces={savedNamespaces}
          maxTools={savedSkillIds === undefined ? undefined : 128}
          savedSkillActions={["tools.test.authored", "tools.test.saved"]}
          form={form}
          isSaving={false}
          registryActions={[
            registryTool("tools.test.authored"),
            registryTool("tools.test.saved"),
            registryTool("tools.test.preview"),
          ]}
          registryLoading={false}
          mcpLoading={false}
          toolsLoadError={false}
          mcpIntegrations={[]}
          onOpenGroup={() => {}}
        />
        <button
          type="button"
          onClick={() => form.setValue("skills", [{ skillId: "new-skill" }])}
        >
          Change skills
        </button>
        <button type="button" onClick={() => form.setValue("skills", [])}>
          Remove skills
        </button>
        <button
          type="button"
          onClick={() =>
            form.setValue("skills", [{ skillId: "invalid-skill" }])
          }
        >
          Invalid skills
        </button>
      </Form>
    </TooltipProvider>
  )
}

function renderConfiguration(
  props: Parameters<typeof ConfigurationTestForm>[0]
) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return render(
    <QueryClientProvider client={client}>
      <ConfigurationTestForm {...props} />
    </QueryClientProvider>
  )
}

it("uses saved skill policy when ids and namespace filters are unchanged, ignoring order and empty ids", () => {
  jest.mocked(agentPresetsPreviewToolPolicy).mockClear()
  renderConfiguration({
    savedSkillIds: ["first-skill", "second-skill"],
    skillIds: ["second-skill", "", "first-skill"],
    savedNamespaces: ["tools.test", "tools.other"],
    namespaces: ["tools.other", "tools.test"],
  })
  expect(agentPresetsPreviewToolPolicy).not.toHaveBeenCalled()
  const group = screen.getByText("From skills").parentElement as HTMLElement
  expect(within(group).getByText("tools.test.saved")).toBeInTheDocument()
  expect(
    within(group).queryByText("tools.test.authored")
  ).not.toBeInTheDocument()
})

it("toggles approval for a tool that only a skill provides", async () => {
  const user = userEvent.setup()
  renderConfiguration({ savedSkillIds: ["first-skill"] })
  const toggle = screen.getByRole("button", {
    name: "Require approval for tools.test.saved",
  })
  expect(toggle).toHaveAttribute("aria-pressed", "false")
  await user.click(toggle)
  expect(toggle).toHaveAttribute("aria-pressed", "true")
  expect(toggle).toHaveTextContent("Needs approval")
})

it("previews changed skills and removes the group without a request when all skills are removed", async () => {
  const user = userEvent.setup()
  const preview = jest
    .mocked(agentPresetsPreviewToolPolicy)
    .mockClear()
    .mockResolvedValue({
      actions: ["tools.test.preview"],
      skill_actions: ["tools.test.preview"],
    })
  renderConfiguration({ savedSkillIds: ["saved-skill"] })
  expect(preview).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "Change skills" }))
  await waitFor(() =>
    expect(preview).toHaveBeenCalledWith({
      workspaceId: "workspace-example",
      requestBody: {
        actions: [],
        namespaces: ["tools.test"],
        mcp_integrations: ["mcp-example"],
        skill_ids: ["new-skill"],
        tool_approvals: {},
      },
    })
  )
  expect(await screen.findByText("tools.test.preview")).toBeInTheDocument()
  const group = screen.getByText("From skills").parentElement as HTMLElement
  expect(within(group).getByText("tools.test.preview")).toBeInTheDocument()
  expect(screen.queryByText("tools.test.saved")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Remove skills" }))
  expect(screen.queryByText("From skills")).not.toBeInTheDocument()
  expect(preview).toHaveBeenCalledTimes(1)
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  expect(
    screen.getByText("1 of 128 tools · 1 MCP integration")
  ).toBeInTheDocument()
})

it("hides saved skill tools during the first preview and hides previous tools after an error", async () => {
  const user = userEvent.setup()
  let resolvePreview: (policy: AgentPresetToolPolicyRead) => void = () => {}
  const pending = new CancelablePromise<AgentPresetToolPolicyRead>(
    (resolve) => {
      resolvePreview = resolve
    }
  )
  const preview = jest
    .mocked(agentPresetsPreviewToolPolicy)
    .mockClear()
    .mockReturnValueOnce(pending)
    .mockRejectedValueOnce({ status: 400 })
  renderConfiguration({ savedSkillIds: ["saved-skill"] })
  await user.click(screen.getByRole("button", { name: "Change skills" }))
  expect(preview).toHaveBeenCalledTimes(1)
  expect(screen.queryByText("From skills")).not.toBeInTheDocument()
  await act(async () => {
    resolvePreview({
      actions: ["tools.test.preview"],
      skill_actions: ["tools.test.preview"],
    })
    await pending
  })
  expect(await screen.findByText("tools.test.preview")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Invalid skills" }))
  await waitFor(() =>
    expect(screen.queryByText("From skills")).not.toBeInTheDocument()
  )
  expect(preview).toHaveBeenCalledTimes(2)
})

it("previews cleared namespace filters even when skills are unchanged", async () => {
  const user = userEvent.setup()
  const preview = jest
    .mocked(agentPresetsPreviewToolPolicy)
    .mockClear()
    .mockResolvedValue({
      actions: ["tools.test.authored", "tools.test.preview"],
      skill_actions: ["tools.test.authored", "tools.test.preview"],
    })
  renderConfiguration({ savedSkillIds: ["saved-skill"] })
  expect(preview).not.toHaveBeenCalled()
  await user.click(screen.getByRole("button", { name: "Clear" }))
  expect(await screen.findByText("tools.test.preview")).toBeInTheDocument()
  expect(preview).toHaveBeenCalledTimes(1)
  expect(preview).toHaveBeenCalledWith({
    workspaceId: "workspace-example",
    requestBody: {
      actions: [],
      namespaces: [],
      mcp_integrations: ["mcp-example"],
      skill_ids: ["saved-skill"],
      tool_approvals: {},
    },
  })
  expect(screen.queryByText("tools.test.saved")).not.toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  expect(
    screen.getByText("2 of 128 tools (1 from skills) · 1 MCP integration")
  ).toBeInTheDocument()
})

it("hides previous preview tools while changed skills are pending", async () => {
  const user = userEvent.setup()
  let resolvePreview: (policy: AgentPresetToolPolicyRead) => void = () => {}
  const pending = new CancelablePromise<AgentPresetToolPolicyRead>(
    (resolve) => {
      resolvePreview = resolve
    }
  )
  const preview = jest
    .mocked(agentPresetsPreviewToolPolicy)
    .mockClear()
    .mockResolvedValueOnce({
      actions: ["tools.test.preview"],
      skill_actions: ["tools.test.preview"],
    })
    .mockReturnValueOnce(pending)
  renderConfiguration({ skillIds: ["first-skill"] })
  expect(await screen.findByText("tools.test.preview")).toBeInTheDocument()
  await user.click(screen.getByRole("button", { name: "Change skills" }))
  expect(preview).toHaveBeenCalledTimes(2)
  expect(screen.queryByText("From skills")).not.toBeInTheDocument()
  expect(screen.queryByText("tools.test.preview")).not.toBeInTheDocument()
  await act(async () => {
    resolvePreview({
      actions: ["tools.test.saved"],
      skill_actions: ["tools.test.saved"],
    })
    await pending
  })
  expect(await screen.findByText("tools.test.saved")).toBeInTheDocument()
  expect(screen.queryByText("tools.test.preview")).not.toBeInTheDocument()
})

it("previews a new preset without skills once and uses its tool limit", async () => {
  const user = userEvent.setup()
  const preview = jest
    .mocked(agentPresetsPreviewToolPolicy)
    .mockClear()
    .mockResolvedValue({ actions: [], skill_actions: [], max_tools: 7 })
  renderConfiguration({})
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  expect(
    await screen.findByText("1 of 7 tools · 1 MCP integration")
  ).toBeInTheDocument()
  expect(preview).toHaveBeenCalledTimes(1)
  expect(preview).toHaveBeenCalledWith(
    expect.objectContaining({
      requestBody: expect.objectContaining({ actions: [], skill_ids: [] }),
    })
  )
})

it("previews skills on a new preset and uses the preview tool limit", async () => {
  const user = userEvent.setup()
  const preview = jest
    .mocked(agentPresetsPreviewToolPolicy)
    .mockClear()
    .mockResolvedValue({
      actions: ["tools.test.preview"],
      skill_actions: ["tools.test.preview"],
      max_tools: 7,
    })
  renderConfiguration({ skillIds: ["new-skill"] })
  expect(await screen.findByText("tools.test.preview")).toBeInTheDocument()
  expect(preview).toHaveBeenCalledTimes(1)
  await user.click(screen.getByRole("button", { name: "Add tools" }))
  expect(
    screen.getByText("2 of 7 tools (1 from skills) · 1 MCP integration")
  ).toBeInTheDocument()
})

it("opens a subagent from its row and links to the attached preset", async () => {
  const user = userEvent.setup()
  render(<TestForm panel="subagents" defaults={{ subagents: [subagent] }} />)
  await user.click(
    screen.getByRole("button", { name: "Example agent example-agent" })
  )
  expect(screen.getByRole("link", { name: "Example agent" })).toHaveAttribute(
    "href",
    "/workspaces/workspace-example/agents/preset-example"
  )
  await user.type(
    screen.getByRole("textbox", { name: "Delegate when" }),
    "Example task"
  )
  expect(values().subagents[0].description).toBe("Example task")
  await user.click(screen.getByRole("button", { name: "Remove subagent" }))
  expect(values().subagents).toEqual([])
})
