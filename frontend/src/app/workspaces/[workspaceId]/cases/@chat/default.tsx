import { MessageCircle } from "lucide-react"

export default async function ChatDefault() {
  return (
    <div className="h-full flex items-center justify-center">
      <div className="text-center text-muted-foreground">
        <MessageCircle className="h-12 w-12 mx-auto mb-4 opacity-50" />
        <p className="text-sm">Select a case to view its chats</p>
        <p className="text-xs mt-1">
          Chat with AI about case details and analysis
        </p>
      </div>
    </div>
  )
}
