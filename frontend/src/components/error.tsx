"use client"

// Error components must be Client Components
import { useEffect } from "react"
import { AppRouterInstance } from "next/dist/shared/lib/app-router-context.shared-runtime"
import Image from "next/image"
import Link from "next/link"
import { AxiosError } from "axios"
import TracecatIcon from "public/icon.png"

import { Button } from "@/components/ui/button"
import { AlertLevel, AlertNotification } from "@/components/notifications"

type ErrorProps = Error & { digest?: string }

export default function Error({ error }: { error: ErrorProps | AxiosError }) {
  const { headline, level, message, action } = refineError(error)
  useEffect(() => {
    // Log the error to an error reporting service
    console.error("log error", error)
  }, [error])
  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-8 size-16" />
      <h1 className="text-2xl font-medium">{headline}</h1>
      {action}
      <AlertNotification level={level} message={message} />
    </main>
  )
}
function sessionExpiredError(router: AppRouterInstance): CustomError {
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

type CustomError = {
  headline: string
  level: AlertLevel
  message: string
  action: React.ReactNode | boolean
}
function refineError(error: ErrorProps | AxiosError): CustomError {
  console.log("HANDLING ERROR", error)
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
