"use client"

import Image from "next/image"
import { useRouter } from "next/navigation"
import TracecatIcon from "public/icon.png"
import { Button } from "@/components/ui/button"

export default function NotFound() {
  const router = useRouter()
  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
      <h1 className="text-2xl font-medium">Page not found</h1>
      <p className="text-sm text-muted-foreground">
        The page you are looking for does not exist.
      </p>
      <Button variant="outline" onClick={() => router.replace("/")}>
        Return to the home page
      </Button>
    </main>
  )
}
