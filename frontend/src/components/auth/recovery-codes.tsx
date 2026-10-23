"use client"

import { Copy } from "lucide-react"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"

export function RecoveryCodes({ codes }: { codes: string[] }) {
  return (
    <div className="space-y-3 rounded-md border p-4">
      <p className="text-sm text-muted-foreground">
        Save these recovery codes somewhere safe. Each one signs you in once if
        you lose your security key. They won&apos;t be shown again.
      </p>
      <div className="grid grid-cols-2 gap-2 font-mono text-xs">
        {codes.map((code) => (
          <span key={code}>{code}</span>
        ))}
      </div>
      <Button
        variant="outline"
        size="sm"
        onClick={async () => {
          await navigator.clipboard.writeText(codes.join("\n"))
          toast({ title: "Copied recovery codes" })
        }}
      >
        <Copy className="mr-2 size-4" />
        Copy codes
      </Button>
    </div>
  )
}
