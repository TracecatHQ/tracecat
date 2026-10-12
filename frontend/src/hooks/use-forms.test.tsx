import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { formsRenderForm, formsSubmitForm } from "@/client"
import { useRenderedForm, useSubmitForm } from "@/hooks/use-forms"
import { resolvedForm } from "@/lib/forms/test-fixtures"
import { focusManager, QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  formsRenderForm: jest.fn(),
  formsSearchFormUsers: jest.fn(),
  formsSubmitForm: jest.fn(),
}))

const renderForm = jest.mocked(formsRenderForm)
const submitFormRequest = jest.mocked(formsSubmitForm)
const scope = { workspaceId: "workspace-1", formId: "form-1" }

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

function createQueryClient() {
  // App defaults refetch on focus and on mount; the hook has to override them.
  return new QueryClient({
    defaultOptions: {
      queries: { refetchOnWindowFocus: true, refetchOnMount: true },
    },
  })
}

beforeEach(() => {
  jest.clearAllMocks()
  renderForm.mockResolvedValue(resolvedForm())
})

describe("useRenderedForm", () => {
  it("waits for the workspace before rendering the form", () => {
    const { result } = renderHook(
      () => useRenderedForm({ workspaceId: undefined, formId: "form-1" }),
      { wrapper: createWrapper(createQueryClient()) }
    )

    expect(result.current.formIsLoading).toBe(false)
    expect(renderForm).not.toHaveBeenCalled()
  })

  it("renders once and does not refetch on focus or remount", async () => {
    const wrapper = createWrapper(createQueryClient())
    const first = renderHook(() => useRenderedForm(scope), { wrapper })
    await waitFor(() => expect(first.result.current.form).toBeDefined())
    expect(renderForm).toHaveBeenCalledTimes(1)
    expect(renderForm).toHaveBeenCalledWith(scope)

    // Each call records a view on the server, so none of these may refetch.
    act(() => {
      focusManager.setFocused(false)
      focusManager.setFocused(true)
    })
    first.unmount()
    const second = renderHook(() => useRenderedForm(scope), { wrapper })
    await waitFor(() => expect(second.result.current.form).toBeDefined())
    expect(renderForm).toHaveBeenCalledTimes(1)

    // An explicit reload, as after `form_changed`, still works.
    await act(async () => {
      await second.result.current.refetch()
    })
    expect(renderForm).toHaveBeenCalledTimes(2)
  })

  it("does not retry a failed render", async () => {
    const error = Object.assign(new Error("Conflict"), { status: 409 })
    renderForm.mockRejectedValue(error)

    const { result } = renderHook(() => useRenderedForm(scope), {
      wrapper: createWrapper(createQueryClient()),
    })

    await waitFor(() => expect(result.current.formError).toBe(error))
    expect(renderForm).toHaveBeenCalledTimes(1)
  })
})

describe("useSubmitForm", () => {
  it("posts the answers with the rendered definition version", async () => {
    const receipt = {
      id: "submission-1",
      reference: "FRM-8F3K2A1C",
      submitted_at: "2026-10-12T10:42:00Z",
    }
    submitFormRequest.mockResolvedValue(receipt)
    const queryClient = createQueryClient()
    const invalidate = jest.spyOn(queryClient, "invalidateQueries")
    const { result } = renderHook(() => useSubmitForm(scope), {
      wrapper: createWrapper(queryClient),
    })

    const body = { values: { model: "mbp" }, definition_version: 4 }
    await act(async () => {
      await expect(result.current.submitForm(body)).resolves.toEqual(receipt)
    })

    expect(submitFormRequest).toHaveBeenCalledWith({
      ...scope,
      requestBody: body,
    })
    expect(invalidate).toHaveBeenCalledWith({
      queryKey: ["requester-submissions"],
    })
    // The rendered form must not be invalidated: that would record a view.
    expect(renderForm).not.toHaveBeenCalled()
  })
})
