"use client"

import React, { useEffect, useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useSessionContext } from "@/providers/session"
import { Loader2 } from "lucide-react"

import { WorkflowMetadata } from "@/types/schemas"
import { fetchWorkflows } from "@/lib/flow"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import NoSSR from "@/components/no-ssr"

export default function Page() {
  return (
    <NoSSR>
      <WorkflowsPage suppressHydrationWarning />
    </NoSSR>
  )
}
function WorkflowsPage(props: React.HTMLAttributes<HTMLElement>) {
  const { supabaseClient, session, isLoading } = useSessionContext()
  if (!session) {
    return (
      <div
        className="container flex h-full w-full items-center justify-center"
        {...props}
      >
        <Link href="/login">Go to Login</Link>
      </div>
    )
  }
  const { user } = session
  const router = useRouter()
  const [userWorkflows, setUserWorkflows] = useState<WorkflowMetadata[]>([])

  const signOut = async () => {
    await supabaseClient.auth.signOut()
    router.push("/login")
    router.refresh()
  }

  useEffect(() => {
    if (user) {
      fetchWorkflows().then((workflows) => setUserWorkflows(workflows))
    } else {
      setUserWorkflows([])
    }
  }, [user])

  if (isLoading || !user) {
    return (
      <div
        className="container flex h-full w-full items-center justify-center"
        {...props}
      >
        <Loader2 className="h-6 w-6 animate-spin" color="#8c8c8c" />
      </div>
    )
  }

  return (
    userWorkflows.length > 0 && (
      <div
        className="flex h-full w-full flex-col items-center justify-center"
        {...props}
      >
        Hello, {user.email}!
        {userWorkflows.map((workflow, idx) => (
          <Button
            key={idx}
            asChild
            className="h-[50px] rounded-md border-[1px] shadow-md"
            variant="ghost"
          >
            <Link
              key={idx}
              href={`/workflows/${workflow.id}`}
              className={cn(
                "dark:bg-muted dark:text-white dark:hover:bg-muted dark:hover:text-white",
                "m-4 flex flex-col justify-start"
              )}
            >
              {workflow.title}
              <span className="ml-2">{workflow.id}</span>
            </Link>
          </Button>
        ))}
        <Button onClick={signOut}>Sign out</Button>
      </div>
    )
  )
}
