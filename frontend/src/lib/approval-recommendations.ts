import type { LucideIcon } from "lucide-react"
import { CheckCircle2, HelpCircle, Sparkles, XCircle } from "lucide-react"

type VerdictKey = "approve" | "reject" | "hold"

type RecommendationDisplay = {
  verdict: VerdictKey | "unknown"
  label: string
  description: string
  icon: LucideIcon
  badgeClassName: string
  surfaceClassName: string
  iconClassName: string
}

const BASE_BADGE_CLASSES =
  "inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide"

const BASE_SURFACE_CLASSES =
  "rounded-md border px-2.5 py-2 text-xs shadow-sm transition-colors"

const METADATA: Record<VerdictKey, RecommendationDisplay> = {
  approve: {
    verdict: "approve",
    label: "Approve",
    description: "AI suggests approving this tool call.",
    icon: CheckCircle2,
    badgeClassName: `${BASE_BADGE_CLASSES} border-emerald-200 bg-emerald-50 text-emerald-700`,
    surfaceClassName: `${BASE_SURFACE_CLASSES} border-emerald-200/70 bg-emerald-50/60`,
    iconClassName: "text-emerald-600",
  },
  reject: {
    verdict: "reject",
    label: "Reject",
    description: "AI recommends rejecting this tool call.",
    icon: XCircle,
    badgeClassName: `${BASE_BADGE_CLASSES} border-rose-200 bg-rose-50 text-rose-600`,
    surfaceClassName: `${BASE_SURFACE_CLASSES} border-rose-200/70 bg-rose-50/60`,
    iconClassName: "text-rose-600",
  },
  hold: {
    verdict: "hold",
    label: "Hold",
    description: "AI cannot decide; flag for manual review.",
    icon: HelpCircle,
    badgeClassName: `${BASE_BADGE_CLASSES} border-amber-200 bg-amber-50 text-amber-700`,
    surfaceClassName: `${BASE_SURFACE_CLASSES} border-amber-200/70 bg-amber-50/60`,
    iconClassName: "text-amber-600",
  },
}

const FALLBACK: RecommendationDisplay = {
  verdict: "unknown",
  label: "No recommendation",
  description: "Add a default approval manager to see recommendations.",
  icon: Sparkles,
  badgeClassName: `${BASE_BADGE_CLASSES} border-muted/60 bg-muted text-muted-foreground`,
  surfaceClassName: `${BASE_SURFACE_CLASSES} border-muted/60 bg-muted/40`,
  iconClassName: "text-muted-foreground",
}

export function getRecommendationDisplay(
  verdict?: string | null
): RecommendationDisplay {
  if (!verdict || typeof verdict !== "string") {
    return FALLBACK
  }
  const normalized = verdict.trim().toLowerCase() as VerdictKey
  return METADATA[normalized] ?? FALLBACK
}
