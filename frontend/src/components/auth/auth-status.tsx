"use client"

import { getBaseUrl } from "@/lib/api"
import { useQuery } from "@/lib/query"
import { cn } from "@/lib/utils"

const STATUS_REFRESH_MS = 30 * 1000

type ProbeResult = { ok: true; latencyMs: number } | { ok: false }

async function probe(path: string): Promise<ProbeResult> {
  const started = performance.now()
  try {
    const response = await fetch(`${getBaseUrl()}${path}`, {
      cache: "no-store",
      credentials: "omit",
    })
    if (!response.ok) {
      return { ok: false }
    }
    return { ok: true, latencyMs: Math.round(performance.now() - started) }
  } catch {
    return { ok: false }
  }
}

function useProbe(path: string) {
  const { data } = useQuery({
    queryKey: ["auth-status", path],
    queryFn: async () => await probe(path),
    refetchInterval: STATUS_REFRESH_MS,
    retry: false,
  })
  return data
}

function StatusRow({
  label,
  result,
  okText,
}: {
  label: string
  result: ProbeResult | undefined
  okText: string
}) {
  let text = "checking"
  let tone = "bg-zinc-500"
  if (result?.ok) {
    text = okText
    tone = "bg-emerald-500"
  } else if (result) {
    text = "unreachable"
    tone = "bg-red-500"
  }
  return (
    <div className="flex items-center justify-between gap-6">
      <span className="text-zinc-400">{label}</span>
      <span className="flex items-center gap-2 text-zinc-200">
        <span className={cn("size-1.5 rounded-full", tone)} aria-hidden />
        {text}
      </span>
    </div>
  )
}

/**
 * Shows whether the browser can reach the API and whether the API can reach
 * its database, so blocked or misrouted API traffic is visible on sign-in.
 */
export function AuthStatus() {
  const api = useProbe("/health")
  const database = useProbe("/ready")
  const apiOkText = api?.ok ? `online · ${api.latencyMs} ms` : "online"
  return (
    <div className="w-full max-w-xs space-y-2 rounded-md border border-zinc-800 px-4 py-3 font-mono text-xs">
      <StatusRow label="api" result={api} okText={apiOkText} />
      <StatusRow
        label="database"
        result={api && !api.ok ? { ok: false } : database}
        okText="ready"
      />
    </div>
  )
}
