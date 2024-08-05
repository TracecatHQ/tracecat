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
  usersUsersCurrentUser,
} from "@/client"
import { MutateFunction, useQuery, useQueryClient } from "@tanstack/react-query"

import { authConfig } from "@/config/auth"

type AuthContextType = {
  user: UserRead | null
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

export async function getCurrentUser(): Promise<UserRead | null> {
  try {
    console.log("Fetching current user")
    return await usersUsersCurrentUser()
  } catch (error) {
    if (error instanceof ApiError) {
      // Backend throws 401 unauthorized if the user is not logged in
      console.log("User is not logged in")
      return null
    } else {
      console.error("Error fetching current user", error)
      throw error
    }
  }
}
export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const router = useRouter()
  const {
    data: user,
    isLoading,
    error,
  } = useQuery<UserRead | null, ApiError>({
    queryKey: ["auth"],
    queryFn: getCurrentUser,
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
    if (!user && !isLoading) {
      router.push("/")
    }
  }, [user, isLoading])
  return (
    <AuthContext.Provider
      value={{
        user: user ?? null,
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
