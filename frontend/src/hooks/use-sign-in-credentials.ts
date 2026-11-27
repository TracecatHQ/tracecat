"use client"

import {
  type LoginMethodsRead,
  type PasswordChange,
  usersChangeCurrentUserPassword,
  usersGetCurrentUserLoginMethods,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useAppInfo } from "@/lib/hooks"
import { useMutation, useQuery } from "@/lib/query"

const LOGIN_METHODS_QUERY_KEY = ["auth", "login-methods"]

/**
 * Change the current user's sign-in password.
 *
 * `canChangeCredentials` is true only when password sign-in is enabled for the
 * deployment and allowed for this user (e.g. not SAML-enforced).
 */
export function useSignInCredentials() {
  const { appInfo } = useAppInfo()
  const basicEnabled = appInfo?.auth_allowed_types.includes("basic") ?? false
  const { data: loginMethods, isLoading } = useQuery<
    LoginMethodsRead,
    TracecatApiError
  >({
    queryKey: LOGIN_METHODS_QUERY_KEY,
    queryFn: usersGetCurrentUserLoginMethods,
    enabled: basicEnabled,
  })

  const { mutateAsync: changePassword, isPending: changePasswordPending } =
    useMutation<unknown, TracecatApiError, PasswordChange>({
      mutationFn: async (requestBody) =>
        await usersChangeCurrentUserPassword({ requestBody }),
      onSuccess: () => {
        toast({
          title: "Password updated",
          description: "Your other sessions have been signed out.",
        })
      },
      onError: (error) => {
        toast({
          title: "Failed to update password",
          description: getApiErrorDetail(error) ?? "Please try again.",
          variant: "destructive",
        })
      },
    })

  return {
    canChangeCredentials: basicEnabled && loginMethods?.password === true,
    isLoading: basicEnabled && isLoading,
    changePassword,
    changePasswordPending,
  }
}
