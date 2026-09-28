import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "MCP servers",
}

/** Applies shared metadata and renders the nested MCP servers route content. */
export default function McpServersLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
