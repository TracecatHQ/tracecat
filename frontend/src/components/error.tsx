"use client"

// Error components must be Client Components
import { useEffect } from "react"
import { AppRouterInstance } from "next/dist/shared/lib/app-router-context.shared-runtime"
import Image from "next/image"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { AxiosError } from "axios"
import TracecatIcon from "public/icon.png"

import { Button } from "@/components/ui/button"
import { AlertLevel, AlertNotification } from "@/components/notifications"

type ErrorProps = Error & { digest?: string }

export default function Error({ error }: { error: ErrorProps | AxiosError }) {
  const router = useRouter()
  const { headline, level, message, action } = refineError(error, router)
  useEffect(() => {
    // Log the error to an error reporting service
    console.error("log error", error)
  }, [error])
  return (
    <main className="container flex h-full w-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-8 h-16 w-16" />
      <h1 className="text-2xl font-medium">{headline}</h1>
      {action}
      <AlertNotification level={level} message={message} />
    </main>
  )
}

function refineError(
  error: ErrorProps | AxiosError,
  router: AppRouterInstance
): {
  headline: string
  level: AlertLevel
  message: string
  action: React.ReactNode | boolean
} {
  console.log("HANDLING ERROR", error)
  if (error instanceof AxiosError) {
    console.log("AXIOS ERROR", error)
    return {
      headline: "Your session has expired",
      level: "warning",
      message: "Please log in again.",
      action: (
        <Button
          variant="outline"
          onClick={async () => {
            router.push("/")
            router.refresh()
          }}
        >
          Log in
        </Button>
      ),
    }
  }
  return {
    headline: "Oh no! An error occurred :(",
    level: "error",
    message: error.message,
    action: (
      <Link href="/" className="">
        <Button variant="outline">Return to the home page</Button>
      </Link>
    ),
  }
}
