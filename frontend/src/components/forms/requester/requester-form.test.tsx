import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import type { ReactNode } from "react"
import {
  formsLocateIntakeForm,
  formsRenderForm,
  formsSubmitForm,
} from "@/client"
import { RequesterForm } from "@/components/forms/requester/requester-form"
import { TooltipProvider } from "@/components/ui/tooltip"
import { fieldBlock, resolvedForm } from "@/lib/forms/test-fixtures"
import { useUserScopes } from "@/lib/hooks"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  formsLocateIntakeForm: jest.fn(),
  formsRenderForm: jest.fn(),
  formsSearchFormUsers: jest.fn(),
  formsSubmitForm: jest.fn(),
  formsListIntakeForms: jest.fn(),
  formsListIntakeSubmissions: jest.fn(),
}))

jest.mock("@/hooks/use-auth", () => ({
  useAuth: () => ({
    user: {
      email: "maya.okafor@example.com",
      firstName: "Maya",
      getDisplayName: () => "Maya Okafor",
    },
    userIsLoading: false,
  }),
  useAuthActions: () => ({ logout: jest.fn() }),
}))

jest.mock("@/lib/hooks", () => ({
  useUserScopes: jest.fn(),
}))

const locate = jest.mocked(formsLocateIntakeForm)
const renderFormRequest = jest.mocked(formsRenderForm)
const submit = jest.mocked(formsSubmitForm)
const mockUseUserScopes = jest.mocked(useUserScopes)

const form = resolvedForm({
  blocks: [fieldBlock({ name: "reason", label: "Reason", required: true })],
})

function apiError(status: number, detail: unknown) {
  return Object.assign(new Error("Request failed"), {
    status,
    body: { detail },
  })
}

function mockScopes(scopes: string[]) {
  mockUseUserScopes.mockReturnValue({
    userScopes: { scopes },
    isLoading: false,
    error: null,
  } as ReturnType<typeof useUserScopes>)
}

function renderPage() {
  const queryClient = new QueryClient()
  const user = userEvent.setup()
  render(<RequesterForm formId="form-1" />, {
    wrapper: ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>{children}</TooltipProvider>
      </QueryClientProvider>
    ),
  })
  return { user }
}

async function fillAndSubmit(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText(/Reason/), "Screen cracked")
  await user.click(screen.getByRole("button", { name: "Submit" }))
}

beforeEach(() => {
  jest.clearAllMocks()
  locate.mockResolvedValue({
    form_id: "form-1",
    workspace_id: "workspace-1",
    workspace_name: "Acme Security",
  })
  renderFormRequest.mockResolvedValue(form)
  mockScopes(["form:read", "form:submit"])
})

describe("RequesterForm", () => {
  it("locates the form, then renders it in its workspace", async () => {
    let resolveLocation: (
      value: Awaited<ReturnType<typeof formsLocateIntakeForm>>
    ) => void = () => {}
    locate.mockReturnValue(
      new Promise((resolve) => {
        resolveLocation = resolve
      }) as ReturnType<typeof formsLocateIntakeForm>
    )

    renderPage()
    await waitFor(() =>
      expect(locate).toHaveBeenCalledWith({ formId: "form-1" })
    )
    expect(renderFormRequest).not.toHaveBeenCalled()

    resolveLocation({
      form_id: "form-1",
      workspace_id: "workspace-1",
      workspace_name: "Acme Security",
    })

    expect(
      await screen.findByRole("heading", { name: "Laptop request" })
    ).toBeInTheDocument()
    expect(renderFormRequest).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      formId: "form-1",
    })
    expect(mockUseUserScopes).toHaveBeenLastCalledWith("workspace-1", {
      enabled: true,
    })
    expect(screen.getByText("Acme Security")).toBeInTheDocument()
    expect(
      screen.getByText("Submitting as maya.okafor@example.com")
    ).toBeInTheDocument()
  })

  it.each([403, 404])(
    "shows not found with a way to browse on %d",
    async (status) => {
      locate.mockRejectedValue(apiError(status, "Form not found"))

      renderPage()

      expect(await screen.findByText("Form not found")).toBeInTheDocument()
      expect(
        screen.getByRole("link", { name: "Browse forms" })
      ).toHaveAttribute("href", "/forms")
      expect(renderFormRequest).not.toHaveBeenCalled()
    }
  )

  it("shows the offline state when the form is not accepting requests", async () => {
    renderFormRequest.mockRejectedValue(
      apiError(409, { code: "form_offline", message: "This form is offline" })
    )

    renderPage()

    expect(
      await screen.findByText("This form isn't accepting requests")
    ).toBeInTheDocument()
  })

  it("offers a retry for other failures", async () => {
    renderFormRequest.mockRejectedValueOnce(apiError(500, "Internal error"))
    const { user } = renderPage()

    await user.click(await screen.findByRole("button", { name: "Try again" }))

    expect(
      await screen.findByRole("heading", { name: "Laptop request" })
    ).toBeInTheDocument()
    expect(renderFormRequest).toHaveBeenCalledTimes(2)
  })

  it("is read-only without form:submit", async () => {
    mockScopes(["form:read"])

    renderPage()

    expect(
      await screen.findByText("You can view this form but you can't submit it.")
    ).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Submit" })).toBeDisabled()
  })

  it("submits with the rendered definition version and shows the confirmation", async () => {
    submit.mockResolvedValue({
      id: "submission-1",
      reference: "FRM-8F3K2A1C",
      submitted_at: "2026-10-12T10:42:00Z",
      success_message: "IT will follow up by email.",
    })
    const { user } = renderPage()

    await fillAndSubmit(user)

    expect(
      await screen.findByRole("heading", { name: "Request submitted" })
    ).toBeInTheDocument()
    expect(submit).toHaveBeenCalledWith({
      workspaceId: "workspace-1",
      formId: "form-1",
      requestBody: {
        values: { reason: "Screen cracked" },
        definition_version: 4,
      },
    })
    expect(screen.getByText("FRM-8F3K2A1C")).toBeInTheDocument()
    expect(
      screen.getByRole("link", { name: "My submissions" })
    ).toBeInTheDocument()
    // Submitting must not render the form again: that would record a view.
    expect(renderFormRequest).toHaveBeenCalledTimes(1)
  })

  it("reloads the form and says it was updated on form_changed", async () => {
    submit.mockRejectedValueOnce(
      apiError(409, { code: "form_changed", message: "This form changed" })
    )
    const { user } = renderPage()
    await screen.findByLabelText(/Reason/)
    renderFormRequest.mockResolvedValue({
      ...form,
      definition_version: 5,
      blocks: [...form.blocks, fieldBlock({ name: "notes", label: "Notes" })],
    })

    await fillAndSubmit(user)

    expect(
      await screen.findByText(
        /This form was updated while you were filling it in/
      )
    ).toBeInTheDocument()
    expect(renderFormRequest).toHaveBeenCalledTimes(2)
    expect(await screen.findByLabelText("Notes")).toBeInTheDocument()
    expect(screen.getByLabelText(/Reason/)).toHaveValue("Screen cracked")

    // The next attempt carries the new version.
    submit.mockResolvedValue({
      id: "submission-2",
      reference: "FRM-2M7QX9D4",
      submitted_at: "2026-10-12T10:43:00Z",
    })
    await user.click(screen.getByRole("button", { name: "Submit" }))
    await waitFor(() =>
      expect(submit).toHaveBeenLastCalledWith(
        expect.objectContaining({
          requestBody: {
            values: { reason: "Screen cracked" },
            definition_version: 5,
          },
        })
      )
    )
  })

  it("switches to the offline state when a submission finds the form offline", async () => {
    submit.mockRejectedValue(
      apiError(409, { code: "form_offline", message: "This form is offline" })
    )
    const { user } = renderPage()

    await fillAndSubmit(user)

    expect(
      await screen.findByText("This form isn't accepting requests")
    ).toBeInTheDocument()
    expect(screen.queryByLabelText(/Reason/)).not.toBeInTheDocument()
  })
})
