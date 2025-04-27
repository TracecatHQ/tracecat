"use client"

import React, { createContext, ReactNode, useContext, useEffect } from "react"
import { useRouter } from "next/navigation"
import {
  ApiError,
  authAuthDatabaseLogin,
  AuthAuthDatabaseLoginData,
  AuthAuthDatabaseLoginResponse,
  authAuthDatabaseLogout,
  AuthAuthDatabaseLogoutResponse,
  authRegisterRegister,
  AuthRegisterRegisterData,
  UserRead,
} from "@/client"
import { MutateFunction, useQuery, useQueryClient } from "@tanstack/react-query"

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

  const login = async (data: AuthAuthDatabaseLoginData) => {
    const loginResponse = await authAuthDatabaseLogin(data)
    await queryClient.invalidateQueries({
      queryKey: ["auth"],
    })
    return loginResponse
  }

  const logout = async () => {
    const logoutResponse = await authAuthDatabaseLogout()
    await queryClient.invalidateQueries({
      queryKey: ["auth"],
    })
    return logoutResponse
  }

  useEffect(() => {
    if (userError) {
      console.error("Error loading user", userError)
      router.push("/auth/error")
    } else if (!user && !userIsLoading) {
      console.log("No user loaded, redirecting to login")
      router.push("/")
    }
  }, [user, userIsLoading, userError])

  return (
    <AuthContext.Provider
      value={{
        user: user ?? null,
        userIsLoading,
        login,
        logout,
        register: authRegisterRegister,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (context === undefined) {
    throw new Error("useAuth must be used within a AuthProvider")
  }
  return context
}
