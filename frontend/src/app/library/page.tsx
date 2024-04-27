import React from "react"
import { type Metadata } from "next"

import { Library } from "@/components/library/workflow-catalog"
import Navbar from "@/components/nav/navbar"

export const metadata: Metadata = {
  title: "Library",
  description: "Pre-built workflows and templates ready to deploy.",
}
export default async function Page() {
  return (
    <div className="no-scrollbar flex h-screen max-h-screen flex-col">
      <Navbar />
      <Library />
    </div>
  )
}
