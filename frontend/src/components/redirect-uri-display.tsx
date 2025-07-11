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
      await new Promise((resolve) => setTimeout(resolve, 2000))
    } catch (error) {
      console.error("Failed to copy:", error)
    } finally {
      setCopied(false)
    }
  }

  return (
    <div className={cn("space-y-2", className)}>
      <div className="flex items-center h-8 shadow-sm">
        <div className="h-full flex flex-1 items-center truncate rounded-md border bg-muted p-2 font-mono text-sm rounded-r-none border-r-0">
          <span className="text-xs text-muted-foreground">{redirectUri}</span>
        </div>
        <Button
          size="sm"
          variant="outline"
          onClick={handleCopy}
          className="flex items-center gap-2 rounded-l-none h-full shadow-none"
        >
          {copied ? (
            <CheckCircle className="size-3" />
          ) : (
            <Copy className="size-3" />
          )}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Add this redirect URI to your OAuth app settings
      </p>
    </div>
  )
}
