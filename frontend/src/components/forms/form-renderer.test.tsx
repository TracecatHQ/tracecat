import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import { type FormUserRead, formsSearchFormUsers } from "@/client"
import {
  FormRenderer,
  type FormRendererProps,
} from "@/components/forms/form-renderer"
import { TooltipProvider } from "@/components/ui/tooltip"
import { fieldBlock, resolvedForm } from "@/lib/forms/test-fixtures"
import type { FormBlock } from "@/lib/forms/values"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({ formsSearchFormUsers: jest.fn() }))

const searchUsers = jest.mocked(formsSearchFormUsers)

const RECEIPT = {
  id: "submission-1",
  reference: "FRM-8F3K2A1C",
  submitted_at: "2026-10-12T10:42:00Z",
  success_message: "IT will follow up by email.",
}

const form = resolvedForm({
  description: "Request a new laptop from IT.",
  success_message: "IT will follow up by email.",
  blocks: [
    { type: "notice", text: "Reviewed within 2 business days.", tone: "info" },
    fieldBlock({ name: "why", label: "Justification", required: true }),
    { type: "heading", text: "Delivery" },
    fieldBlock({
      name: "for_someone_else",
      label: "For someone else",
      widget: "toggle",
      default: false,
    }),
    fieldBlock({
      name: "recipient",
      label: "Recipient name",
      required: true,
      visible_if: { field: "for_someone_else", equals: true },
    }),
  ],
})

const formOf = (...blocks: FormBlock[]) => resolvedForm({ blocks })

function apiError(status: number, detail: unknown) {
  return Object.assign(new Error("Request failed"), {
    status,
    body: { detail },
  })
}

function validationError(...errors: [field: string, message: string][]) {
  return apiError(422, {
    code: "form_validation_error",
    message: "Some answers are not valid",
    errors: errors.map(([field, message]) => ({
      field,
      code: "invalid_value",
      message,
    })),
  })
}

function renderForm(props: Partial<FormRendererProps> = {}) {
  const onSubmit = jest.fn().mockResolvedValue(RECEIPT)
  const user = userEvent.setup()
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const view = render(
    <FormRenderer
      workspaceId="workspace-1"
      formId="form-1"
      form={form}
      mode="live"
      onSubmit={onSubmit}
      {...props}
    />,
    {
      wrapper: ({ children }: { children: ReactNode }) => (
        <QueryClientProvider client={queryClient}>
          <TooltipProvider>{children}</TooltipProvider>
        </QueryClientProvider>
      ),
    }
  )
  // Shows or hides the conditional "Recipient name" field.
  const toggleRecipient = () =>
    user.click(screen.getByRole("switch", { name: "For someone else" }))
  return { ...view, onSubmit, user, toggleRecipient }
}

const submitButton = () => screen.getByRole("button", { name: "Submit" })
const why = () => screen.getByLabelText(/Justification/)
const recipient = () => screen.queryByLabelText(/Recipient name/)
const submitted = () =>
  screen.findByRole("heading", { name: "Request submitted" })

beforeEach(() => {
  jest.clearAllMocks()
})

describe("FormRenderer", () => {
  it("renders the header, static blocks and fields", () => {
    renderForm({ submitterEmail: "maya.okafor@example.com" })

    expect(
      screen.getByRole("heading", { level: 1, name: "Laptop request" })
    ).toBeVisible()
    expect(screen.getByText("Request a new laptop from IT.")).toBeVisible()
    expect(screen.getByRole("note")).toHaveTextContent(
      "Reviewed within 2 business days."
    )
    expect(screen.getByRole("heading", { level: 2 })).toHaveTextContent(
      "Delivery"
    )
    // Required fields are marked; optional ones are not.
    expect(screen.getByText("Justification")).toHaveTextContent(
      "Justification *"
    )
    expect(screen.getByText("For someone else")).toHaveTextContent(
      /^For someone else$/
    )
    expect(
      screen.getByText("Submitting as maya.okafor@example.com")
    ).toBeVisible()
  })

  it("validates first, focuses the first invalid field and clears an error on change", async () => {
    const { user, onSubmit, toggleRecipient } = renderForm()
    await toggleRecipient()

    await user.click(submitButton())

    expect(await screen.findAllByText("This field is required")).toHaveLength(2)
    expect(why()).toHaveAttribute("aria-invalid", "true")
    expect(why()).toHaveFocus()
    expect(onSubmit).not.toHaveBeenCalled()

    await user.type(why(), "x")
    expect(why()).toHaveAttribute("aria-invalid", "false")
    expect(screen.getAllByText("This field is required")).toHaveLength(1)
  })

  it("shows a conditional field live and sends visible answers only", async () => {
    const { user, onSubmit, toggleRecipient } = renderForm()
    expect(recipient()).not.toBeInTheDocument()

    // Fill the conditional field, then hide it again.
    await toggleRecipient()
    await user.type(recipient() as HTMLElement, "Daniel Reyes")
    await toggleRecipient()
    expect(recipient()).not.toBeInTheDocument()
    await user.type(why(), "Screen cracked")
    await user.click(submitButton())

    expect(await submitted()).toBeVisible()
    expect(onSubmit).toHaveBeenCalledWith({
      why: "Screen cracked",
      for_someone_else: false,
    })
    expect(screen.getByText("IT will follow up by email.")).toBeVisible()
    expect(screen.getByText("FRM-8F3K2A1C")).toBeVisible()
    expect(
      screen.getByRole("link", { name: "My submissions" })
    ).toHaveAttribute("href", "/forms?tab=submissions")
    // Requesters never see the run behind a submission.
    expect(screen.queryByText(/running|completed|failed|status/i)).toBeNull()

    await user.click(screen.getByRole("button", { name: "Submit another" }))
    expect(why()).toHaveValue("")
  })

  it("puts the server's errors on their fields and the rest above the button", async () => {
    const { user, onSubmit } = renderForm()
    onSubmit.mockRejectedValueOnce(
      validationError(
        ["why", "Too short"],
        // Later errors come from more specific checks, so the last one shows.
        ["why", "Say more about why"],
        ["cost_center", "Input should be a valid string"]
      )
    )
    await user.type(why(), "Because")

    await user.click(submitButton())

    expect(await screen.findByText("Say more about why")).toBeVisible()
    expect(screen.queryByText("Too short")).not.toBeInTheDocument()
    expect(why()).toHaveAttribute("aria-invalid", "true")
    expect(why()).toHaveFocus()
    expect(
      screen.getByText("cost_center: Input should be a valid string")
    ).toBeVisible()

    await user.type(why(), " it broke")
    expect(screen.queryByText("Say more about why")).not.toBeInTheDocument()
  })

  it.each([
    [
      "the form changed",
      apiError(409, { code: "form_changed", message: "This form changed" }),
      /This form was updated while you were filling it in/,
    ],
    [
      "any other failure",
      apiError(503, {
        code: "workflow_start_failed",
        message: "The request could not be started. Try again.",
      }),
      "The request could not be started. Try again.",
    ],
    [
      "a failure without a message",
      new Error("Network Error"),
      "Something went wrong. Try again.",
    ],
  ])("says so above the button when %s", async (_case, error, message) => {
    const { user, onSubmit } = renderForm()
    onSubmit.mockRejectedValueOnce(error)
    await user.type(why(), "Because")

    await user.click(submitButton())

    expect(await screen.findByText(message)).toBeVisible()
    expect(why()).toHaveValue("Because")
  })

  it("keeps answers that still apply when the form is reloaded", async () => {
    const { user, rerender } = renderForm()
    await user.type(why(), "Kept")

    // A new `form` prop is what a refetch after `form_changed` produces.
    const notes = fieldBlock({ name: "notes", label: "Notes" })
    rerender(
      <FormRenderer
        workspaceId="workspace-1"
        formId="form-1"
        mode="live"
        form={{ ...form, blocks: [...form.blocks, notes] }}
      />
    )

    expect(await screen.findByLabelText("Notes")).toBeVisible()
    expect(why()).toHaveValue("Kept")
  })

  it("is read-only without permission to submit", () => {
    renderForm({ canSubmit: false })

    expect(
      screen.getByText("You can view this form but you can't submit it.")
    ).toBeVisible()
    expect(submitButton()).toBeDisabled()
    expect(why()).toBeDisabled()
  })

  it("submits once when the button is pressed twice", async () => {
    const { user, onSubmit } = renderForm()
    let finish: (receipt: unknown) => void = () => {}
    onSubmit.mockReturnValueOnce(
      new Promise((resolve) => {
        finish = resolve
      })
    )
    await user.type(why(), "Screen cracked")

    await user.click(submitButton())
    const pending = screen.getByRole("button", { name: "Submitting..." })
    expect(pending).toBeDisabled()
    await user.click(pending)
    await user.keyboard("{Enter}")

    expect(onSubmit).toHaveBeenCalledTimes(1)
    finish(RECEIPT)
    expect(await submitted()).toBeVisible()
  })

  it("marks required controls and describes them by text that exists", async () => {
    const { user } = renderForm({
      form: formOf(
        fieldBlock({
          name: "cost center",
          label: "Cost center",
          help: "Ask your manager.",
          required: true,
        }),
        fieldBlock({ name: "notes", label: "Notes" }),
        fieldBlock({
          name: "teams",
          label: "Teams",
          widget: "multi_select",
          multiple: true,
          required: true,
          options: [{ value: "it", label: "IT" }],
        })
      ),
    })
    const described = (element: HTMLElement) =>
      (element.getAttribute("aria-describedby") ?? "")
        .split(" ")
        .filter(Boolean)
        .map((id) => document.getElementById(id)?.textContent)
    const costCenter = screen.getByLabelText(/Cost center/)
    const notes = screen.getByLabelText(/Notes/)
    const teams = screen.getByLabelText(/Teams/)

    expect(costCenter).toHaveAttribute("aria-required", "true")
    expect(notes).toHaveAttribute("aria-required", "false")
    expect(teams).toHaveAttribute("aria-required", "true")
    // A name with a space still yields ids that resolve.
    expect(described(costCenter)).toEqual(["Ask your manager."])
    expect(notes).not.toHaveAttribute("aria-describedby")
    expect(teams).not.toHaveAttribute("aria-describedby")

    await user.click(submitButton())

    await screen.findAllByText("This field is required")
    expect(described(costCenter)).toEqual([
      "Ask your manager.",
      "This field is required",
    ])
    expect(teams).toHaveAttribute("aria-invalid", "true")
    expect(described(teams)).toEqual(["This field is required"])
  })

  it("confirms a preview with PREVIEW and calls nothing", async () => {
    const { user, onSubmit } = renderForm({ mode: "preview" })
    expect(screen.getByText("Preview. Nothing is submitted.")).toBeVisible()

    await user.type(why(), "Because")
    await user.click(submitButton())

    expect(await submitted()).toBeVisible()
    expect(screen.getByText("PREVIEW")).toBeVisible()
    expect(screen.getByText("IT will follow up by email.")).toBeVisible()
    expect(screen.queryByRole("link", { name: "My submissions" })).toBeNull()
    expect(onSubmit).not.toHaveBeenCalled()
    expect(searchUsers).not.toHaveBeenCalled()
  })

  describe("a form edited after it was submitted", () => {
    async function submitThenEdit(mode: FormRendererProps["mode"]) {
      const view = renderForm({ mode })
      await view.user.type(why(), "Because")
      await view.user.click(submitButton())
      expect(await submitted()).toBeVisible()
      // In the builder every edit is a new `form`; live, it is a refetch.
      const notes = fieldBlock({ name: "notes", label: "Notes" })
      view.rerender(
        <FormRenderer
          workspaceId="workspace-1"
          formId="form-1"
          mode={mode}
          form={{ ...form, blocks: [...form.blocks, notes] }}
        />
      )
    }
    const confirmation = () =>
      screen.queryByRole("heading", { name: "Request submitted" })

    it("shows the edited form in preview", async () => {
      await submitThenEdit("preview")

      expect(confirmation()).not.toBeInTheDocument()
      expect(screen.getByLabelText("Notes")).toBeVisible()
    })

    it("keeps a requester's confirmation live", async () => {
      await submitThenEdit("live")

      expect(confirmation()).toBeVisible()
    })
  })

  it.each([
    ["date", "2026-10-22", "Needed by Oct 22, 2026"],
    ["date", null, "Needed by Pick a date"],
    [
      "datetime",
      new Date(2026, 9, 22, 9, 30).toISOString(),
      "Needed by Oct 22, 2026",
    ],
  ] as const)(
    "names a %s button by its label and what it shows",
    (widget, value, name) => {
      renderForm({
        form: formOf(
          fieldBlock({ name: "by", label: "Needed by", widget, default: value })
        ),
      })

      expect(screen.getByRole("button", { name })).toBeVisible()
    }
  )

  it("never shows the unfiltered people list under a typed query", async () => {
    const members: FormUserRead[] = [
      { user_id: "user-avery", email: "avery.admin@example.com" },
      { user_id: "user-eli", email: "eli.editor@example.com" },
    ]
    searchUsers.mockImplementation((({ search }: { search?: string }) =>
      Promise.resolve({
        items: members.filter((member) => member.email.includes(search ?? "")),
        has_more: false,
        next_cursor: null,
      })) as unknown as typeof formsSearchFormUsers)
    const { user } = renderForm({
      form: formOf(fieldBlock({ name: "manager", widget: "user" })),
    })
    const eli = () => screen.queryByText("eli.editor@example.com")

    await user.click(screen.getByRole("combobox"))
    expect(await screen.findByText("eli.editor@example.com")).toBeVisible()
    await user.type(
      screen.getByPlaceholderText("Search by name or email"),
      "av"
    )

    // The debounced request has not gone out yet: the old list must be gone.
    expect(eli()).not.toBeInTheDocument()
    expect(screen.getByText("Searching...")).toBeVisible()
    expect(await screen.findByText("avery.admin@example.com")).toBeVisible()
    expect(eli()).not.toBeInTheDocument()
  })

  it("says so when the people search fails", async () => {
    searchUsers.mockRejectedValue(new Error("Network Error"))
    const { user } = renderForm({
      form: formOf(fieldBlock({ name: "manager", widget: "user" })),
    })

    await user.click(screen.getByRole("combobox"))

    expect(
      await screen.findByText("Couldn't search people. Try again.")
    ).toBeVisible()
    expect(screen.queryByText("No people found.")).not.toBeInTheDocument()
  })

  describe("file fields", () => {
    const filesForm = (config = {}) =>
      formOf(
        fieldBlock({
          name: "photos",
          label: "Photos",
          widget: "file",
          multiple: true,
          config: { max_files: 2, ...config },
        })
      )
    const pdf = (name: string, body = "x") =>
      new File([body], name, { type: "application/pdf" })

    function renderFiles(props: Partial<FormRendererProps>) {
      const view = renderForm({ form: filesForm(), ...props })
      const input = () => screen.getByLabelText("Photos", { selector: "input" })
      const pick = (...files: File[]) => view.user.upload(input(), files)
      return { ...view, pick }
    }

    it("keeps picked files local in preview and removes them", async () => {
      const uploadFile = jest.fn()
      const { user, pick } = renderFiles({ mode: "preview", uploadFile })

      await pick(pdf("a.pdf"))

      expect(screen.getByText("a.pdf")).toBeVisible()
      expect(uploadFile).not.toHaveBeenCalled()
      await user.click(screen.getByRole("button", { name: "Remove a.pdf" }))
      expect(screen.queryByText("a.pdf")).not.toBeInTheDocument()
    })

    it("uploads up to max_files through the injected function and submits their IDs", async () => {
      const uploadFile = jest
        .fn()
        .mockResolvedValueOnce({ id: "file-1" })
        .mockResolvedValueOnce({ id: "file-2" })
      const { user, onSubmit, pick } = renderFiles({ uploadFile })

      await pick(pdf("a.pdf"), pdf("b.pdf"), pdf("c.pdf"))

      expect(uploadFile).toHaveBeenCalledTimes(2)
      expect(uploadFile).toHaveBeenCalledWith(
        expect.objectContaining({
          workspaceId: "workspace-1",
          formId: "form-1",
          field: "photos",
        })
      )
      expect(screen.queryByText("c.pdf")).not.toBeInTheDocument()
      expect(screen.getByText("Choose files")).toBeDisabled()
      await user.click(submitButton())
      await waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith({ photos: ["file-1", "file-2"] })
      )
    })

    it("shows why a file failed, never uploads one over the size limit and blocks submission", async () => {
      const uploadFile = jest.fn().mockRejectedValue(new Error("Too large"))
      const { user, onSubmit, pick } = renderFiles({
        form: filesForm({ max_file_size: 3 }),
        uploadFile,
      })

      await pick(pdf("big.pdf", "xxxx"), pdf("ok.pdf"))

      expect(screen.getByText("Files can be at most 3 Bytes")).toBeVisible()
      expect(await screen.findByText("Too large")).toBeVisible()
      expect(uploadFile).toHaveBeenCalledTimes(1)
      await user.click(submitButton())
      expect(
        await screen.findByText("Remove the files that failed to upload")
      ).toBeVisible()
      expect(onSubmit).not.toHaveBeenCalled()
    })

    it("keeps files added after the field was hidden and shown during an upload", async () => {
      let finishA: (file: { id: string }) => void = () => {}
      const uploadFile = jest
        .fn()
        .mockReturnValueOnce(
          new Promise((resolve) => {
            finishA = resolve
          })
        )
        .mockResolvedValueOnce({ id: "file-b" })
      const { user, onSubmit, pick } = renderFiles({
        form: formOf(
          fieldBlock({
            name: "attach",
            label: "Attach photos",
            widget: "toggle",
            default: true,
          }),
          fieldBlock({
            name: "photos",
            label: "Photos",
            widget: "file",
            multiple: true,
            visible_if: { field: "attach", equals: true },
          })
        ),
        uploadFile,
      })
      const toggleAttach = () =>
        user.click(screen.getByRole("switch", { name: "Attach photos" }))

      await pick(pdf("a.pdf"))
      // Hiding and showing the field mounts a new widget while a.pdf uploads.
      await toggleAttach()
      await toggleAttach()
      await pick(pdf("b.pdf"))
      finishA({ id: "file-a" })

      await waitFor(() =>
        expect(screen.queryByText("Uploading...")).not.toBeInTheDocument()
      )
      expect(screen.getByText("a.pdf")).toBeVisible()
      expect(screen.getByText("b.pdf")).toBeVisible()
      await user.click(submitButton())
      await waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith({
          attach: true,
          photos: ["file-a", "file-b"],
        })
      )
    })
  })

  describe("cards", () => {
    const PNG = `data:image/png;base64,${btoa("\x89PNG\r\n\x1a\n\0\0\0\r")}`
    const SVG = `data:image/svg+xml;base64,${btoa("<svg/>")}`
    const cardsForm = (multiple = false, required = !multiple) =>
      formOf(
        fieldBlock({
          name: "model",
          label: "Laptop model",
          widget: "cards",
          required,
          multiple,
          options: [
            { value: "mba", label: "MacBook Air", image: PNG },
            { value: "mbp", label: "MacBook Pro", image: "https://x.test/a" },
            { value: "svg", label: "Other", image: SVG },
          ],
        })
      )
    const card = (name: string) =>
      screen.getByRole("radio", { name: new RegExp(name) })

    it("renders only allowed inline images, as plain img elements", () => {
      const { container } = renderForm({ form: cardsForm() })

      const images = container.querySelectorAll("img")
      expect(images).toHaveLength(1)
      expect(images[0]).toHaveAttribute("src", PNG)
      expect(container.innerHTML).not.toContain("x.test")
      expect(container.innerHTML).not.toContain("svg+xml")
    })

    it("is a radio group with one tab stop whose arrow keys move and select", async () => {
      const { user, onSubmit } = renderForm({ form: cardsForm() })
      expect(
        screen.getByRole("radiogroup", { name: /Laptop model/ })
      ).toHaveAttribute("aria-required", "true")

      // An invalid group focuses its first card, its only tab stop for now.
      await user.click(submitButton())
      await screen.findByText("This field is required")
      expect(card("MacBook Air")).toHaveFocus()
      await user.tab()
      expect(submitButton()).toHaveFocus()

      await user.tab({ shift: true })
      await user.keyboard("{ArrowRight}")
      expect(card("MacBook Pro")).toHaveFocus()
      expect(card("MacBook Pro")).toBeChecked()
      expect(card("MacBook Air")).not.toBeChecked()
      await user.keyboard("{ArrowDown}")
      expect(card("Other")).toBeChecked()
      // Wraps at both ends.
      await user.keyboard("{ArrowRight}")
      expect(card("MacBook Air")).toBeChecked()
      await user.keyboard("{ArrowLeft}")
      expect(card("Other")).toHaveFocus()

      // The chosen card is now the tab stop.
      await user.tab()
      await user.tab({ shift: true })
      expect(card("Other")).toHaveFocus()

      await user.click(card("MacBook Pro"))
      await user.click(submitButton())
      await waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith({ model: "mbp" })
      )
    })

    it("is a group of checkboxes for a list field", async () => {
      const { user, onSubmit } = renderForm({ form: cardsForm(true) })
      const box = (name: string) =>
        screen.getByRole("checkbox", { name: new RegExp(name) })

      // Every card is a tab stop, Space toggles it and arrows do nothing.
      await user.tab()
      expect(box("MacBook Air")).toHaveFocus()
      await user.tab()
      await user.keyboard(" ")
      expect(box("MacBook Pro")).toBeChecked()
      await user.keyboard("{ArrowLeft}")
      expect(box("MacBook Pro")).toHaveFocus()
      expect(box("MacBook Air")).not.toBeChecked()

      await user.click(box("MacBook Air"))
      await user.click(submitButton())
      // The list keeps option order, whatever the click order.
      await waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith({ model: ["mba", "mbp"] })
      )
    })

    it("describes a required list field as required", () => {
      renderForm({ form: cardsForm(true, true) })

      const group = screen.getByRole("group", { name: /Laptop model/ })
      // `aria-required` is not allowed on a group.
      expect(group).not.toHaveAttribute("aria-required")
      expect(group).toHaveAccessibleDescription("Required")
    })

    it("does not describe an optional list field as required", () => {
      renderForm({ form: cardsForm(true) })

      expect(
        screen.getByRole("group", { name: /Laptop model/ })
      ).not.toHaveAccessibleDescription(/Required/)
    })
  })
})
