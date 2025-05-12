import { Metadata } from "next"

export const metadata: Metadata = {
  title: "Email authentication | Organization",
}

export default function EmailAuthenticationLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
