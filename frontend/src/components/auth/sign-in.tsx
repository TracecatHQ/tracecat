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
import type { AuthDiscoverResponse } from "@/client"
import { authDiscoverAuthMethod } from "@/client"
import { OidcOAuthButton } from "@/components/auth/oauth-buttons"
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
import {
  sanitizeReturnUrl,
  serializeClearPostAuthReturnUrlCookie,
  serializePostAuthReturnUrlCookie,
} from "@/lib/auth-return-url"
import { useAppInfo } from "@/lib/hooks"
import { cn } from "@/lib/utils"

interface SignInProps extends React.HTMLProps<HTMLDivElement> {
  returnUrl?: string | null
}

function setPostAuthReturnUrlCookie(returnUrl?: string | null): void {
  const secure = window.location.protocol === "https:"
  const sanitizedReturnUrl = sanitizeReturnUrl(returnUrl)
  document.cookie = sanitizedReturnUrl
    ? serializePostAuthReturnUrlCookie(sanitizedReturnUrl, secure)
    : serializeClearPostAuthReturnUrlCookie(secure)
}

async function startSamlLogin(
  returnUrl?: string | null,
  nextUrl?: string | null
): Promise<void> {
  // Use the org-scoped next_url from discovery when available so the
  // backend can resolve the correct organization for SAML login.
  const loginUrl = nextUrl ?? "/api/auth/saml/login"
  const res = await fetch(loginUrl, { credentials: "include" })
  if (!res.ok) {
    throw new Error(`SAML login request failed: ${res.status}`)
  }
  const { redirect_url } = (await res.json()) as { redirect_url: string }
  setPostAuthReturnUrlCookie(returnUrl)
  window.location.href = redirect_url
}

export function SignIn({ className, returnUrl }: SignInProps) {
  const { user } = useAuth()
  const { appInfo, appInfoIsLoading, appInfoError } = useAppInfo()
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
  const oidcProviderLabel = showGenericOidcAuth ? "Single sign-on" : "Google"
  const oidcProviderIcon = showGenericOidcAuth ? "saml" : "google"
  const onDiscover = async (email: string) => {
    setIsDiscovering(true)
    setDiscoveredEmail(email)
    try {
      const data: AuthDiscoverResponse = await authDiscoverAuthMethod({
        requestBody: { email },
      })

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
        // Fall through — show OIDC buttons so user can click
        // (OIDC requires user-initiated navigation for OAuth redirects)
        setDiscoveredMethod(null)
        return
      }
      if (data.method === "saml") {
        await startSamlLogin(returnUrl, data.next_url)
        return
      }
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
            <BasicLoginForm initialEmail={discoveredEmail} />
          ) : (
            <EmailDiscoveryForm
              isLoading={isDiscovering}
              onSubmit={onDiscover}
            />
          )}
          {discoveredMethod === null && showOidcAuth && (
            <div className="relative">
              <div className="absolute inset-0 flex items-center">
                <span className="w-full border-t" />
              </div>
              <div className="relative my-6 flex justify-center text-xs uppercase">
                <span className="bg-background px-2 text-muted-foreground">
                  Or continue with
                </span>
              </div>
            </div>
          )}
          {discoveredMethod === null && showOidcAuth && (
            <OidcOAuthButton
              className="w-full"
              returnUrl={returnUrl}
              providerLabel={oidcProviderLabel}
              providerIcon={oidcProviderIcon}
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

export function BasicLoginForm({ initialEmail }: { initialEmail?: string }) {
  const [isLoading, setIsLoading] = useState(false)
  const { login } = useAuthActions()
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
