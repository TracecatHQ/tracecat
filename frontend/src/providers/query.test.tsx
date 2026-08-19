import { act, renderHook } from "@testing-library/react"
import type { ReactNode } from "react"
import { useMutation, useQueryClient } from "@/lib/query"
import { DefaultQueryClientProvider } from "@/providers/query"

const mockToast = jest.fn()

jest.mock("@/components/ui/use-toast", () => ({
  toast: (...args: unknown[]) => mockToast(...args),
}))

function wrapper({ children }: { children: ReactNode }) {
  return <DefaultQueryClientProvider>{children}</DefaultQueryClientProvider>
}

describe("DefaultQueryClientProvider error handling", () => {
  beforeEach(() => {
    mockToast.mockClear()
  })

  it("shows a fallback for an initial query failure", async () => {
    const { result } = renderHook(() => useQueryClient(), { wrapper })

    await act(async () => {
      await expect(
        result.current.fetchQuery({
          queryKey: ["initial-failure"],
          queryFn: async () => {
            throw new Error("Could not load workflows")
          },
          retry: false,
        })
      ).rejects.toThrow("Could not load workflows")
    })

    expect(mockToast).toHaveBeenCalledTimes(1)
    expect(mockToast).toHaveBeenCalledWith({
      description: "Could not load workflows",
      variant: "destructive",
    })
  })

  it("does not toast when a background refresh leaves cached data", async () => {
    const { result } = renderHook(() => useQueryClient(), { wrapper })
    result.current.setQueryData(["background-failure"], ["workflow-123"])

    await act(async () => {
      await expect(
        result.current.fetchQuery({
          queryKey: ["background-failure"],
          queryFn: async () => {
            throw new Error("Temporary refresh failure")
          },
          retry: false,
        })
      ).rejects.toThrow("Temporary refresh failure")
    })

    expect(mockToast).not.toHaveBeenCalled()
  })

  it("routes query permission failures through the global handler", async () => {
    const { result } = renderHook(() => useQueryClient(), { wrapper })
    const error = Object.assign(new Error("Forbidden"), {
      status: 403,
      body: { detail: "Missing workflow:read scope" },
    })

    await act(async () => {
      await expect(
        result.current.fetchQuery({
          queryKey: ["permission-failure"],
          queryFn: async () => {
            throw error
          },
          retry: false,
        })
      ).rejects.toBe(error)
    })

    expect(mockToast).toHaveBeenCalledTimes(1)
    expect(mockToast).toHaveBeenCalledWith({
      title: "Permission denied",
      description: "Missing workflow:read scope",
      variant: "destructive",
    })
  })

  it("runs facade mutations without local handlers through the fallback", async () => {
    const error = new Error("Could not delete workflow")
    const { result } = renderHook(
      () =>
        useMutation({
          mutationFn: async () => {
            throw error
          },
          retry: false,
        }),
      { wrapper }
    )

    await act(async () => {
      await expect(result.current.mutateAsync()).rejects.toBe(error)
    })

    expect(mockToast).toHaveBeenCalledTimes(1)
    expect(mockToast).toHaveBeenCalledWith({
      description: "Could not delete workflow",
      variant: "destructive",
    })
  })

  it("does not duplicate feedback owned by a local mutation handler", async () => {
    const local = jest.fn()
    const error = new Error("Workflow conflict")
    const { result } = renderHook(
      () =>
        useMutation({
          mutationFn: async () => {
            throw error
          },
          onError: local,
          retry: false,
        }),
      { wrapper }
    )

    await act(async () => {
      await expect(result.current.mutateAsync(undefined)).rejects.toBe(error)
    })

    expect(local).toHaveBeenCalledTimes(1)
    expect(mockToast).not.toHaveBeenCalled()
  })
})
