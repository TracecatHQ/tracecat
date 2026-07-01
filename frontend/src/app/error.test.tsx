import * as Sentry from "@sentry/nextjs"
import { render, waitFor } from "@testing-library/react"
import { ApiError } from "@/client"
import type { ApiRequestOptions } from "@/client/core/ApiRequestOptions"
import type { ApiResult } from "@/client/core/ApiResult"
import RouteError from "./error"

const mockClearLastWorkspaceId = jest.fn()

jest.mock("@sentry/nextjs", () => ({
  captureException: jest.fn(),
}))

jest.mock("@/components/error", () => ({
  __esModule: true,
  default: ({ error }: { error: Error }) => (
    <div data-testid="error-page">{error.message}</div>
  ),
}))

jest.mock("@/lib/hooks", () => ({
  useWorkspaceManager: () => ({
    clearLastWorkspaceId: mockClearLastWorkspaceId,
  }),
}))

function getApiError(status: number): ApiError {
  const request: ApiRequestOptions = {
    method: "GET",
    url: "/api/test",
  }
  const response: ApiResult = {
    body: { detail: "API error" },
    ok: false,
    status,
    statusText: "Error",
    url: "/api/test",
  }
  return new ApiError(request, response, `API error ${status}`)
}

describe("RouteError", () => {
  beforeEach(() => {
    jest.spyOn(console, "info").mockImplementation(() => undefined)
  })

  afterEach(() => {
    jest.restoreAllMocks()
    jest.clearAllMocks()
  })

  it("skips expected 4xx ApiErrors", async () => {
    const error = getApiError(403)

    render(<RouteError error={error} />)

    await waitFor(() => {
      expect(mockClearLastWorkspaceId).toHaveBeenCalledTimes(1)
    })
    expect(Sentry.captureException).not.toHaveBeenCalled()
  })

  it("captures server ApiErrors", async () => {
    const error = getApiError(503)

    render(<RouteError error={error} />)

    await waitFor(() => {
      expect(Sentry.captureException).toHaveBeenCalledWith(error)
    })
  })

  it("captures unexpected errors", async () => {
    const error = new Error("boom")

    render(<RouteError error={error} />)

    await waitFor(() => {
      expect(Sentry.captureException).toHaveBeenCalledWith(error)
    })
  })
})
