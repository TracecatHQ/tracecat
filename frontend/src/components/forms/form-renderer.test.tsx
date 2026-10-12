import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  formsRenderForm,
  formsSearchFormUsers,
  formsSubmitForm,
} from "@/client"
import {
  FormRenderer,
  type FormRendererProps,
} from "@/components/forms/form-renderer"
import { TooltipProvider } from "@/components/ui/tooltip"
import { fieldBlock, resolvedForm } from "@/lib/forms/test-fixtures"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  formsRenderForm: jest.fn(),
  formsSearchFormUsers: jest.fn(),
  formsSubmitForm: jest.fn(),
}))

const PNG_DATA_URI = `data:image/png;base64,${btoa("\x89PNG\r\n\x1a\n\0\0\0\r")}`

const form = resolvedForm({
  description: "Request a new laptop from IT.",
  success_message: "IT will follow up by email.",
  blocks: [
    { type: "notice", text: "Reviewed within 2 business days.", tone: "info" },
    fieldBlock({
      name: "justification",
      label: "Justification",
      required: true,
    }),
    { type: "heading", text: "Delivery" },
    fieldBlock({
      name: "for_someone_else",
      label: "For someone else",
      widget: "toggle",
      default: false,
    }),
    fieldBlock({
      name: "recipient_name",
      label: "Recipient name",
      required: true,
      visible_if: { field: "for_someone_else", equals: true },
    }),
  ],
})

function apiError(status: number, detail: unknown) {
  return Object.assign(new Error("Request failed"), {
    status,
    body: { detail },
  })
}

function Providers({ children }: { children: ReactNode }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  )
}

function renderForm(props: Partial<FormRendererProps> = {}) {
  const onSubmit = jest.fn().mockResolvedValue({
    id: "submission-1",
    reference: "FRM-8F3K2A1C",
    submitted_at: "2026-10-12T10:42:00Z",
    success_message: "IT will follow up by email.",
  })
  const user = userEvent.setup()
  render(
    <FormRenderer
      formId="form-1"
      form={form}
      mode="live"
      onSubmit={onSubmit}
      {...props}
    />,
    { wrapper: Providers }
  )
  return { onSubmit, user }
}

const submitButton = () => screen.getByRole("button", { name: "Submit" })

beforeEach(() => {
  jest.clearAllMocks()
})

describe("FormRenderer", () => {
  it("renders the header, static blocks and fields", () => {
    renderForm({ submitterEmail: "maya.okafor@example.com" })

    expect(
      screen.getByRole("heading", { level: 1, name: "Laptop request" })
    ).toBeInTheDocument()
    expect(
      screen.getByText("Request a new laptop from IT.")
    ).toBeInTheDocument()
    expect(screen.getByRole("note")).toHaveTextContent(
      "Reviewed within 2 business days."
    )
    expect(
      screen.getByRole("heading", { level: 2, name: "Delivery" })
    ).toBeInTheDocument()
    // Required fields are marked; optional ones are not.
    expect(screen.getByText("Justification")).toHaveTextContent(
      "Justification *"
    )
    expect(screen.getByText("For someone else")).toHaveTextContent(
      /^For someone else$/
    )
    expect(
      screen.getByText("Submitting as maya.okafor@example.com")
    ).toBeInTheDocument()
  })

  it("shows a conditional field only while its controller matches", async () => {
    const { user } = renderForm()
    expect(screen.queryByLabelText(/Recipient name/)).not.toBeInTheDocument()

    await user.click(screen.getByRole("switch", { name: "For someone else" }))
    expect(screen.getByLabelText(/Recipient name/)).toBeInTheDocument()

    await user.click(screen.getByRole("switch", { name: "For someone else" }))
    expect(screen.queryByLabelText(/Recipient name/)).not.toBeInTheDocument()
  })

  it("validates before submitting and does not call onSubmit when invalid", async () => {
    const { user, onSubmit } = renderForm()

    await user.click(submitButton())

    expect(
      await screen.findByText("This field is required")
    ).toBeInTheDocument()
    expect(screen.getByLabelText(/Justification/)).toHaveAttribute(
      "aria-invalid",
      "true"
    )
    expect(onSubmit).not.toHaveBeenCalled()
  })

  it("sends visible answers only, then shows the confirmation", async () => {
    const { user, onSubmit } = renderForm()

    // Fill the conditional field, then hide it again.
    await user.click(screen.getByRole("switch", { name: "For someone else" }))
    await user.type(screen.getByLabelText(/Recipient name/), "Daniel Reyes")
    await user.click(screen.getByRole("switch", { name: "For someone else" }))
    await user.type(screen.getByLabelText(/Justification/), "Screen cracked")
    await user.click(submitButton())

    expect(
      await screen.findByRole("heading", { name: "Request submitted" })
    ).toBeInTheDocument()
    expect(onSubmit).toHaveBeenCalledWith({
      justification: "Screen cracked",
      for_someone_else: false,
    })
    expect(screen.getByText("IT will follow up by email.")).toBeInTheDocument()
    expect(screen.getByText("FRM-8F3K2A1C")).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "My submissions" })
    ).toHaveAttribute("href", "/forms?tab=submissions")
    // Requesters never see the run behind a submission.
    expect(screen.queryByText(/running|completed|failed|status/i)).toBeNull()

    await user.click(screen.getByRole("button", { name: "Submit another" }))
    expect(screen.getByLabelText(/Justification/)).toHaveValue("")
  })

  it("shows the server's per-field errors on their fields", async () => {
    const { user, onSubmit } = renderForm()
    onSubmit.mockRejectedValueOnce(
      apiError(422, {
        code: "form_validation_error",
        message: "Some answers are not valid",
        errors: [
          {
            field: "justification",
            code: "invalid_value",
            message: "Say more about why",
          },
          {
            field: "cost_center",
            code: "invalid_value",
            message: "Input should be a valid string",
          },
        ],
      })
    )

    await user.type(screen.getByLabelText(/Justification/), "Because")
    await user.click(submitButton())

    expect(await screen.findByText("Say more about why")).toBeInTheDocument()
    expect(screen.getByLabelText(/Justification/)).toHaveAttribute(
      "aria-invalid",
      "true"
    )
    // An error for a field that is not on screen goes above the button.
    expect(
      screen.getByText("cost_center: Input should be a valid string")
    ).toBeInTheDocument()
    expect(
      screen.queryByRole("heading", { name: "Request submitted" })
    ).not.toBeInTheDocument()

    // Editing the field clears its server error.
    await user.type(screen.getByLabelText(/Justification/), " it broke")
    await waitFor(() =>
      expect(screen.queryByText("Say more about why")).not.toBeInTheDocument()
    )
  })

  it("tells the requester when the form changed underneath them", async () => {
    const { user, onSubmit } = renderForm()
    onSubmit.mockRejectedValueOnce(
      apiError(409, { code: "form_changed", message: "This form changed" })
    )

    await user.type(screen.getByLabelText(/Justification/), "Because")
    await user.click(submitButton())

    expect(
      await screen.findByText(
        /This form was updated while you were filling it in/
      )
    ).toBeInTheDocument()
    expect(screen.getByLabelText(/Justification/)).toHaveValue("Because")
  })

  it("shows other failures above the submit button", async () => {
    const { user, onSubmit } = renderForm()
    onSubmit.mockRejectedValueOnce(
      apiError(503, {
        code: "workflow_start_failed",
        message: "The request could not be started. Try again.",
      })
    )

    await user.type(screen.getByLabelText(/Justification/), "Because")
    await user.click(submitButton())

    expect(
      await screen.findByText("The request could not be started. Try again.")
    ).toBeInTheDocument()
  })

  it("keeps answers that still apply when the form is reloaded", async () => {
    const user = userEvent.setup()
    const { rerender } = render(
      <FormRenderer formId="form-1" form={form} mode="live" />,
      { wrapper: Providers }
    )
    await user.type(screen.getByLabelText(/Justification/), "Kept")

    // A new `form` prop is what a refetch after `form_changed` produces.
    rerender(
      <FormRenderer
        formId="form-1"
        mode="live"
        form={{
          ...form,
          definition_version: 5,
          blocks: [
            ...form.blocks,
            fieldBlock({ name: "notes", label: "Notes" }),
          ],
        }}
      />
    )

    expect(await screen.findByLabelText("Notes")).toBeInTheDocument()
    expect(screen.getByLabelText(/Justification/)).toHaveValue("Kept")
  })

  describe("input names with path syntax", () => {
    const dottedForm = resolvedForm({
      blocks: [
        fieldBlock({ name: "user.email", label: "Email", required: true }),
        fieldBlock({ name: "user", label: "User" }),
        fieldBlock({
          name: "items[0]",
          label: "Remote",
          widget: "toggle",
          default: false,
        }),
        fieldBlock({
          name: "items[0].note",
          label: "Remote note",
          visible_if: { field: "items[0]", equals: true },
        }),
      ],
    })

    it("keeps each answer under its own name", async () => {
      const { user, onSubmit } = renderForm({ form: dottedForm })

      await user.type(screen.getByLabelText(/Email/), "maya@example.com")
      await user.type(screen.getByLabelText("User"), "Maya")
      await user.click(screen.getByRole("switch", { name: "Remote" }))
      await user.type(screen.getByLabelText("Remote note"), "Ship it")
      // Typing in one field must not rewrite another.
      expect(screen.getByLabelText("User")).toHaveValue("Maya")
      await user.click(submitButton())

      await waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith({
          "user.email": "maya@example.com",
          user: "Maya",
          "items[0]": true,
          "items[0].note": "Ship it",
        })
      )
    })

    it("validates and shows server errors on the named field", async () => {
      const { user, onSubmit } = renderForm({ form: dottedForm })
      onSubmit.mockRejectedValueOnce(
        apiError(422, {
          code: "form_validation_error",
          message: "Some answers are not valid",
          errors: [
            {
              field: "user.email",
              code: "invalid_value",
              message: "Use your work email",
            },
          ],
        })
      )

      await user.click(submitButton())
      expect(
        await screen.findByText("This field is required")
      ).toBeInTheDocument()
      expect(screen.getByLabelText(/Email/)).toHaveAttribute(
        "aria-invalid",
        "true"
      )
      expect(onSubmit).not.toHaveBeenCalled()

      await user.type(screen.getByLabelText(/Email/), "maya@example.org")
      await user.click(submitButton())
      expect(await screen.findByText("Use your work email")).toBeInTheDocument()
      expect(screen.getByLabelText(/Email/)).toHaveAttribute(
        "aria-invalid",
        "true"
      )
    })
  })

  it("is read-only without permission to submit", () => {
    renderForm({ canSubmit: false })

    expect(
      screen.getByText("You can view this form but you can't submit it.")
    ).toBeInTheDocument()
    expect(submitButton()).toBeDisabled()
    expect(screen.getByLabelText(/Justification/)).toBeDisabled()
  })

  describe("preview mode", () => {
    it("validates, then confirms with PREVIEW and makes no network call", async () => {
      const { user, onSubmit } = renderForm({ mode: "preview" })
      expect(
        screen.getByText("Preview. Nothing is submitted.")
      ).toBeInTheDocument()

      await user.click(submitButton())
      expect(
        await screen.findByText("This field is required")
      ).toBeInTheDocument()

      await user.type(screen.getByLabelText(/Justification/), "Because")
      await user.click(submitButton())

      expect(
        await screen.findByRole("heading", { name: "Request submitted" })
      ).toBeInTheDocument()
      expect(screen.getByText("PREVIEW")).toBeInTheDocument()
      expect(
        screen.getByText("IT will follow up by email.")
      ).toBeInTheDocument()
      expect(
        screen.queryByRole("link", { name: "My submissions" })
      ).not.toBeInTheDocument()
      expect(onSubmit).not.toHaveBeenCalled()
      expect(formsSubmitForm).not.toHaveBeenCalled()
      expect(formsRenderForm).not.toHaveBeenCalled()
      expect(formsSearchFormUsers).not.toHaveBeenCalled()
    })

    it("keeps picked files local", async () => {
      const uploadFile = jest.fn()
      const user = userEvent.setup()
      render(
        <FormRenderer
          formId="form-1"
          mode="preview"
          uploadFile={uploadFile}
          form={resolvedForm({
            blocks: [
              fieldBlock({ name: "quote", label: "Quote", widget: "file" }),
            ],
          })}
        />,
        { wrapper: Providers }
      )

      await user.upload(
        screen.getByLabelText("Quote", { selector: "input" }),
        new File(["x"], "quote.pdf", { type: "application/pdf" })
      )

      expect(screen.getByText("quote.pdf")).toBeInTheDocument()
      expect(uploadFile).not.toHaveBeenCalled()
    })
  })

  describe("file fields", () => {
    const fileForm = resolvedForm({
      blocks: [
        fieldBlock({
          name: "quote",
          label: "Quote",
          widget: "file",
          required: true,
          config: { accept: [".pdf"], max_files: 1 },
        }),
      ],
    })
    const pdf = () => new File(["x"], "quote.pdf", { type: "application/pdf" })

    it("selects a file locally and shows the server's error on the field", async () => {
      const { user, onSubmit } = renderForm({ form: fileForm })
      onSubmit.mockRejectedValueOnce(
        apiError(422, {
          code: "form_validation_error",
          message: "Some answers are not valid",
          errors: [
            {
              field: "quote",
              code: "required",
              message: "This field is required",
            },
            {
              field: "quote",
              code: "invalid_file",
              message: "Upload the file again",
            },
          ],
        })
      )

      await user.upload(
        screen.getByLabelText("Quote", { selector: "input" }),
        pdf()
      )
      expect(screen.getByText("quote.pdf")).toBeInTheDocument()
      await user.click(submitButton())

      expect(
        await screen.findByText("Upload the file again")
      ).toBeInTheDocument()
      // No uploader was given, so there is no file ID to send.
      expect(onSubmit).toHaveBeenCalledWith({})

      await user.click(screen.getByRole("button", { name: "Remove quote.pdf" }))
      expect(screen.queryByText("quote.pdf")).not.toBeInTheDocument()
    })

    it("uploads through the injected function and submits the file ID", async () => {
      const uploadFile = jest.fn().mockResolvedValue({ id: "file-1" })
      const { user, onSubmit } = renderForm({ form: fileForm, uploadFile })

      await user.upload(
        screen.getByLabelText("Quote", { selector: "input" }),
        pdf()
      )
      expect(uploadFile).toHaveBeenCalledWith(
        expect.objectContaining({
          workspaceId: "workspace-1",
          formId: "form-1",
          field: "quote",
        })
      )
      await user.click(submitButton())

      await waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith({ quote: "file-1" })
      )
    })

    it("blocks submission after a failed upload", async () => {
      const uploadFile = jest.fn().mockRejectedValue(new Error("Too large"))
      const { user, onSubmit } = renderForm({ form: fileForm, uploadFile })

      await user.upload(
        screen.getByLabelText("Quote", { selector: "input" }),
        pdf()
      )
      expect(await screen.findByText("Upload failed")).toBeInTheDocument()
      await user.click(submitButton())

      expect(
        await screen.findByText("Remove the files that failed to upload")
      ).toBeInTheDocument()
      expect(onSubmit).not.toHaveBeenCalled()
    })
  })

  describe("cards", () => {
    const cardsForm = resolvedForm({
      blocks: [
        fieldBlock({
          name: "model",
          label: "Laptop model",
          widget: "cards",
          required: true,
          options: [
            { value: "mba", label: "MacBook Air", image: PNG_DATA_URI },
            {
              value: "mbp",
              label: "MacBook Pro",
              image: "https://example.com/mbp.png",
            },
            {
              value: "svg",
              label: "Other",
              image: `data:image/svg+xml;base64,${btoa("<svg/>")}`,
            },
          ],
        }),
      ],
    })

    it("renders only allowed inline images, as plain img elements", () => {
      const { container } = render(
        <FormRenderer formId="form-1" form={cardsForm} mode="preview" />,
        { wrapper: Providers }
      )

      const images = container.querySelectorAll("img")
      expect(images).toHaveLength(1)
      expect(images[0]).toHaveAttribute("src", PNG_DATA_URI)
      expect(container.innerHTML).not.toContain("example.com")
      expect(container.innerHTML).not.toContain("svg+xml")
    })

    it("selects one card as a radio group and submits its value", async () => {
      const { user, onSubmit } = renderForm({ form: cardsForm })
      const group = screen.getByRole("radiogroup", { name: /Laptop model/ })
      expect(group).toBeInTheDocument()

      await user.click(screen.getByRole("radio", { name: /MacBook Pro/ }))
      expect(screen.getByRole("radio", { name: /MacBook Pro/ })).toBeChecked()
      expect(
        screen.getByRole("radio", { name: /MacBook Air/ })
      ).not.toBeChecked()
      await user.click(submitButton())

      await waitFor(() =>
        expect(onSubmit).toHaveBeenCalledWith({ model: "mbp" })
      )
    })
  })
})
