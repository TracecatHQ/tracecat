import {
  navigateToDocument,
  reloadCurrentDocument,
} from "@/lib/auth-navigation"

describe("authentication document navigation", () => {
  it("reloads the current URL, including auth return parameters", () => {
    const reload = jest.fn()
    const location = {
      href: "https://tracecat.example/sign-in?returnUrl=%2Fworkspaces",
      reload,
    }

    reloadCurrentDocument(location)

    expect(reload).toHaveBeenCalledTimes(1)
  })

  it("navigates to the requested logout destination", () => {
    const assign = jest.fn()

    navigateToDocument("/sign-in", { assign })

    expect(assign).toHaveBeenCalledWith("/sign-in")
  })
})
