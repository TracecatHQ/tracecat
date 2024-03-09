"use client"

import React, { useEffect } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useSupabase } from "@/providers/supabase"
import { User } from "@supabase/supabase-js"
import { Loader2 } from "lucide-react"

import { fetchWorkflows, WorkflowMetadata } from "@/lib/flow"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

export default function Page() {
  return <WorkflowsPage suppressHydrationWarning />
}
function WorkflowsPage(props: React.HTMLAttributes<HTMLElement>) {
  const { supabase, session } = useSupabase()
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
  const router = useRouter()
  const [userWorkflows, setUserWorkflows] = React.useState<WorkflowMetadata[]>(
    []
  )
  const [user, setUser] = React.useState<User | null>(null)
  const [isLoading, setIsLoading] = React.useState(true)
  const [error, setError] = React.useState<Error | null>(null)

  const signOut = async () => {
    await supabase.auth.signOut()
    router.push("/login")
    router.refresh()
    setUser(null)
  }

  useEffect(() => {
    if (user) {
      fetchWorkflows().then((workflows) => setUserWorkflows(workflows))
    } else {
      setUserWorkflows([])
    }
  }, [user])

  useEffect(() => {
    async function getUser() {
      const {
        data: { user },
      } = await supabase.auth.getUser()
      setUser(user)
      setIsLoading(false)
    }
    getUser()
  }, [])

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

  if (error) {
    return (
      <div
        className="container flex h-full w-full items-center justify-center"
        {...props}
      >
        <div>Error: {error.message}</div>
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
