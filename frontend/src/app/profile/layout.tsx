import type { Metadata } from "next"
import { AuthGuard } from "@/components/auth/auth-guard"
import { ProfileLayout } from "@/components/sidebar/profile-layout"

export const metadata: Metadata = {
  title: "Profile | Tracecat",
}

export default function ProfileLayoutPage({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <AuthGuard>
      <ProfileLayout>{children}</ProfileLayout>
    </AuthGuard>
  )
}
