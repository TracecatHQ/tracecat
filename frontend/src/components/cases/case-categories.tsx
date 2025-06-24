import {
  AlertTriangleIcon,
  CheckCircleIcon,
  CircleHelpIcon,
  CircleIcon,
  CirclePauseIcon,
  FlagTriangleRightIcon,
  InfoIcon,
  SignalHighIcon,
  SignalIcon,
  SignalMediumIcon,
  TrafficConeIcon,
} from "lucide-react"
import type { CasePriority, CaseSeverity, CaseStatus } from "@/client"

import type { CaseBadgeProps } from "@/components/cases/case-badge"

const palette = {
  gray: "bg-gray-100/70 border-gray-400/70 text-gray-500/80",
  yellow: "bg-yellow-100/80 border-yellow-600/70 text-yellow-700/80",
  orange: "bg-orange-100/80 border-orange-600/70 text-orange-700/80",
  red: "bg-red-100 border-red-400/70 text-red-700/80",
  fuchsia: "bg-fuchsia-100 border-fuchsia-400/70 text-fuchsia-700/80",
  emerald: "bg-emerald-100 border-emerald-400/70 text-emerald-700/80",
  sky: "bg-sky-100 border-sky-400/70 text-sky-700/80",
}

export const STATUSES: Record<CaseStatus, CaseBadgeProps<CaseStatus>> = {
  unknown: {
    value: "unknown",
    label: "Unknown",
    icon: CircleHelpIcon,
    color: palette.gray,
  },
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
    color: palette.fuchsia,
  },
  other: {
    value: "other",
    label: "Other",
    icon: CircleIcon,
    color: palette.yellow,
  },
  on_hold: {
    value: "on_hold",
    label: "On Hold",
    icon: CirclePauseIcon,
    color: palette.orange,
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
    color: palette.red,
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
    color: palette.sky,
  },
}
