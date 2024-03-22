"use client"

// Error components must be Client Components
import { useEffect } from "react"
import Image from "next/image"
import Link from "next/link"
import TracecatIcon from "public/icon.png"

import { Button } from "@/components/ui/button"
import { AlertNotification } from "@/components/notifications"

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    // Log the error to an error reporting service
    console.error(error)
  }, [error])

  return (
    <main className="container flex h-full w-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-8 h-16 w-16" />
      <h1 className="text-2xl font-medium">Oh no! An error occurred :(</h1>

      <Link href="/" className="">
        <Button variant="outline">Return to the home page</Button>
      </Link>

      <AlertNotification level="error" message={error.message} />
    </main>
  )
}
