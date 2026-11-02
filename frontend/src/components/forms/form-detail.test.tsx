import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type FormIssue,
  type FormRead,
  type FormsValidateFormData,
  formsDownloadFormFile,
  formsGetForm,
  formsListFormSubmissions,
  formsUpdateForm,
  formsValidateForm,
} from "@/client"
import { FormDetail } from "@/components/forms/form-detail"
import { TooltipProvider } from "@/components/ui/tooltip"
import { formKeys } from "@/hooks/use-forms"
import { fieldBlock } from "@/lib/forms/test-fixtures"
import { QueryClient, QueryClientProvider } from "@/lib/query"

let scopes = new Set<string>()
let searchParams = new URLSearchParams()

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace: jest.fn() }),
  usePathname: () => "/workspaces/workspace-1/forms/form-1",
  useSearchParams: () => searchParams,
}))
jest.mock("@/client", () => ({
  formsDownloadFormFile: jest.fn(),
  formsGetForm: jest.fn(),
  formsListFormSubmissions: jest.fn(),
  formsSearchFormUsers: jest.fn(),
  formsUpdateForm: jest.fn(),
  formsValidateForm: jest.fn(),
}))
jest.mock("@/components/auth/scope-guard", () => ({
  useScopeCheck: (scope: string) => scopes.has(scope),
}))
jest.mock("@/providers/workspace-id", () => ({
  useWorkspaceId: () => "workspace-1",
}))
// CodeMirror needs layout that jsdom lacks; a text box is enough here.
jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: (props: {
    value: string
    onChange: (value: string) => void
    readOnly: boolean
  }) => (
    <textarea
      aria-label="Form config"
      value={props.value}
      readOnly={props.readOnly}
      onChange={(event) => props.onChange(event.target.value)}
    />
  ),
}))

const updateForm = jest.mocked(formsUpdateForm)
const validateForm = jest.mocked(formsValidateForm)

const SAVED_YAML = "title: Laptop request\nlayout:\n  - field: model\n"
const WIDGET_ISSUE: FormIssue = {
  severity: "error",
  code: "incompatible_widget",
  message: "cards does not accept a bool input",
  loc: ["layout", 0, "widget"],
}

function formRead(overrides: Partial<FormRead> = {}): FormRead {
  return {
    id: "form-1",
    workspace_id: "workspace-1",
    workflow_id: "wf_3KbAYlkEEToril859NNEOb",
    workflow_title: "Provision laptop",
    workflow_version: 4,
    config: { title: "Laptop request", layout: [{ field: "model" }] },
    config_version: "v-saved",
    counts: { views: 311, submissions: 42 },
    ...overrides,
  } as FormRead
}

function renderDetail() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = render(<FormDetail formId="form-1" />, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    ),
  })
  return { ...view, queryClient, user: userEvent.setup() }
}

const editor = () => screen.getByLabelText("Form config")
const saveButton = () => screen.getByRole("button", { name: "Save" })
const edit = (text: string) =>
  fireEvent.change(editor(), { target: { value: text } })
async function previewTitle(name: string) {
  const pane = await screen.findByRole("region", { name: "Preview" })
  return within(pane).findByRole("heading", { name }, { timeout: 3000 })
}

beforeEach(() => {
  jest.clearAllMocks()
  sessionStorage.clear()
  scopes = new Set([
    "form:read",
    "form:update",
    "form:submission:read",
    "workflow:read",
  ])
  searchParams = new URLSearchParams()
  jest.mocked(formsGetForm).mockResolvedValue(formRead())
  // The server previews each config under its title; "Broken" has an issue.
  validateForm.mockImplementation((async ({
    requestBody: { config },
  }: FormsValidateFormData) => {
    const broken = config.title === "Broken"
    return {
      valid: !broken,
      issues: broken ? [WIDGET_ISSUE] : [],
      preview: {
        title: config.title ?? "Untitled",
        blocks: [fieldBlock({ name: "model", label: "Model" })],
      },
    }
  }) as unknown as typeof formsValidateForm)
})

describe("FormDetail config", () => {
  it("previews the saved config through the form's validate route", async () => {
    renderDetail()

    await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
    expect(await previewTitle("Laptop request")).toBeInTheDocument()
    expect(validateForm).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      formId: "form-1",
      requestBody: {
        config: { title: "Laptop request", layout: [{ field: "model" }] },
      },
    })
    // Forms carry the workflow's short ID, which the builder route takes.
    expect(
      screen.getByRole("link", { name: "Provision laptop v4" })
    ).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/workflows/wf_3KbAYlkEEToril859NNEOb"
    )
  })

  it("names the workflow as plain text without workflow:read", async () => {
    scopes = new Set(["form:read", "form:update"])

    renderDetail()

    expect(await screen.findByText("Provision laptop v4")).toBeInTheDocument()
    expect(
      screen.queryByRole("link", { name: "Provision laptop v4" })
    ).toBeNull()
  })

  it("saves the YAML as JSON, or lists the problems a save was rejected with", async () => {
    updateForm.mockRejectedValueOnce(
      Object.assign(new Error("Unprocessable"), {
        status: 422,
        body: {
          detail: {
            code: "form_config_invalid",
            message: "Invalid form config",
            issues: [WIDGET_ISSUE],
          },
        },
      })
    )
    updateForm.mockResolvedValue(formRead({ config: { title: "Order" } }))
    const { user } = renderDetail()
    await waitFor(() => expect(saveButton()).toBeDisabled())

    edit("layout:\n  - field: model\n    widget: cards\n")
    await user.click(saveButton())
    expect(
      await screen.findByRole("button", { name: "1 error blocks saving" })
    ).toBeInTheDocument()
    expect(editor()).toHaveValue(
      "layout:\n  - field: model\n    widget: cards\n"
    )

    edit("# Renamed\ntitle: Order\n")
    expect(screen.getByText("Unsaved changes")).toBeInTheDocument()
    await user.click(saveButton())
    // The server stores JSON, so the comment does not come back.
    await waitFor(() => expect(editor()).toHaveValue("title: Order\n"))
    expect(updateForm).toHaveBeenLastCalledWith({
      workspaceId: "workspace-1",
      formId: "form-1",
      requestBody: {
        config: { title: "Order" },
        base_config_version: "v-saved",
      },
    })
    expect(saveButton()).toBeDisabled()
  })

  it("blocks saving on problems and keeps the last good preview, dimmed", async () => {
    renderDetail()
    await previewTitle("Laptop request")

    edit("title: Broken\n")

    expect(
      await screen.findByRole(
        "button",
        { name: "1 error blocks saving" },
        { timeout: 3000 }
      )
    ).toBeInTheDocument()
    expect(saveButton()).toBeDisabled()
    const pane = screen.getByRole("region", { name: "Preview" })
    expect(pane).toHaveTextContent("Showing the last valid config")
    expect(
      within(pane)
        .getByRole("heading", { name: "Laptop request" })
        .closest("[data-stale]")
    ).toHaveAttribute("data-stale", "true")

    // A YAML syntax error never reaches the server.
    const calls = validateForm.mock.calls.length
    edit("title: [Broken\n")
    await act(() => new Promise((resolve) => setTimeout(resolve, 700)))
    expect(validateForm).toHaveBeenCalledTimes(calls)
  })

  it("warns about a remote change under unsaved edits and saves only on Overwrite", async () => {
    updateForm.mockResolvedValue(formRead({ config: { title: "Mine" } }))
    const { queryClient, user } = renderDetail()
    await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
    const remote = formRead({ config: { title: "Renamed by chat" } })

    // A clean buffer follows the saved config.
    act(() =>
      queryClient.setQueryData(formKeys.detail("workspace-1", "form-1"), remote)
    )
    await waitFor(() =>
      expect(editor()).toHaveValue("title: Renamed by chat\n")
    )

    edit("title: Mine\n")
    act(() =>
      queryClient.setQueryData(
        formKeys.detail("workspace-1", "form-1"),
        formRead({ config: { title: "Renamed again" } })
      )
    )
    expect(
      await screen.findByText(/This form changed elsewhere/)
    ).toBeInTheDocument()
    expect(editor()).toHaveValue("title: Mine\n")
    expect(saveButton()).toBeDisabled()

    await user.click(screen.getByRole("button", { name: "Overwrite" }))
    await waitFor(() =>
      expect(updateForm).toHaveBeenCalledWith(
        expect.objectContaining({ requestBody: { config: { title: "Mine" } } })
      )
    )
  })

  it("refuses a save over a config that changed since it was loaded, then offers Overwrite", async () => {
    updateForm.mockRejectedValueOnce(
      Object.assign(new Error("Conflict"), {
        status: 409,
        body: {
          detail: {
            code: "form_changed",
            message: "This form changed since you opened it",
          },
        },
      })
    )
    updateForm.mockResolvedValue(
      formRead({ config: { title: "Mine" }, config_version: "v-mine" })
    )
    const { user } = renderDetail()
    await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
    // A colleague saves while this editor's copy is still the one it loaded.
    jest.mocked(formsGetForm).mockResolvedValue(
      formRead({
        config: { title: "Renamed by a colleague" },
        config_version: "v-colleague",
      })
    )

    edit("title: Mine\n")
    await user.click(saveButton())

    expect(updateForm).toHaveBeenCalledWith(
      expect.objectContaining({
        requestBody: {
          config: { title: "Mine" },
          base_config_version: "v-saved",
        },
      })
    )
    expect(
      await screen.findByText(/This form changed elsewhere/)
    ).toBeInTheDocument()
    expect(editor()).toHaveValue("title: Mine\n")

    await user.click(screen.getByRole("button", { name: "Overwrite" }))
    await waitFor(() =>
      expect(updateForm).toHaveBeenLastCalledWith(
        expect.objectContaining({ requestBody: { config: { title: "Mine" } } })
      )
    )
  })

  it("restores an unsaved draft after in-app navigation until it is discarded", async () => {
    const first = renderDetail()
    await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
    edit("title: Half done\n")
    first.unmount()

    const { user } = renderDetail()
    await waitFor(() => expect(editor()).toHaveValue("title: Half done\n"))
    expect(screen.getByText(/Unsaved draft restored/)).toBeInTheDocument()

    await user.click(screen.getByRole("button", { name: "Discard draft" }))
    expect(editor()).toHaveValue(SAVED_YAML)
    cleanup()
    renderDetail()
    await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
  })

  it("survives a restored draft with an unresolved alias and marks it", async () => {
    const first = renderDetail()
    await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
    edit("title: *missing\n")
    expect(
      await screen.findByRole(
        "button",
        { name: "1 error blocks saving" },
        { timeout: 3000 }
      )
    ).toBeInTheDocument()
    first.unmount()

    renderDetail()

    await waitFor(() => expect(editor()).toHaveValue("title: *missing\n"))
    expect(
      await screen.findByRole(
        "button",
        { name: "1 error blocks saving" },
        { timeout: 3000 }
      )
    ).toBeInTheDocument()
  })

  it("gives a builder without form:update a read-only editor and the saved config's preview", async () => {
    scopes = new Set(["form:read", "form:submission:read"])

    renderDetail()

    expect(await previewTitle("Laptop request")).toBeInTheDocument()
    expect(editor()).toHaveAttribute("readonly")
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull()
    expect(screen.queryByRole("button", { name: "Insert image" })).toBeNull()
  })
})

describe("FormDetail activity", () => {
  beforeEach(() => {
    searchParams = new URLSearchParams("tab=activity")
    jest.mocked(formsListFormSubmissions).mockResolvedValue({
      items: [
        {
          id: "submission-1",
          form_id: "form-1",
          reference: "FRM-8F3K2A1C",
          answers: {
            model: "mbp14",
            ship_to_home: true,
            manager: {
              user_id: "user-2",
              email: "daniel.reyes@example.com",
              first_name: "Daniel",
              last_name: "Reyes",
            },
            quote: [
              {
                id: "file-1",
                name: "quote.pdf",
                content_type: "application/pdf",
                size: 2048,
                sha256: "0".repeat(64),
              },
            ],
          },
          submitted_by: {
            type: "user",
            id: "user-1",
            email: "maya.okafor@example.com",
            first_name: "Maya",
            last_name: "Okafor",
          },
          wf_exec_id: "wf_3KbAYlkEEToril859NNEOb/exec_2a",
          run_status: "COMPLETED",
          submitted_at: "2026-10-12T10:42:00Z",
        },
      ],
      has_more: false,
    } as never)
  })

  it("shows the counts, the run and each answer under its field's label", async () => {
    jest.mocked(formsDownloadFormFile).mockResolvedValue({
      download_url: "https://storage.example.com/file-1?signature=x",
      file_name: "quote.pdf",
      content_type: "application/pdf",
    })
    const clicked: string[] = []
    jest
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(function (this: HTMLAnchorElement) {
        clicked.push(this.href)
      })
    const { user } = renderDetail()

    expect(await screen.findByText("Maya Okafor")).toBeInTheDocument()
    expect(screen.getByText("311")).toBeInTheDocument()
    expect(screen.getByText("42")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Run" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/workflows/wf_3KbAYlkEEToril859NNEOb/executions/exec_2a"
    )
    await user.click(
      screen.getByRole("button", { name: "Show answers of FRM-8F3K2A1C" })
    )

    const answerOf = (term: string) =>
      screen.getByText(term, { selector: "dt" }).nextElementSibling
    // Labels come from the preview; inputs with no field keep their name.
    await waitFor(() => expect(answerOf("Model")).toHaveTextContent("mbp14"))
    expect(answerOf("ship_to_home")).toHaveTextContent("Yes")
    expect(answerOf("manager")).toHaveTextContent(
      "Daniel Reyes (daniel.reyes@example.com)"
    )

    await user.click(screen.getByRole("button", { name: "Download quote.pdf" }))
    await waitFor(() =>
      expect(clicked).toEqual([
        "https://storage.example.com/file-1?signature=x",
      ])
    )
    expect(formsDownloadFormFile).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      formId: "form-1",
      fileId: "file-1",
    })
    jest.restoreAllMocks()
  })

  it("shows the run as plain text without workflow:read", async () => {
    scopes = new Set(["form:read", "form:submission:read"])

    renderDetail()

    expect(await screen.findByText("Run")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "Run" })).toBeNull()
  })

  it("asks for no submissions without form:submission:read", async () => {
    scopes = new Set(["form:read", "form:update"])

    renderDetail()

    expect(
      await screen.findByText("No access to submissions")
    ).toBeInTheDocument()
    expect(formsListFormSubmissions).not.toHaveBeenCalled()
  })
})
