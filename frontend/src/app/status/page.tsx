import Image from "next/image"
import TracecatIcon from "public/icon.png"

import { AlertNotification } from "@/components/notifications"

export default async function Page() {
  return (
    <main className="container flex h-full w-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-8 h-16 w-16" />
      <h1 className="text-lg font-semibold">
        We&apos;re sorry for the inconvenience.
      </h1>
      <AlertNotification
        level="info"
        className="text-2xl font-medium"
        message="Our site is currently down for maintenance. Please check our Discord for
        updates."
      />
    </main>
  )
}
