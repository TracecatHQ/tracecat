import { ExternalLink } from "lucide-react"
import Image from "next/image"
import TracecatIcon from "public/icon.png"
import { Alert, AlertDescription } from "@/components/ui/alert"

const CONTACT_US_URL = "https://cal.com/team/tracecat"
const SYSTEM_MSG_KINDS = ["beta-ee-contact-us"] as const
type SystemMessageKind = (typeof SYSTEM_MSG_KINDS)[number]

const SYSTEM_MSG_MAP: Record<SystemMessageKind, React.ReactNode> = {
  "beta-ee-contact-us": (
    <>
      This feature is in Beta and may be promoted to an enterprise-only feature.
      Please{" "}
      <a
        href={CONTACT_US_URL}
        target="_blank"
        rel="noopener noreferrer"
        className="underline hover:text-sky-800 dark:hover:text-sky-100 inline-flex items-center gap-1"
      >
        contact us
        <ExternalLink className="size-3 inline-block" />
      </a>{" "}
      for more information.
    </>
  ),
}

export function SystemInfoAlert({ kind }: { kind: SystemMessageKind }) {
  return (
    <Alert className="border border-sky-200 bg-sky-50 text-xs text-sky-700 dark:border-sky-800 dark:bg-sky-950/40 dark:text-sky-200">
      <AlertDescription className="text-xs flex items-start gap-2">
        <Image
          src={TracecatIcon}
          alt="Tracecat"
          className="size-3.5 inline-block mt-0.5"
        />
        <span>{SYSTEM_MSG_MAP[kind]}</span>
      </AlertDescription>
    </Alert>
  )
}
