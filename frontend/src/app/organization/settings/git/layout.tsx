import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Git repository | Organization",
}

export default function GitLayout({ children }: { children: React.ReactNode }) {
  return children
}
