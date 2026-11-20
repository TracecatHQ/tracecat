"use client"

import {
  type AgentChannelUserRead,
  agentChannelUsersListMyChannelUsers,
  agentChannelUsersUnlinkMyChannelUser,
} from "@/client"
import { toast } from "@/components/ui/use-toast"
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
  return { links, isLoading, error, unlink, unlinkPending }
}
