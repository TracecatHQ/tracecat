import {
  AlertTriangleIcon,
  CheckCircleIcon,
  CircleHelpIcon,
  CircleIcon,
  CirclePauseIcon,
  CircleSlashIcon,
  EyeIcon,
  FlagTriangleRightIcon,
  FlaskConicalIcon,
  InfoIcon,
  ShieldAlertIcon,
  ShieldCheckIcon,
  ShieldIcon,
  SignalHighIcon,
  SignalIcon,
  SignalMediumIcon,
  TrafficConeIcon,
} from "lucide-react"
import type {
  CasePriority,
  CaseSeverity,
  CaseStatus,
  CaseVerdict,
} from "@/client"

import type { CaseBadgeProps } from "@/components/cases/case-badge"

const palette = {
  gray: "bg-muted/50 text-muted-foreground",
  yellow: "bg-yellow-500/10 text-yellow-700 dark:text-yellow-400",
  orange: "bg-orange-500/10 text-orange-700 dark:text-orange-400",
  red: "bg-red-500/10 text-red-700 dark:text-red-400",
  fuchsia: "bg-fuchsia-500/10 text-fuchsia-700 dark:text-fuchsia-400",
  emerald: "bg-green-500/10 text-green-700 dark:text-green-400",
  sky: "bg-blue-500/10 text-blue-700 dark:text-blue-400",
  violet: "bg-violet-500/10 text-violet-700 dark:text-violet-400",
  slate: "bg-muted text-muted-foreground",
}

/**
 * Returns the text color classes of a category color, for tinting an icon or
 * label without the badge background.
 */
export function getCategoryTextClass(color?: string): string {
  const tokens = color
    ?.split(" ")
    .filter(
      (token) => token.startsWith("text-") || token.startsWith("dark:text-")
    )
  return tokens?.length ? tokens.join(" ") : "text-muted-foreground"
}

export const STATUSES: Record<CaseStatus, CaseBadgeProps<CaseStatus>> = {
  new: {
    value: "new",
    label: "New",
    icon: FlagTriangleRightIcon,
    color: palette.yellow,
  },
  in_progress: {
    value: "in_progress",
    label: "In Progress",
    icon: TrafficConeIcon,
    color: palette.sky,
  },
  on_hold: {
    value: "on_hold",
    label: "On Hold",
    icon: CirclePauseIcon,
    color: palette.orange,
  },
  resolved: {
    value: "resolved",
    label: "Resolved",
    icon: CheckCircleIcon,
    color: palette.emerald,
  },
  closed: {
    value: "closed",
    label: "Closed",
    icon: CheckCircleIcon,
    color: palette.violet,
  },
  other: {
    value: "other",
    label: "Other",
    icon: CircleIcon,
    color: palette.gray,
  },
  unknown: {
    value: "unknown",
    label: "Unknown",
    icon: CircleHelpIcon,
    color: palette.slate,
  },
} as const

export const PRIORITIES: Record<CasePriority, CaseBadgeProps<CasePriority>> = {
  unknown: {
    label: "Unknown",
    value: "unknown",
    icon: CircleHelpIcon,
    color: palette.gray,
  },
  low: {
    label: "Low",
    value: "low",
    icon: SignalMediumIcon,
    color: palette.yellow,
  },
  medium: {
    label: "Medium",
    value: "medium",
    icon: SignalHighIcon,
    color: palette.orange,
  },
  high: {
    label: "High",
    value: "high",
    icon: SignalIcon,
    color: palette.red,
  },
  critical: {
    label: "Critical",
    value: "critical",
    icon: AlertTriangleIcon,
    color: palette.fuchsia,
  },
  other: {
    label: "Other",
    value: "other",
    icon: CircleIcon,
    color: palette.gray,
  },
} as const

export const SEVERITIES: Record<CaseSeverity, CaseBadgeProps<CaseSeverity>> = {
  unknown: {
    label: "Unknown",
    value: "unknown",
    icon: CircleHelpIcon,
    color: palette.gray,
  },
  informational: {
    label: "Informational",
    value: "informational",
    icon: InfoIcon,
    color: palette.sky,
  },
  low: {
    label: "Low",
    value: "low",
    icon: SignalMediumIcon,
    color: palette.yellow,
  },
  medium: {
    label: "Medium",
    value: "medium",
    icon: SignalHighIcon,
    color: palette.orange,
  },
  high: {
    label: "High",
    value: "high",
    icon: SignalIcon,
    color: palette.red,
  },
  critical: {
    label: "Critical",
    value: "critical",
    icon: AlertTriangleIcon,
    color: palette.fuchsia,
  },
  fatal: {
    label: "Fatal",
    value: "fatal",
    icon: AlertTriangleIcon,
    color: palette.red,
  },
  other: {
    label: "Other",
    value: "other",
    icon: CircleIcon,
    color: palette.gray,
  },
}

/** Case verdicts aligned with OCSF Incident Finding verdict IDs 0-6. */
export const VERDICTS: Record<CaseVerdict, CaseBadgeProps<CaseVerdict>> = {
  unknown: {
    label: "Unknown",
    value: "unknown",
    icon: CircleHelpIcon,
    color: palette.gray,
  },
  false_positive: {
    label: "False positive",
    value: "false_positive",
    icon: ShieldCheckIcon,
    color: palette.emerald,
  },
  true_positive: {
    label: "True positive",
    value: "true_positive",
    icon: ShieldAlertIcon,
    color: palette.red,
  },
  disregard: {
    label: "Disregard",
    value: "disregard",
    icon: CircleSlashIcon,
    color: palette.slate,
  },
  suspicious: {
    label: "Suspicious",
    value: "suspicious",
    icon: EyeIcon,
    color: palette.orange,
  },
  benign: {
    label: "Benign",
    value: "benign",
    icon: ShieldIcon,
    color: palette.sky,
  },
  test: {
    label: "Test",
    value: "test",
    icon: FlaskConicalIcon,
    color: palette.violet,
  },
}
