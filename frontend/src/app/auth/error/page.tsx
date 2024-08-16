"use client"

import Link from "next/link"

export default function Page() {
  return (
    <div className="flex h-screen flex-col items-center justify-center">
      <div className="font-bold">
        Unable to login, please try again and/or contact an administrator.
      </div>
      <Link href="/sign-in" className="w-fit">
        Back to login
      </Link>
    </div>
  )
}
