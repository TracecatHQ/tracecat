"use client"

import { useEffect } from "react"
import {
  type AgentChannelUserRead,
  agentChannelUsersListMyChannelUsers,
  agentChannelUsersStartMyChannelLinkSignIn,
  agentChannelUsersUnlinkMyChannelUser,
  type ChannelLinkSignInRead,
} from "@/client"
import { useSettingsModal } from "@/components/settings/settings-modal-context"
import { toast } from "@/components/ui/use-toast"
import { linkFailureMessage } from "@/lib/channel-link-failures"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { useMutation, useQuery, useQueryClient } from "@/lib/query"

const MY_CHANNEL_USERS_QUERY_KEY = ["agent-channel-users", "me"]

/** The signed-in user's chat accounts linked in the current organization. */
export function useMyChannelUsers() {
  const queryClient = useQueryClient()
  const {
    data: links,
    isLoading,
    error,
  } = useQuery<AgentChannelUserRead[], TracecatApiError>({
    queryKey: MY_CHANNEL_USERS_QUERY_KEY,
    queryFn: async () => {
      // Only the first page is read; a user links a handful of accounts.
      const page = await agentChannelUsersListMyChannelUsers({ limit: 100 })
      return page.items
    },
  })
  const { mutateAsync: unlink, isPending: unlinkPending } = useMutation<
    void,
    TracecatApiError,
    string
  >({
    mutationFn: async (linkId) =>
      await agentChannelUsersUnlinkMyChannelUser({ linkId }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: MY_CHANNEL_USERS_QUERY_KEY,
      })
      toast({ title: "Account unlinked" })
    },
    onError: (error) => {
      toast({
        title: "Failed to unlink account",
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
    },
  })
  const { mutate: startSignIn, isPending: signInPending } = useMutation<
    ChannelLinkSignInRead,
    TracecatApiError
  >({
    mutationFn: async () => await agentChannelUsersStartMyChannelLinkSignIn(),
    onSuccess: ({ sign_in_url }) => window.location.assign(sign_in_url),
    onError: (error) => {
      toast({
        title: "Couldn't start linking",
        description: getApiErrorDetail(error) ?? "Please try again.",
        variant: "destructive",
      })
    },
  })
  return {
    links,
    isLoading,
    error,
    unlink,
    unlinkPending,
    startSignIn,
    signInPending,
  }
}

/** Toast the outcome of a sign-in started from Settings, handed over by its deep link. */
export function useChannelLinkResultToast() {
  const { takeSectionParams } = useSettingsModal()
  useEffect(() => {
    const params = takeSectionParams()
    const result = params?.get("result")
    if (result === "linked") {
      toast({ title: "Slack account linked" })
    } else if (result === "error") {
      toast({
        title: "Couldn't link your Slack account",
        description: linkFailureMessage(params?.get("error") ?? ""),
        variant: "destructive",
      })
    }
  }, [takeSectionParams])
}
