"use client"

import type { AxiosError } from "axios"
import Cookies from "js-cookie"
import { ChevronDownIcon } from "lucide-react"
import Image from "next/image"
import { useRouter } from "next/navigation"
import TracecatIcon from "public/icon.png"
// Error components must be Client Components
import { useEffect } from "react"
import {
  ApiError,
  type tracecat_ee__admin__organizations__schemas__OrgRead as OrgRead,
} from "@/client"
import { type AlertLevel, AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { useAdminOrganizations } from "@/hooks/use-admin"

type ErrorProps = Error & { digest?: string }

export default function Error({
  error,
}: {
  error: ErrorProps | AxiosError | ApiError
}) {
  const refined = refineError(error)
  useEffect(() => {
    // Log the error to an error reporting service
    console.error("log error", error)
  }, [error])

  if (refined.customComponent) {
    return refined.customComponent
  }

  const { headline, level, message, action } = refined
  return (
    <main className="container flex size-full max-w-[400px] flex-col items-center justify-center space-y-4">
      <Image src={TracecatIcon} alt="Tracecat" className="mb-4 size-16" />
      <h1 className="text-2xl font-medium">{headline}</h1>
      {action}
      {message ? <AlertNotification level={level} message={message} /> : null}
    </main>
  )
}
export type CustomError = {
  headline: string
  level: AlertLevel
  message: React.ReactNode | null
  action: React.ReactNode
  customComponent?: React.ReactNode
}
function refineError(error: ErrorProps): CustomError {
  if (error instanceof ApiError) {
    return apiErrorHandler(error)
  } else {
    return unexpectedError(error)
  }
}

function GoHome() {
  const router = useRouter()
  return (
    <Button variant="outline" onClick={() => router.replace("/")}>
      Return to the home page
    </Button>
  )
}

function OrgSelector() {
  const router = useRouter()
  const { organizations, isLoading, error } = useAdminOrganizations()

  const handleSelectOrg = (org: OrgRead) => {
    Cookies.set("tracecat-org-id", org.id, { path: "/", sameSite: "lax" })
    // Full page reload to ensure React Query refetches with new cookie
    window.location.reload()
  }

  if (isLoading) {
    return (
      <div className="text-sm text-muted-foreground">
        Loading organizations...
      </div>
    )
  }

  if (error || !organizations) {
    return (
      <Button
        variant="outline"
        onClick={() => router.replace("/admin/organizations")}
      >
        Go to organizations
      </Button>
    )
  }

  if (organizations.length === 0) {
    return (
      <div className="flex w-full flex-col items-center gap-4">
        <div className="text-sm text-muted-foreground">
          No organizations available
        </div>
        <Button
          variant="outline"
          onClick={() => router.push("/admin/organizations")}
        >
          Go to admin console
        </Button>
      </div>
    )
  }

  return (
    <div className="flex w-full flex-col gap-4">
      <div className="flex w-full flex-col gap-2">
        {organizations.map((org) => (
          <Button
            key={org.id}
            variant="outline"
            className="w-full justify-start"
            onClick={() => handleSelectOrg(org)}
          >
            {org.name}
          </Button>
        ))}
      </div>
      <Button
        variant="ghost"
        size="sm"
        className="text-muted-foreground"
        onClick={() => router.push("/admin/organizations")}
      >
        Go to admin console
      </Button>
    </div>
  )
}

function unexpectedError(error: ErrorProps | AxiosError): CustomError {
  console.log("HANDLING ERROR", error)
  return {
    headline: "Oh no! An error occurred :(",
    level: "error",
    message: error.message,
    action: <GoHome />,
  }
}

function getErrorLevel(status: number): AlertLevel {
  if (Math.floor(status / 100) === 4) {
    return "error"
  }
  return "warning"
}

interface ScopeError {
  error: {
    code: string
    message: string
    required_scopes: string[]
    missing_scopes?: string[]
  }
}

function isScopeError(body: unknown): body is ScopeError {
  return (
    typeof body === "object" &&
    body !== null &&
    "error" in body &&
    typeof (body as ScopeError).error === "object" &&
    (body as ScopeError).error !== null &&
    "code" in (body as ScopeError).error &&
    (body as ScopeError).error.code === "insufficient_scope" &&
    Array.isArray((body as ScopeError).error.missing_scopes)
  )
}

function PermissionDeniedPage({ body }: { body: unknown }) {
  const router = useRouter()
  const missingScopes = isScopeError(body)
    ? (body.error.missing_scopes ?? [])
    : []

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-4">
      <div className="flex max-w-md flex-col items-center text-center">
        <Image src={TracecatIcon} alt="Tracecat" className="mb-6 size-12" />

        <h1 className="mb-2 text-xl font-semibold tracking-tight">
          Access denied
        </h1>

        <p className="mb-6 text-sm text-muted-foreground">
          You don&apos;t have permission to access this resource.
          {missingScopes.length > 0 &&
            " Contact your administrator to request access."}
        </p>

        {missingScopes.length > 0 && (
          <Collapsible className="mb-6 w-full">
            <CollapsibleTrigger className="group flex w-full items-center justify-center gap-1 text-xs text-muted-foreground hover:text-foreground">
              <span>Missing permissions</span>
              <ChevronDownIcon className="size-3 transition-transform group-data-[state=open]:rotate-180" />
            </CollapsibleTrigger>
            <CollapsibleContent>
              <div className="mt-3 flex flex-wrap justify-center gap-1.5">
                {missingScopes.map((scope) => (
                  <span
                    key={scope}
                    className="inline-flex items-center rounded-md bg-muted px-2 py-1 font-mono text-xs text-muted-foreground"
                  >
                    {scope}
                  </span>
                ))}
              </div>
            </CollapsibleContent>
          </Collapsible>
        )}

        <Button variant="outline" size="sm" onClick={() => router.replace("/")}>
          Go to home
        </Button>
      </div>
    </main>
  )
}

function apiErrorHandler(error: ApiError): CustomError {
  const level = getErrorLevel(error.status)
  switch (error.status) {
    case 401:
      return {
        headline: "Your session has expired",
        level,
        message: "Please log in again.",
        action: <GoHome />,
      }
    case 403:
      return {
        headline: "Access denied",
        level,
        message: "",
        action: null,
        customComponent: <PermissionDeniedPage body={error.body} />,
      }
    case 404:
      return {
        headline: "Resource not found",
        level,
        message: "The resource you are looking for does not exist.",
        action: <GoHome />,
      }
    case 428:
      return {
        headline: "Select organization",
        level: "info",
        message: null,
        action: <OrgSelector />,
      }
    case 503:
      return {
        headline: "Service unavailable",
        level,
        message:
          "The service is temporarily unavailable. Please try again later.",
        action: <GoHome />,
      }
    default:
      return {
        headline: "An unexpected error occurred.",
        level,
        message: (
          <div className="space-y-4">
            <b>{error.message}</b>
            <pre className="whitespace-pre-wrap break-all">
              {typeof error.body === "string"
                ? error.body
                : JSON.stringify(error.body, null, 2)}
            </pre>
          </div>
        ),
        action: <GoHome />,
      }
  }
}
