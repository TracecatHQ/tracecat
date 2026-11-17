import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import {
  type FormRead,
  formsDeleteForm,
  formsRenderForm,
  formsUpdateForm,
} from "@/client"
import {
  formKeys,
  useDeleteForm,
  useRenderedForm,
  useUpdateForm,
} from "@/hooks/use-forms"
import { resolvedForm } from "@/lib/forms/test-fixtures"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  formsDeleteForm: jest.fn(),
  formsRenderForm: jest.fn(),
  formsUpdateForm: jest.fn(),
}))

const renderForm = jest.mocked(formsRenderForm)
const SCOPE = { workspaceId: "workspace-1", formId: "form-1" }
const RENDER_KEY = formKeys.render("workspace-1", "form-1")

function setup() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  )
  return { queryClient, wrapper }
}

beforeEach(() => {
  jest.clearAllMocks()
})

describe("useRenderedForm", () => {
  it("loads the form again when the page mounts again", async () => {
    const { wrapper } = setup()
    renderForm.mockResolvedValueOnce(resolvedForm({ title: "Old title" }))
    const first = renderHook(() => useRenderedForm(SCOPE), { wrapper })
    await waitFor(() =>
      expect(first.result.current.data?.title).toBe("Old title")
    )
    first.unmount()

    // The form's config changed while the requester was away.
    renderForm.mockResolvedValueOnce(resolvedForm({ title: "New title" }))
    const second = renderHook(() => useRenderedForm(SCOPE), { wrapper })

    await waitFor(() =>
      expect(second.result.current.data?.title).toBe("New title")
    )
    expect(renderForm).toHaveBeenCalledTimes(2)
  })
})

describe("form mutations", () => {
  it("marks the rendered form stale after an update", async () => {
    const { queryClient, wrapper } = setup()
    queryClient.setQueryData(RENDER_KEY, resolvedForm())
    jest
      .mocked(formsUpdateForm)
      .mockResolvedValue({ id: "form-1" } as unknown as FormRead)
    const { result } = renderHook(() => useUpdateForm(SCOPE), { wrapper })

    await act(() => result.current.mutateAsync({ status: "online" }))

    expect(queryClient.getQueryState(RENDER_KEY)?.isInvalidated).toBe(true)
  })

  it("drops the rendered form after a delete", async () => {
    const { queryClient, wrapper } = setup()
    queryClient.setQueryData(RENDER_KEY, resolvedForm())
    jest.mocked(formsDeleteForm).mockResolvedValue(undefined)
    const { result } = renderHook(() => useDeleteForm("workspace-1"), {
      wrapper,
    })

    await act(() => result.current.mutateAsync("form-1"))

    expect(queryClient.getQueryState(RENDER_KEY)).toBeUndefined()
  })
})
