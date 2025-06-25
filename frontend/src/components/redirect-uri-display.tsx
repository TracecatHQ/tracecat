"use client"

import { CheckCircle, Copy } from "lucide-react"
import { useState } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

interface RedirectUriDisplayProps {
  redirectUri: string
  className?: string
}

export function RedirectUriDisplay({
  redirectUri,
  className,
}: RedirectUriDisplayProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(redirectUri)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch (error) {
      console.error("Failed to copy:", error)
    }
  }

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-center gap-2">
        <div className="flex-1 truncate rounded-md border bg-muted p-2 font-mono text-sm">
          {redirectUri}
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={handleCopy}
          className="flex items-center gap-2"
        >
          {copied ? (
            <>
              <CheckCircle className="size-3" />
              Copied
            </>
          ) : (
            <>
              <Copy className="size-3" />
              Copy
            </>
          )}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Add this redirect URI to your OAuth app settings
      </p>
    </div>
  )
}
