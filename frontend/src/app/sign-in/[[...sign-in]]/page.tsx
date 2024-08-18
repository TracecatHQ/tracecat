"use client"

import { useRouter } from "next/navigation"
import { useAuth } from "@/providers/auth"

import { SignIn } from "@/components/auth/sign-in"

export default function Page() {
  const { user } = useAuth()
  const router = useRouter()
  if (user) {
    router.push("/workspaces")
  }
  return (
    <div className="flex size-full items-center justify-center">
      <SignIn className="flex size-16 w-full justify-center" />
    </div>
  )
}
