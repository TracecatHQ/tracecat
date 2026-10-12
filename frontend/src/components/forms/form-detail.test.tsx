import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  type FormConfig_Input,
  type FormIssue,
  type FormPreview,
  type FormRead,
  type FormSubmissionRead,
  type FormsValidateFormData,
  type FormValidateResponse,
  formsDownloadFormFile,
  formsGetForm,
  formsListFormSubmissions,
  formsPreviewForm,
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
const replace = jest.fn()

jest.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  usePathname: () => "/workspaces/workspace-1/forms/form-1",
  useSearchParams: () => searchParams,
}))

jest.mock("@/client", () => ({
  formsDownloadFormFile: jest.fn(),
  formsGetForm: jest.fn(),
  formsListFormSubmissions: jest.fn(),
  formsPreviewForm: jest.fn(),
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

// CodeMirror needs layout that jsdom does not have. The config tab only
// needs a text box here; the editor's own behaviour has its own tests.
jest.mock("@/components/editor/codemirror/code-editor", () => ({
  CodeEditor: ({
    value,
    onChange,
    readOnly,
  }: {
    value: string
    onChange?: (value: string) => void
    readOnly?: boolean
  }) => (
    <textarea
      aria-label="Form config"
      value={value}
      readOnly={readOnly}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}))

const getForm = jest.mocked(formsGetForm)
const updateForm = jest.mocked(formsUpdateForm)
const validateForm = jest.mocked(formsValidateForm)
const listSubmissions = jest.mocked(formsListFormSubmissions)
const previewForm = jest.mocked(formsPreviewForm)

const SAVED_YAML = "title: Laptop request\nlayout:\n  - field: model\n"

function formRead(overrides: Partial<FormRead> = {}): FormRead {
  return {
    id: "form-1",
    workspace_id: "workspace-1",
    workflow_id: "00000000-0000-4000-8000-000000000001",
    workflow_title: "Provision laptop",
    workflow_version: 4,
    status: "online",
    config: { title: "Laptop request", layout: [{ field: "model" }] },
    issues: [],
    url: "https://app.example.com/forms/form-1",
    counts: { views: 311, submissions: 42 },
    created_at: "2026-10-01T09:00:00Z",
    updated_at: "2026-10-01T09:00:00Z",
    ...overrides,
  }
}

function preview(title: string): FormPreview {
  return {
    title,
    definition_version: 4,
    blocks: [fieldBlock({ name: "model", label: "Model" })],
  }
}

function valid(title: string): FormValidateResponse {
  return { valid: true, issues: [], preview: preview(title) }
}

function invalid(...issues: FormIssue[]): FormValidateResponse {
  return { valid: false, issues, preview: preview("Broken") }
}

const WIDGET_ISSUE: FormIssue = {
  severity: "error",
  code: "incompatible_widget",
  message: "cards does not accept a bool input",
  loc: ["layout", 0, "widget"],
}

function apiError(status: number, detail: unknown) {
  return Object.assign(new Error("Request failed"), {
    status,
    body: { detail },
  })
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

function renderDetail() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  render(<FormDetail formId="form-1" />, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    ),
  })
  return { queryClient, user: userEvent.setup() }
}

const editor = () => screen.getByLabelText("Form config")
const saveButton = () => screen.getByRole("button", { name: "Save" })
const previewPane = () => screen.getByRole("region", { name: "Preview" })
async function previewTitle(name: string) {
  const pane = await screen.findByRole("region", { name: "Preview" })
  return within(pane).findByRole("heading", { name }, { timeout: 3000 })
}

function edit(text: string) {
  fireEvent.change(editor(), { target: { value: text } })
}

/** Answer each check of a config, as the server's validate route would. */
function answerChecks(
  answer: (
    config: FormConfig_Input
  ) => FormValidateResponse | Promise<FormValidateResponse>
) {
  const implementation = async ({ requestBody }: FormsValidateFormData) =>
    answer(requestBody.config)
  validateForm.mockImplementation(
    implementation as unknown as typeof formsValidateForm
  )
}

/** Configs the server was asked to check, oldest first. */
function checkedConfigs() {
  return validateForm.mock.calls.map(([data]) => data.requestBody.config)
}

beforeEach(() => {
  jest.clearAllMocks()
  scopes = new Set(["form:read", "form:update", "form:submission:read"])
  searchParams = new URLSearchParams()
  getForm.mockResolvedValue(formRead())
  answerChecks((config) => valid(config.title ?? "Untitled"))
  listSubmissions.mockResolvedValue({
    items: [],
    next_cursor: null,
    prev_cursor: null,
    has_more: false,
    has_previous: false,
  })
})

describe("FormDetail", () => {
  it("shows the saved config as YAML with a preview and the workflow version", async () => {
    renderDetail()

    await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
    expect(await previewTitle("Laptop request")).toBeInTheDocument()
    expect(within(previewPane()).getByLabelText("Model")).toBeInTheDocument()
    expect(validateForm).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      requestBody: {
        workflow_id: "00000000-0000-4000-8000-000000000001",
        config: { title: "Laptop request", layout: [{ field: "model" }] },
      },
    })
    expect(
      screen.getByRole("link", { name: "Provision laptop v4" })
    ).toHaveAttribute(
      "href",
      // The builder is addressed by the workflow's short ID.
      "/workspaces/workspace-1/workflows/wf_000000001vGeH72LxVtxKh"
    )
    expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument()
  })

  it("says when the inputs come from an unpublished workflow", async () => {
    getForm.mockResolvedValue(formRead({ workflow_version: null }))

    renderDetail()

    expect(
      await screen.findByRole("link", { name: "Provision laptop" })
    ).toBeInTheDocument()
    expect(screen.getByText("(draft, not published)")).toBeInTheDocument()
  })

  it("shows not found for a form the caller cannot open", async () => {
    getForm.mockRejectedValue(apiError(404, "Form not found"))

    renderDetail()

    expect(await screen.findByText("Form not found")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "Back to forms" })).toHaveAttribute(
      "href",
      "/workspaces/workspace-1/forms"
    )
  })

  describe("saving", () => {
    it("is disabled while the buffer is clean", async () => {
      renderDetail()

      await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
      expect(saveButton()).toBeDisabled()
    })

    it("sends the YAML as JSON and takes the stored config back", async () => {
      const stored = { title: "Laptop order", layout: [{ field: "model" }] }
      updateForm.mockResolvedValue(formRead({ config: stored }))
      const { user } = renderDetail()
      await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))

      edit(
        "# Renamed in October\ntitle: Laptop order\nlayout:\n  - field: model\n"
      )
      expect(screen.getByText("Unsaved changes")).toBeInTheDocument()
      expect(saveButton()).toBeEnabled()
      await user.click(saveButton())

      await waitFor(() =>
        expect(updateForm).toHaveBeenCalledWith({
          workspaceId: "workspace-1",
          formId: "form-1",
          requestBody: { config: stored },
        })
      )
      // The server stores JSON, so the comment does not come back.
      await waitFor(() =>
        expect(editor()).toHaveValue(
          "title: Laptop order\nlayout:\n  - field: model\n"
        )
      )
      expect(saveButton()).toBeDisabled()
      expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument()
      expect(
        screen.queryByText(/This form changed elsewhere/)
      ).not.toBeInTheDocument()
    })

    it("is disabled by a YAML syntax error, which is listed with its line", async () => {
      renderDetail()
      await waitFor(() => expect(validateForm).toHaveBeenCalledTimes(1))

      edit("title: Laptop request\nlayout:\n  - field: [model\n")

      const problems = await screen.findByRole(
        "region",
        { name: "Problems" },
        { timeout: 3000 }
      )
      expect(within(problems).getByRole("img", { name: "Error" })).toBeVisible()
      expect(within(problems).getByText(/^Ln [34]$/)).toBeInTheDocument()
      expect(saveButton()).toBeDisabled()
      expect(screen.getByText("1 error blocks saving")).toBeInTheDocument()
      // Broken YAML never reaches the server.
      expect(validateForm).toHaveBeenCalledTimes(1)
    })

    it("is disabled by a server issue, which is listed on the line of its key", async () => {
      answerChecks((config) =>
        config.layout?.some(
          (entry) => "widget" in entry && entry.widget === "cards"
        )
          ? invalid(WIDGET_ISSUE, {
              severity: "warning",
              code: "unrendered_optional_input",
              message: "Optional input asset_tag is not rendered",
              loc: ["layout"],
            })
          : valid("Laptop request")
      )
      renderDetail()
      await previewTitle("Laptop request")

      edit(
        "title: Laptop request\nlayout:\n  - field: model\n    widget: cards\n"
      )

      const problems = within(
        await screen.findByRole(
          "region",
          { name: "Problems" },
          { timeout: 3000 }
        )
      )
      const rows = problems.getAllByRole("button")
      expect(rows[0]).toHaveTextContent("cards does not accept a bool input")
      expect(rows[0]).toHaveTextContent("Ln 4")
      expect(within(rows[0]).getByRole("img", { name: "Error" })).toBeVisible()
      expect(rows[1]).toHaveTextContent("Optional input asset_tag")
      expect(rows[1]).toHaveTextContent("Ln 2")
      expect(
        within(rows[1]).getByRole("img", { name: "Warning" })
      ).toBeVisible()
      expect(saveButton()).toBeDisabled()
      expect(screen.getByText("1 error blocks saving")).toBeInTheDocument()
    })

    it("is not blocked by warnings alone", async () => {
      validateForm.mockResolvedValue({
        valid: true,
        issues: [
          {
            severity: "warning",
            code: "unrendered_optional_input",
            message: "Optional input asset_tag is not rendered",
            loc: ["layout"],
          },
        ],
        preview: preview("Laptop request"),
      })
      renderDetail()
      await previewTitle("Laptop request")

      edit("title: Laptop request\n")

      expect(
        await screen.findByRole("region", { name: "Problems" })
      ).toBeInTheDocument()
      expect(saveButton()).toBeEnabled()
    })

    it("lists the problems of a rejected save and keeps the buffer", async () => {
      updateForm.mockRejectedValue(
        apiError(422, {
          code: "form_config_invalid",
          message: "Invalid form config",
          issues: [WIDGET_ISSUE],
        })
      )
      const { user } = renderDetail()
      await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
      const text = "layout:\n  - field: model\n    widget: cards\n"

      edit(text)
      await user.click(saveButton())

      const problems = await screen.findByRole("region", { name: "Problems" })
      expect(problems).toHaveTextContent("cards does not accept a bool input")
      expect(problems).toHaveTextContent("Ln 3")
      expect(editor()).toHaveValue(text)
      expect(saveButton()).toBeDisabled()
    })

    it("lists a request validation error on the key it names", async () => {
      validateForm.mockRejectedValue(
        apiError(422, [
          {
            type: "enum",
            loc: ["body", "config", "layout", 0, "notice", "tone"],
            msg: "Input should be 'info', 'success', 'warning' or 'danger'",
          },
        ])
      )
      renderDetail()
      await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))

      edit("layout:\n  - notice: Heads up\n    tone: purple\n")

      const problems = await screen.findByRole(
        "region",
        { name: "Problems" },
        { timeout: 3000 }
      )
      expect(problems).toHaveTextContent("Input should be 'info'")
      expect(problems).toHaveTextContent("Ln 3")
      expect(saveButton()).toBeDisabled()
    })
  })

  describe("preview", () => {
    it("follows valid edits after a pause", async () => {
      renderDetail()
      await previewTitle("Laptop request")

      edit("title: Hardware request\n")

      expect(await previewTitle("Hardware request")).toBeInTheDocument()
      expect(checkedConfigs()).toEqual([
        { title: "Laptop request", layout: [{ field: "model" }] },
        { title: "Hardware request" },
      ])
    })

    it("checks once for a burst of edits", async () => {
      renderDetail()
      await previewTitle("Laptop request")

      edit("title: H\n")
      edit("title: Ha\n")
      edit("title: Hardware\n")

      expect(await previewTitle("Hardware")).toBeInTheDocument()
      expect(checkedConfigs().map((config) => config.title)).toEqual([
        "Laptop request",
        "Hardware",
      ])
    })

    it("keeps the last good preview, dimmed, while the config is invalid", async () => {
      answerChecks((config) =>
        config.title === "Broken"
          ? invalid(WIDGET_ISSUE)
          : valid(config.title ?? "Untitled")
      )
      renderDetail()
      const good = await previewTitle("Laptop request")
      expect(good.closest("[data-stale]")).toHaveAttribute(
        "data-stale",
        "false"
      )

      edit("title: Broken\n")

      expect(
        await within(previewPane()).findByText(
          "Showing the last valid config",
          undefined,
          { timeout: 3000 }
        )
      ).toBeInTheDocument()
      // The server's preview of the broken config is not shown.
      expect(
        within(previewPane()).getByRole("heading", { name: "Laptop request" })
      ).toBeInTheDocument()
      expect(
        within(previewPane()).queryByRole("heading", { name: "Broken" })
      ).not.toBeInTheDocument()
      expect(
        within(previewPane())
          .getByRole("heading", { name: "Laptop request" })
          .closest("[data-stale]")
      ).toHaveAttribute("data-stale", "true")

      // A syntax error keeps it too.
      edit("title: [Broken\n")
      await screen.findByText(/^Ln 1$/, undefined, { timeout: 3000 })
      expect(
        within(previewPane()).getByRole("heading", { name: "Laptop request" })
      ).toBeInTheDocument()

      edit("title: Fixed\n")

      const fixed = await previewTitle("Fixed")
      expect(fixed.closest("[data-stale]")).toHaveAttribute(
        "data-stale",
        "false"
      )
      expect(
        within(previewPane()).queryByText("Showing the last valid config")
      ).not.toBeInTheDocument()
    })

    it("asks for fixes when there has never been a good preview", async () => {
      validateForm.mockResolvedValue(invalid(WIDGET_ISSUE))

      renderDetail()

      expect(
        await screen.findByText(
          "Fix the problems in the config to see the form."
        )
      ).toBeInTheDocument()
      expect(previewPane()).toHaveTextContent("No preview yet")
    })

    it("ignores an answer that arrives after a newer one", async () => {
      const slow = deferred<FormValidateResponse>()
      answerChecks((config) =>
        config.title === "Slow" ? slow.promise : valid(config.title ?? "")
      )
      renderDetail()
      await previewTitle("Laptop request")

      edit("title: Slow\n")
      await waitFor(() => expect(validateForm).toHaveBeenCalledTimes(2), {
        timeout: 3000,
      })
      edit("title: Fast\n")
      await previewTitle("Fast")

      await act(async () => {
        slow.resolve(valid("Slow"))
      })

      expect(
        within(previewPane()).getByRole("heading", { name: "Fast" })
      ).toBeInTheDocument()
      expect(
        within(previewPane()).queryByRole("heading", { name: "Slow" })
      ).not.toBeInTheDocument()
    })

    it("does not block saving when the check itself fails", async () => {
      renderDetail()
      await previewTitle("Laptop request")
      validateForm.mockRejectedValue(new Error("Network error"))

      edit("title: Offline edit\n")

      expect(
        await within(previewPane()).findByText(
          "Couldn't check the config",
          undefined,
          { timeout: 3000 }
        )
      ).toBeInTheDocument()
      expect(screen.queryByRole("region", { name: "Problems" })).toBeNull()
      expect(saveButton()).toBeEnabled()
    })
  })

  describe("remote changes", () => {
    const remote = formRead({
      config: { title: "Renamed by chat" },
      updated_at: "2026-10-12T10:00:00Z",
    })

    it("adopts the saved config into a clean buffer", async () => {
      const { queryClient } = renderDetail()
      await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))

      act(() => {
        queryClient.setQueryData(
          formKeys.detail("workspace-1", "form-1"),
          remote
        )
      })

      await waitFor(() =>
        expect(editor()).toHaveValue("title: Renamed by chat\n")
      )
      expect(
        screen.queryByText(/This form changed elsewhere/)
      ).not.toBeInTheDocument()
      expect(saveButton()).toBeDisabled()
    })

    it("leaves a dirty buffer alone and offers a reload", async () => {
      const { queryClient, user } = renderDetail()
      await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
      edit("title: My edit\n")

      act(() => {
        queryClient.setQueryData(
          formKeys.detail("workspace-1", "form-1"),
          remote
        )
      })

      expect(
        await screen.findByText(/This form changed elsewhere/)
      ).toBeInTheDocument()
      expect(editor()).toHaveValue("title: My edit\n")

      await user.click(screen.getByRole("button", { name: "Reload" }))

      expect(editor()).toHaveValue("title: Renamed by chat\n")
      expect(
        screen.queryByText(/This form changed elsewhere/)
      ).not.toBeInTheDocument()
      expect(screen.queryByText("Unsaved changes")).not.toBeInTheDocument()
    })
  })

  describe("without form:update", () => {
    beforeEach(() => {
      scopes = new Set(["form:read", "form:submission:read"])
    })

    it("is read-only, with the saved issues and the saved config's preview", async () => {
      getForm.mockResolvedValue(formRead({ issues: [WIDGET_ISSUE] }))
      previewForm.mockResolvedValue(preview("Laptop request"))

      renderDetail()

      await waitFor(() => expect(editor()).toHaveValue(SAVED_YAML))
      expect(editor()).toHaveAttribute("readonly")
      expect(screen.getByText("Read only")).toBeInTheDocument()
      expect(screen.queryByRole("button", { name: "Save" })).toBeNull()
      expect(screen.queryByRole("button", { name: "Insert image" })).toBeNull()
      expect(
        screen.getByRole("region", { name: "Problems" })
      ).toHaveTextContent("cards does not accept a bool input")
      expect(await previewTitle("Laptop request")).toBeInTheDocument()
      expect(within(previewPane()).getByLabelText("Model")).toBeInTheDocument()
      expect(previewForm).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        formId: "form-1",
      })
      // The live check needs form:update, so it is never asked for.
      expect(validateForm).not.toHaveBeenCalled()
    })

    it("says so when the saved preview cannot be loaded", async () => {
      previewForm.mockRejectedValue(apiError(500, "Internal error"))

      renderDetail()

      expect(
        await within(
          await screen.findByRole("region", { name: "Preview" })
        ).findByText("No preview")
      ).toBeInTheDocument()
    })
  })

  it("asks for no saved preview from a caller who can edit", async () => {
    renderDetail()

    await previewTitle("Laptop request")
    expect(previewForm).not.toHaveBeenCalled()
  })

  describe("activity tab", () => {
    const submission: FormSubmissionRead = {
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
        extras: { colour: "silver" },
      },
      submitted_by: {
        type: "user",
        id: "user-1",
        email: "maya.okafor@example.com",
        first_name: "Maya",
        last_name: "Okafor",
      },
      definition_version: 4,
      wf_exec_id: "wf_3KbAYlkEEToril859NNEOb/exec_2a",
      run_status: "COMPLETED",
      submitted_at: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(),
    }

    function submissionsPage(items: FormSubmissionRead[]) {
      return {
        items,
        next_cursor: null,
        prev_cursor: null,
        has_more: false,
        has_previous: false,
      }
    }

    beforeEach(() => {
      searchParams = new URLSearchParams("tab=activity")
      listSubmissions.mockResolvedValue(submissionsPage([submission]))
    })

    it("opens from ?tab=activity with counters, the run and its answers", async () => {
      const { user } = renderDetail()

      expect(await screen.findByText("Maya Okafor")).toBeInTheDocument()
      expect(screen.getByText("311")).toBeInTheDocument()
      expect(screen.getByText("42")).toBeInTheDocument()
      expect(screen.getByText("FRM-8F3K2A1C")).toBeInTheDocument()
      expect(screen.getByText("Completed")).toBeInTheDocument()
      expect(screen.getByRole("link", { name: "Run" })).toHaveAttribute(
        "href",
        "/workspaces/workspace-1/workflows/wf_3KbAYlkEEToril859NNEOb/executions/exec_2a"
      )
      expect(screen.queryByLabelText("Form config")).not.toBeInTheDocument()
      // Answers stay folded until asked for.
      expect(screen.queryByText("mbp14")).not.toBeInTheDocument()

      await user.click(
        screen.getByRole("button", { name: "Show answers of FRM-8F3K2A1C" })
      )

      // An answer sits under its field's label, or under the input name
      // when the form shows no field for it.
      const answerOf = (term: string) =>
        screen.getByText(term, { selector: "dt" }).nextElementSibling
      await waitFor(() => expect(answerOf("Model")).toHaveTextContent("mbp14"))
      expect(answerOf("ship_to_home")).toHaveTextContent("Yes")
      // People and files read as text, not JSON.
      expect(answerOf("manager")).toHaveTextContent(
        "Daniel Reyes (daniel.reyes@example.com)"
      )
      expect(answerOf("quote")).toHaveTextContent("quote.pdf2 KB")
      expect(answerOf("extras")).toHaveTextContent('"colour": "silver"')
    })

    it("downloads a submitted file from its answer", async () => {
      jest.mocked(formsDownloadFormFile).mockResolvedValue({
        download_url:
          "https://storage.example.com/forms/a/b/file-1?signature=x",
        file_name: "quote.pdf",
        content_type: "application/pdf",
      })
      const clicked: string[] = []
      const click = jest
        .spyOn(HTMLAnchorElement.prototype, "click")
        .mockImplementation(function (this: HTMLAnchorElement) {
          clicked.push(this.href)
        })
      const { user } = renderDetail()

      await user.click(
        await screen.findByRole("button", {
          name: "Show answers of FRM-8F3K2A1C",
        })
      )
      await user.click(
        await screen.findByRole("button", { name: "Download quote.pdf" })
      )

      await waitFor(() =>
        expect(clicked).toEqual([
          "https://storage.example.com/forms/a/b/file-1?signature=x",
        ])
      )
      expect(formsDownloadFormFile).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        formId: "form-1",
        fileId: "file-1",
      })
      click.mockRestore()
    })

    it("shows no run for a submission the server sent without one", async () => {
      listSubmissions.mockResolvedValue(
        submissionsPage([{ ...submission, wf_exec_id: null, run_status: null }])
      )

      renderDetail()

      expect(await screen.findByText("Maya Okafor")).toBeInTheDocument()
      expect(screen.queryByRole("link", { name: "Run" })).toBeNull()
      expect(screen.queryByText("Completed")).toBeNull()
    })

    it("does not ask for submissions without form:submission:read", async () => {
      scopes = new Set(["form:read", "form:update"])
      getForm.mockResolvedValue(formRead({ counts: null }))

      renderDetail()

      expect(
        await screen.findByText("No access to submissions")
      ).toBeInTheDocument()
      expect(listSubmissions).not.toHaveBeenCalled()
    })

    it("switches tabs through the URL and keeps the preview", async () => {
      searchParams = new URLSearchParams()
      const { user } = renderDetail()
      await previewTitle("Laptop request")

      await user.click(screen.getByRole("tab", { name: "Activity" }))

      expect(replace).toHaveBeenCalledWith(
        "/workspaces/workspace-1/forms/form-1?tab=activity"
      )
    })
  })
})
