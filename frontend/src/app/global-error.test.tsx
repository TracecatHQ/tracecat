import * as Sentry from "@sentry/nextjs"
import { render, waitFor } from "@testing-library/react"
import { initBrowserSentry } from "@/lib/sentry-client"
import GlobalError from "./global-error"

jest.mock("@sentry/nextjs", () => ({
  captureException: jest.fn(),
}))

jest.mock("next/error", () => ({
  __esModule: true,
  default: () => <div data-testid="next-error" />,
}))

jest.mock("next-runtime-env", () => ({
  PublicEnvScript: () => <script data-testid="public-env-script" />,
}))

jest.mock("@/lib/sentry-client", () => ({
  initBrowserSentry: jest.fn(),
}))

describe("GlobalError", () => {
  afterEach(() => {
    jest.restoreAllMocks()
    jest.clearAllMocks()
  })

  it("initializes runtime Sentry before capturing the error", async () => {
    const error = new Error("boom")
    // Next global-error intentionally renders a document shell.
    jest.spyOn(console, "error").mockImplementation(() => undefined)

    const { getByTestId } = render(<GlobalError error={error} />)

    expect(getByTestId("public-env-script")).toBeInTheDocument()
    await waitFor(() => {
      expect(initBrowserSentry).toHaveBeenCalledTimes(1)
      expect(Sentry.captureException).toHaveBeenCalledWith(error)
    })
    expect(
      jest.mocked(initBrowserSentry).mock.invocationCallOrder[0]
    ).toBeLessThan(
      jest.mocked(Sentry.captureException).mock.invocationCallOrder[0]
    )
  })
})
