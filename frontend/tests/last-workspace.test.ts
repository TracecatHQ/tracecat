/**
 * @jest-environment jsdom
 */

import Cookies from "js-cookie"
import {
  clearLastWorkspaceIdForUser,
  getLastWorkspaceIdForUser,
  setLastWorkspaceIdForUser,
} from "@/lib/last-workspace"

describe("last workspace persistence", () => {
  beforeEach(() => {
    Cookies.remove("__tracecat:workspaces:last-viewed")
    Cookies.remove("__tracecat:workspaces:last-viewed:user-a")
    Cookies.remove("__tracecat:workspaces:last-viewed:user-b")
  })

  it("stores the last workspace separately for each user", () => {
    setLastWorkspaceIdForUser("user-a", "workspace-a")
    setLastWorkspaceIdForUser("user-b", "workspace-b")

    expect(getLastWorkspaceIdForUser("user-a")).toBe("workspace-a")
    expect(getLastWorkspaceIdForUser("user-b")).toBe("workspace-b")
  })

  it("keeps anonymous storage on the legacy shared cookie", () => {
    setLastWorkspaceIdForUser(undefined, "workspace-anonymous")
    setLastWorkspaceIdForUser("user-a", "workspace-a")

    expect(getLastWorkspaceIdForUser()).toBe("workspace-anonymous")
    expect(getLastWorkspaceIdForUser("user-a")).toBe("workspace-a")
  })

  it("clears only the scoped user's last workspace", () => {
    setLastWorkspaceIdForUser("user-a", "workspace-a")
    setLastWorkspaceIdForUser("user-b", "workspace-b")

    clearLastWorkspaceIdForUser("user-a")

    expect(getLastWorkspaceIdForUser("user-a")).toBeUndefined()
    expect(getLastWorkspaceIdForUser("user-b")).toBe("workspace-b")
  })
})
