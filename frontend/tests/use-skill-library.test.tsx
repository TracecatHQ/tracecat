import { act, renderHook } from "@testing-library/react"
import { skillLibraryBatchUninstallLibrarySkills } from "@/client"
import { useSkillLibrary } from "@/hooks/use-skill-library"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => ({
  ...jest.requireActual("@/client"),
  skillLibraryBatchUninstallLibrarySkills: jest.fn(),
}))
jest.mock("@/components/ui/use-toast", () => ({ toast: jest.fn() }))

describe("bulk library uninstall", () => {
  it.each([false, true])(
    "submits one batch request even when rejected=%s",
    async (rejected) => {
      const uninstall = jest.mocked(skillLibraryBatchUninstallLibrarySkills)
      uninstall.mockReset()
      const error = new Error("A selected skill is in use")
      if (rejected) {
        uninstall.mockRejectedValue(error)
      } else {
        uninstall.mockResolvedValue(undefined)
      }
      const client = new QueryClient({
        defaultOptions: { mutations: { retry: false } },
      })
      const { result } = renderHook(
        () => useSkillLibrary("workspace-1", { enabled: false }),
        {
          wrapper: ({ children }) => (
            <QueryClientProvider client={client}>
              {children}
            </QueryClientProvider>
          ),
        }
      )

      await act(async () => {
        const request = result.current.uninstallLibrarySkills([
          "a-unused",
          "b-bound",
          "c-unused",
        ])
        if (rejected) {
          await expect(request).rejects.toBe(error)
        } else {
          await request
        }
      })

      expect(uninstall).toHaveBeenCalledTimes(1)
      expect(uninstall).toHaveBeenCalledWith({
        workspaceId: "workspace-1",
        requestBody: { slugs: ["a-unused", "b-bound", "c-unused"] },
      })
    }
  )
})
