import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Chat",
}

export default function WorkspaceChatLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
