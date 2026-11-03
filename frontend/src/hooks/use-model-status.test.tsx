import { act, renderHook, waitFor } from "@testing-library/react"
import type { ReactNode } from "react"
import { agentCheckWorkspaceModelStatus, type ModelStatusRead } from "@/client"
import { CancelablePromise } from "@/client/core/CancelablePromise"
import { useModelStatus } from "@/hooks/use-model-status"
import type { ModelInfo } from "@/lib/chat"
import { DefaultQueryClientProvider } from "@/providers/query"

jest.mock("@/client", () => ({
  agentCheckWorkspaceModelStatus: jest.fn(),
}))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

function wrapper({ children }: { children: ReactNode }) {
  return <DefaultQueryClientProvider>{children}</DefaultQueryClientProvider>
}

function ready(model: string): ModelStatusRead {
  return {
    state: "ready",
    provider: "openai",
    model,
    checked_at: "2026-11-03T00:00:00Z",
  }
}

const MODEL_A: ModelInfo = { name: "a", provider: "openai", catalogId: "ca" }
const MODEL_B: ModelInfo = { name: "b", provider: "openai", catalogId: "cb" }

describe("useModelStatus", () => {
  it("never shows one model's failed recheck on another model", async () => {
    const fetchStatus = jest.mocked(agentCheckWorkspaceModelStatus)
    fetchStatus.mockImplementation(
      ({ requestBody }) =>
        new CancelablePromise((resolve, reject) => {
          if (requestBody.refresh) reject(new Error("check failed"))
          else resolve(ready(requestBody.model_name ?? ""))
        })
    )

    const { result, rerender } = renderHook(
      ({ modelInfo }) => useModelStatus("ws", modelInfo),
      { wrapper, initialProps: { modelInfo: MODEL_A } }
    )
    await waitFor(() => expect(result.current.status?.model).toBe("a"))

    act(() => result.current.recheck())
    await waitFor(() => expect(result.current.error).not.toBeNull())

    rerender({ modelInfo: MODEL_B })
    await waitFor(() => expect(result.current.status?.model).toBe("b"))
    expect(result.current.error).toBeNull()
    expect(result.current.isChecking).toBe(false)
  })
})
