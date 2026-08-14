import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Skills",
}

export default function SkillsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
