import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Variables",
}

/** Applies shared metadata and renders the nested Variables route content. */
export default function VariablesLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
