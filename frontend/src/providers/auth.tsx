"use client"

import {
  type MutateFunction,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { useRouter } from "next/navigation"
import {
  createContext,
  type ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
} from "react"
import {
  type ApiError,
  type AuthAuthDatabaseLoginData,
  type AuthAuthDatabaseLoginResponse,
  type AuthAuthDatabaseLogoutResponse,
  type AuthRegisterRegisterData,
  authAuthDatabaseLogin,
  authAuthDatabaseLogout,
  authRegisterRegister,
  type UserRead,
} from "@/client"

import { authConfig } from "@/config/auth"
import { getCurrentUser, User } from "@/lib/auth"

type AuthContextType = {
  user: User | null
  userIsLoading: boolean
  login: MutateFunction<
    AuthAuthDatabaseLoginResponse,
    unknown,
    AuthAuthDatabaseLoginData,
    void
  >
  logout: MutateFunction<AuthAuthDatabaseLogoutResponse, unknown, void, unknown>
  register: MutateFunction<UserRead, unknown, AuthRegisterRegisterData, void>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const router = useRouter()
  const {
    data: user,
    isLoading: userIsLoading,
    error: userError,
  } = useQuery<User | null, ApiError>({
    queryKey: ["auth"],
    queryFn: async () => {
      const userRead = await getCurrentUser()
      return userRead ? new User(userRead) : null
    },
    retry: false,
    staleTime: authConfig.staleTime,
    refetchOnWindowFocus: true,
  })

  const login = useCallback(
    async (data: AuthAuthDatabaseLoginData) => {
      const loginResponse = await authAuthDatabaseLogin(data)
      await queryClient.invalidateQueries({
        queryKey: ["auth"],
      })
      return loginResponse
    },
    [queryClient]
  )

  const logout = useCallback(async () => {
    const logoutResponse = await authAuthDatabaseLogout()
    await queryClient.invalidateQueries({
      queryKey: ["auth"],
    })
    router.push("/sign-in")
    return logoutResponse
  }, [queryClient])

  useEffect(() => {
    if (userError) {
      console.error("Error loading user", userError)
      router.push("/auth/error")
    }
  }, [userError])

  const value = useMemo(
    () => ({
      user: user ?? null,
      userIsLoading,
      login,
      logout,
      register: authRegisterRegister,
    }),
    [user, userIsLoading, login, logout]
  )
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error("useAuth must be used within a AuthProvider")
  }
  return context
}
