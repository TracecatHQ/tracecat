"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import Image from "next/image"
import Link from "next/link"
import { useRouter } from "next/navigation"
import TracecatIcon from "public/icon.png"
import type React from "react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import { Icons } from "@/components/icons"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import { useAuth, useAuthActions } from "@/hooks/use-auth"
import { getBaseUrl } from "@/lib/api"
import {
  sanitizeReturnUrl,
  serializeClearPostAuthReturnUrlCookie,
  serializePostAuthReturnUrlCookie,
} from "@/lib/auth-return-url"
import { useAppInfo } from "@/lib/hooks"
import { cn } from "@/lib/utils"

interface SignInProps extends React.HTMLProps<HTMLDivElement> {
  returnUrl?: string | null
  orgSlug?: string | null
}

type AuthDiscoveryMethod = "basic" | "oidc" | "saml"

type AuthDiscoverResponse = {
  method: AuthDiscoveryMethod
  next_url?: string | null
  organization_slug?: string | null
}

function setPostAuthReturnUrlCookie(returnUrl?: string | null): void {
  const secure = window.location.protocol === "https:"
  const sanitizedReturnUrl = sanitizeReturnUrl(returnUrl)
  document.cookie = sanitizedReturnUrl
    ? serializePostAuthReturnUrlCookie(sanitizedReturnUrl, secure)
    : serializeClearPostAuthReturnUrlCookie(secure)
}

async function startOidcLogin(
  orgSlug?: string | null,
  returnUrl?: string | null
): Promise<void> {
  const params = new URLSearchParams()
  params.append("scopes", "openid")
  params.append("scopes", "email")
  params.append("scopes", "profile")
  if (orgSlug) {
    params.set("org", orgSlug)
  }

  const response = await fetch(
    `${getBaseUrl()}/auth/oauth/oidc/authorize?${params.toString()}`,
    {
      credentials: "include",
    }
  )
  if (!response.ok) {
    throw new Error("Failed to start OIDC login")
  }
  const data = (await response.json()) as { authorization_url?: string }
  if (!data.authorization_url) {
    throw new Error("OIDC authorization URL missing")
  }

  setPostAuthReturnUrlCookie(returnUrl)
  window.location.href = data.authorization_url
}

async function startSamlLogin(
  email: string,
  nextUrl?: string | null,
  orgSlug?: string | null,
  returnUrl?: string | null
): Promise<void> {
  const fallbackParams = new URLSearchParams({ email })
  if (orgSlug) {
    fallbackParams.set("org", orgSlug)
  }
  const samlLoginUrl =
    nextUrl ?? `${getBaseUrl()}/auth/saml/login?${fallbackParams.toString()}`

  const response = await fetch(samlLoginUrl, { credentials: "include" })
  if (!response.ok) {
    throw new Error("Failed to start SAML login")
  }
  const data = (await response.json()) as { redirect_url?: string }
  if (!data.redirect_url) {
    throw new Error("SAML redirect URL missing")
  }

  setPostAuthReturnUrlCookie(returnUrl)
  window.location.href = data.redirect_url
}

export function SignIn({ className, returnUrl, orgSlug }: SignInProps) {
  const { user } = useAuth()
  const { appInfo, appInfoIsLoading, appInfoError } = useAppInfo(orgSlug)
  const [isDiscovering, setIsDiscovering] = useState(false)
  const [discoveredMethod, setDiscoveredMethod] = useState<"basic" | null>(null)
  const [discoveredEmail, setDiscoveredEmail] = useState("")
  const router = useRouter()

  if (user) {
    router.push("/workspaces")
  }

  if (appInfoIsLoading) {
    return <CenteredSpinner />
  }
  if (appInfoError) {
    throw appInfoError
  }

  const allowedAuthTypes: string[] = appInfo?.auth_allowed_types ?? []
  const showBasicAuth = allowedAuthTypes.includes("basic")
  const showGenericOidcAuth = allowedAuthTypes.includes("oidc")
  const showGoogleOauthAuth = allowedAuthTypes.includes("google_oauth")
  const showOidcAuth = showGenericOidcAuth || showGoogleOauthAuth

  const onDiscover = async (email: string) => {
    setIsDiscovering(true)
    setDiscoveredEmail(email)
    try {
      const response = await fetch(`${getBaseUrl()}/auth/discover`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ email, org: orgSlug ?? undefined }),
      })
      if (!response.ok) {
        throw new Error("Failed to discover authentication method")
      }
      const data = (await response.json()) as AuthDiscoverResponse

      if (data.method === "basic") {
        if (!showBasicAuth) {
          throw new Error("Password login is not enabled")
        }
        setDiscoveredMethod("basic")
        return
      }
      if (data.method === "oidc") {
        if (!showOidcAuth) {
          throw new Error("OIDC login is not enabled")
        }
        await startOidcLogin(orgSlug, returnUrl)
        return
      }

      await startSamlLogin(email, data.next_url, orgSlug, returnUrl)
    } catch (error) {
      console.error("Error discovering auth method", error)
      toast({
        title: "Unable to continue",
        description:
          "Could not determine authentication method for this email.",
      })
    } finally {
      setIsDiscovering(false)
    }
  }

  return (
    <div
      className={cn(
        "container flex size-full items-center justify-center",
        className
      )}
    >
      <div className="flex w-full flex-1 flex-col justify-center gap-2 px-8 sm:max-w-md">
        <CardHeader className="items-center space-y-2 text-center">
          <Image src={TracecatIcon} alt="Tracecat" className="mb-8 size-16" />
          <CardTitle className="text-2xl">Sign into your account</CardTitle>
          <CardDescription>
            {discoveredMethod === "basic"
              ? "Enter your password to continue"
              : "Enter your work email to continue"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex-col space-y-2">
          {discoveredMethod === "basic" ? (
            <BasicLoginForm orgSlug={orgSlug} initialEmail={discoveredEmail} />
          ) : (
            <EmailDiscoveryForm
              isLoading={isDiscovering}
              onSubmit={onDiscover}
            />
          )}
        </CardContent>
        {showBasicAuth && (
          <CardFooter className="flex items-center justify-center text-sm text-muted-foreground">
            <div className="mt-4 text-center">
              Don&apos;t have an account?{" "}
              <Link
                href={
                  returnUrl
                    ? `/sign-up?returnUrl=${encodeURIComponent(returnUrl)}`
                    : "/sign-up"
                }
                className="underline"
              >
                Sign up
              </Link>
            </div>
          </CardFooter>
        )}
      </div>
    </div>
  )
}

const discoverySchema = z.object({
  email: z.string().email().min(3, { message: "Required" }),
})

type DiscoveryFormValues = z.infer<typeof discoverySchema>

function EmailDiscoveryForm({
  isLoading,
  onSubmit,
}: {
  isLoading: boolean
  onSubmit: (email: string) => Promise<void>
}) {
  const form = useForm<DiscoveryFormValues>({
    resolver: zodResolver(discoverySchema),
    defaultValues: { email: "" },
  })

  const handleSubmit = async (values: DiscoveryFormValues) => {
    await onSubmit(values.email)
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-8">
        <div className="grid gap-4">
          <FormField
            control={form.control}
            name="email"
            render={({ field }) => (
              <FormItem>
                <FormLabel className="text-xs">Email</FormLabel>
                <FormControl>
                  <Input placeholder="user@company.com" {...field} />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />
          <Button className="w-full text-sm" disabled={isLoading} type="submit">
            {isLoading && (
              <Icons.spinner className="mr-2 size-4 animate-spin" />
            )}
            Continue
          </Button>
        </div>
      </form>
    </Form>
  )
}

const basicLoginSchema = z.object({
  email: z.string().email().min(3, { message: "Required" }),
  password: z
    .string()
    .min(12, "Password needs to be at least 12 characters long"),
})

type BasicLoginFormValues = z.infer<typeof basicLoginSchema>

export function BasicLoginForm({
  orgSlug,
  initialEmail,
}: {
  orgSlug?: string | null
  initialEmail?: string
}) {
  const [isLoading, setIsLoading] = useState(false)
  const { login } = useAuthActions(orgSlug)
  const form = useForm<BasicLoginFormValues>({
    resolver: zodResolver(basicLoginSchema),
    defaultValues: {
      email: "",
      password: "",
    },
  })

  useEffect(() => {
    if (initialEmail) {
      form.setValue("email", initialEmail)
    }
  }, [form, initialEmail])

  const onSubmit = async (values: BasicLoginFormValues) => {
    try {
      setIsLoading(true)
      await login({
        formData: {
          username: values.email,
          password: values.password,
        },
      })
    } catch (error) {
      console.error("Error signing in", error)
      toast({
        title: "Error signing in",
        description: "Please check your email and password and try again",
      })
    } finally {
      setIsLoading(false)
    }
  }

  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <div className="flex w-full flex-col space-y-4">
          <div className="grid gap-4">
            <div className="grid gap-2">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Email</FormLabel>
                    <FormControl>
                      <Input placeholder="user@company.com" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <div className="grid gap-2">
              <div className="grid gap-2">
                <FormField
                  control={form.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Password</FormLabel>
                      <FormControl>
                        <Input
                          type="password"
                          placeholder="••••••••••••••••"
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />
              </div>
            </div>
          </div>
          <Button className="w-full text-sm" disabled={isLoading} type="submit">
            {isLoading && (
              <Icons.spinner className="mr-2 size-4 animate-spin" />
            )}
            Sign In
          </Button>
        </div>
      </form>
    </Form>
  )
}
