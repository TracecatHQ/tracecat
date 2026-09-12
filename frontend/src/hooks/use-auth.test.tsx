import { act, renderHook, waitFor } from "@testing-library/react"
import Cookies from "js-cookie"
import type { ReactNode } from "react"
import {
  type AuthAuthDatabaseLoginData,
  authAuthDatabaseLogin,
  authAuthDatabaseLogout,
  usersUsersCurrentUser,
} from "@/client"
import { useAuth, useAuthActions } from "@/hooks/use-auth"
import { User } from "@/lib/auth"
import {
  navigateToDocument,
  reloadCurrentDocument,
} from "@/lib/auth-navigation"
import { QueryClient, QueryClientProvider } from "@/lib/query"

jest.mock("@/client", () => {
  const actual = jest.requireActual("@/client")
  return {
    ...actual,
    authAuthDatabaseLogin: jest.fn(),
    authAuthDatabaseLogout: jest.fn(),
    usersUsersCurrentUser: jest.fn(),
  }
})

jest.mock("@/lib/auth-navigation", () => ({
  navigateToDocument: jest.fn(),
  reloadCurrentDocument: jest.fn(),
}))

const mockLogin = jest.mocked(authAuthDatabaseLogin)
const mockLogout = jest.mocked(authAuthDatabaseLogout)
const mockCurrentUser = jest.mocked(usersUsersCurrentUser)
const mockReloadCurrentDocument = jest.mocked(reloadCurrentDocument)
const mockNavigateToDocument = jest.mocked(navigateToDocument)
const mockCookieRemove = jest.spyOn(Cookies, "remove")

const loginData: AuthAuthDatabaseLoginData = {
  formData: {
    username: "new-user@example.test",
    password: "synthetic-password",
  },
}

const currentUser = new User({
  id: "old-user",
  email: "old-user@example.test",
  role: "basic",
  settings: {},
})

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
}

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

function deferred<T>() {
  let resolve!: (value: T | PromiseLike<T>) => void
  let reject!: (reason?: unknown) => void
  const promise = new Promise<T>((promiseResolve, promiseReject) => {
    resolve = promiseResolve
    reject = promiseReject
  })
  return { promise, resolve, reject }
}

describe("useAuthActions", () => {
  beforeEach(() => {
    jest.clearAllMocks()
    mockCurrentUser.mockReset()
  })

  afterAll(() => {
    mockCookieRemove.mockRestore()
  })

  it("reloads the document only after login succeeds", async () => {
    const queryClient = createQueryClient()
    const pendingLogin = deferred<void>()
    mockLogin.mockReturnValue(pendingLogin.promise as never)

    const { result } = renderHook(() => useAuthActions(), {
      wrapper: createWrapper(queryClient),
    })

    const loginPromise = result.current.login(loginData)
    expect(mockReloadCurrentDocument).not.toHaveBeenCalled()

    await act(async () => {
      pendingLogin.resolve(undefined)
      await expect(loginPromise).resolves.toBeUndefined()
    })

    expect(mockReloadCurrentDocument).toHaveBeenCalledTimes(1)
    queryClient.clear()
  })

  it("clears the active organization and navigates only after logout succeeds", async () => {
    const queryClient = createQueryClient()
    const pendingLogout = deferred<void>()
    mockLogout.mockReturnValue(pendingLogout.promise as never)

    const { result } = renderHook(() => useAuthActions(), {
      wrapper: createWrapper(queryClient),
    })

    const logoutPromise = result.current.logout("/sign-in?logged-out=true")
    expect(mockCookieRemove).not.toHaveBeenCalled()
    expect(mockNavigateToDocument).not.toHaveBeenCalled()

    await act(async () => {
      pendingLogout.resolve(undefined)
      await expect(logoutPromise).resolves.toBeUndefined()
    })

    expect(mockCookieRemove).toHaveBeenCalledWith("tracecat:active-org-id")
    expect(mockNavigateToDocument).toHaveBeenCalledWith(
      "/sign-in?logged-out=true"
    )
    queryClient.clear()
  })

  it("keeps the mounted auth observer and cache when login fails", async () => {
    const queryClient = createQueryClient()
    queryClient.setQueryData(["auth"], currentUser)
    mockLogin.mockRejectedValue(new Error("invalid credentials"))

    const { result } = renderHook(
      () => ({ auth: useAuth(), actions: useAuthActions() }),
      { wrapper: createWrapper(queryClient) }
    )

    await waitFor(() => expect(result.current.auth.user).toBe(currentUser))

    await act(async () => {
      await expect(result.current.actions.login(loginData)).rejects.toThrow(
        "invalid credentials"
      )
    })

    expect(queryClient.getQueryData(["auth"])).toBe(currentUser)
    expect(result.current.auth.user).toBe(currentUser)
    expect(mockReloadCurrentDocument).not.toHaveBeenCalled()
    queryClient.clear()
  })

  it("keeps mounted workspace data when logout fails", async () => {
    const queryClient = createQueryClient()
    const workspaceData = { id: "old-workspace", name: "Old workspace" }
    queryClient.setQueryData(["workspace", "old-workspace"], workspaceData)
    mockLogout.mockRejectedValue(new Error("logout unavailable"))

    const { result } = renderHook(
      () => ({ auth: useAuth(), actions: useAuthActions() }),
      { wrapper: createWrapper(queryClient) }
    )

    await act(async () => {
      await expect(result.current.actions.logout("/sign-in")).rejects.toThrow(
        "logout unavailable"
      )
    })

    expect(queryClient.getQueryData(["workspace", "old-workspace"])).toEqual(
      workspaceData
    )
    expect(mockCookieRemove).not.toHaveBeenCalled()
    expect(mockNavigateToDocument).not.toHaveBeenCalled()
    queryClient.clear()
  })
})
