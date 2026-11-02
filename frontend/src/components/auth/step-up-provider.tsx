"use client"

import axios, { type AxiosResponse } from "axios"
import { KeyRound } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import { OpenAPI } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { verifyWithSecurityKey } from "@/lib/webauthn"

const STEP_UP_CODES = new Set([
  "mfa_step_up_required",
  "mfa_recent_verification_required",
])
const RETRY_MARKER = "__tracecatStepUpRetry"

export function isStepUpRequired(status: number, data: unknown): boolean {
  if (status !== 403 || typeof data !== "object" || data === null) {
    return false
  }
  const detail = (data as { detail?: unknown }).detail
  return (
    typeof detail === "object" &&
    detail !== null &&
    STEP_UP_CODES.has(String((detail as { code?: unknown }).code))
  )
}

/**
 * Retries any API request rejected for a missing step-up after the user
 * verifies with a security key. Any route that depends on step-up gets this
 * for free; there is nothing to wire up per action.
 */
export function StepUpProvider({ children }: { children: React.ReactNode }) {
  const [open, setOpen] = useState(false)
  const [verifying, setVerifying] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const resolverRef = useRef<((verified: boolean) => void) | null>(null)
  const inFlightRef = useRef<Promise<boolean> | null>(null)

  const settle = useCallback((verified: boolean) => {
    resolverRef.current?.(verified)
    resolverRef.current = null
    inFlightRef.current = null
    setOpen(false)
    setVerifying(false)
    setError(null)
  }, [])

  useEffect(() => {
    const requestStepUp = () => {
      if (!inFlightRef.current) {
        inFlightRef.current = new Promise<boolean>((resolve) => {
          resolverRef.current = resolve
          setOpen(true)
        })
      }
      return inFlightRef.current
    }
    const interceptor = async (response: AxiosResponse) => {
      const config = response.config as typeof response.config &
        Record<string, unknown>
      if (
        !isStepUpRequired(response.status, response.data) ||
        config[RETRY_MARKER]
      ) {
        return response
      }
      if (!(await requestStepUp())) {
        return response
      }
      const retryConfig: Record<string, unknown> = {
        ...config,
        validateStatus: () => true,
      }
      retryConfig[RETRY_MARKER] = true
      return await axios.request(
        retryConfig as unknown as typeof response.config
      )
    }
    OpenAPI.interceptors.response.use(interceptor)
    return () => OpenAPI.interceptors.response.eject(interceptor)
  }, [])

  const verify = async () => {
    setVerifying(true)
    setError(null)
    try {
      await verifyWithSecurityKey()
      settle(true)
    } catch (e) {
      setVerifying(false)
      setError(e instanceof Error ? e.message : "Verification failed.")
    }
  }

  return (
    <>
      {children}
      <Dialog open={open} onOpenChange={(next) => !next && settle(false)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Verify it&apos;s you</DialogTitle>
            <DialogDescription>
              This action needs your security key or passkey. You won&apos;t be
              asked again for 5 minutes.
            </DialogDescription>
          </DialogHeader>
          {error && <p className="text-sm text-destructive">{error}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => settle(false)}>
              Cancel
            </Button>
            <Button onClick={verify} disabled={verifying}>
              <KeyRound className="mr-2 size-4" />
              {verifying ? "Waiting for security key..." : "Use security key"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
