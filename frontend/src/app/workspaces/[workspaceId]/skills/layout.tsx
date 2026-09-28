import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Skills",
}

/** Applies shared metadata and renders the nested Skills route content. */
export default function SkillsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
