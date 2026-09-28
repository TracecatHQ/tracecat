import { useLocalStorage } from "@/hooks/use-local-storage"

export function useWorkspaceChatOpen() {
  return useLocalStorage<boolean>("workspace_chat_open", false)
}
