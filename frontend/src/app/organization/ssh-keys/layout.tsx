import { Metadata } from "next"

export const metadata: Metadata = {
  title: "SSH keys | Organization",
}

export default function SSHKeysLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return children
}
