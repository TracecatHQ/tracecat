import { CasePriority, CaseSeverity, CaseStatus } from "@/client"
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  CircleIcon,
  FlagTriangleRightIcon,
  SignalHighIcon,
  SignalIcon,
  SignalMediumIcon,
  TrafficConeIcon,
} from "lucide-react"

import { CaseBadgeProps } from "@/components/cases/case-badge"

const statusColors: Record<CaseStatus, string> = {
  unknown: "bg-blue-100/70 border-blue-600/70 text-blue-700/80",
  new: "bg-yellow-100/80 border-yellow-600/70 text-yellow-700/80",
  in_progress: "bg-orange-100 border-orange-600 text-orange-700",
  resolved: "bg-red-100 border-red-400 text-red-700",
  closed: "bg-fuchsia-100 border-fuchsia-400 text-fuchsia-700",
  other: "bg-red-100 border-red-400 text-red-700",
  on_hold: "bg-red-100 border-red-400 text-red-700",
}

export const STATUSES: CaseBadgeProps<CaseStatus>[] = [
  {
    value: "unknown",
    label: "Unknown",
    icon: CircleIcon,
    color: statusColors.unknown,
  },
  {
    value: "new",
    label: "New",
    icon: FlagTriangleRightIcon,
    color: statusColors.new,
  },
  {
    value: "in_progress",
    label: "In Progress",
    icon: TrafficConeIcon,
    color: statusColors.in_progress,
  },
  {
    value: "resolved",
    label: "Resolved",
    icon: CheckCircleIcon,
    color: statusColors.resolved,
  },
  {
    value: "closed",
    label: "Closed",
    icon: CheckCircleIcon,
    color: statusColors.closed,
  },
  {
    value: "other",
    label: "Other",
    icon: CircleIcon,
    color: statusColors.other,
  },
  {
    value: "on_hold",
    label: "On Hold",
    icon: CircleIcon,
    color: statusColors.on_hold,
  },
]

const priorityColors: Record<CasePriority, string> = {
  unknown: "bg-blue-100/70 border-blue-600/70 text-blue-700/80",
  low: "bg-yellow-100/80 border-yellow-600/70 text-yellow-700/80",
  medium: "bg-orange-100 border-orange-600 text-orange-700",
  high: "bg-red-100 border-red-400 text-red-700",
  critical: "bg-fuchsia-100 border-fuchsia-400 text-fuchsia-700",
  other: "bg-red-100 border-red-400 text-red-700",
}

export const PRIORITIES: CaseBadgeProps<CasePriority>[] = [
  {
    label: "Unknown",
    value: "unknown",
    icon: CircleIcon,
    color: priorityColors.unknown,
  },
  {
    label: "Low",
    value: "low",
    icon: SignalMediumIcon,
    color: priorityColors.low,
  },
  {
    label: "Medium",
    value: "medium",
    icon: SignalHighIcon,
    color: priorityColors.medium,
  },
  {
    label: "High",
    value: "high",
    icon: SignalIcon,
    color: priorityColors.high,
  },
  {
    label: "Critical",
    value: "critical",
    icon: AlertTriangleIcon,
    color: priorityColors.critical,
  },
  {
    label: "Other",
    value: "other",
    icon: CircleIcon,
    color: priorityColors.other,
  },
]

const severityColors: Record<CaseSeverity, string> = {
  unknown: "bg-blue-100/70 border-blue-600/70 text-blue-700/80",
  informational: "bg-yellow-100/80 border-yellow-600/70 text-yellow-700/80",
  low: "bg-yellow-100/80 border-yellow-600/70 text-yellow-700/80",
  medium: "bg-orange-100 border-orange-600 text-orange-700",
  high: "bg-red-100 border-red-400 text-red-700",
  critical: "bg-fuchsia-100 border-fuchsia-400 text-fuchsia-700",
  fatal: "bg-red-100 border-red-400 text-red-700",
  other: "bg-red-100 border-red-400 text-red-700",
}

export const SEVERITIES: CaseBadgeProps<CaseSeverity>[] = [
  {
    label: "Unknown",
    value: "unknown",
    icon: CircleIcon,
    color: severityColors.unknown,
  },
  {
    label: "Informational",
    value: "informational",
    icon: SignalMediumIcon,
    color: severityColors.informational,
  },
  {
    label: "Low",
    value: "low",
    icon: SignalMediumIcon,
    color: severityColors.low,
  },
  {
    label: "Medium",
    value: "medium",
    icon: SignalHighIcon,
    color: severityColors.medium,
  },
  {
    label: "High",
    value: "high",
    icon: SignalIcon,
    color: severityColors.high,
  },
  {
    label: "Critical",
    value: "critical",
    icon: AlertTriangleIcon,
    color: severityColors.critical,
  },
  {
    label: "Fatal",
    value: "fatal",
    icon: AlertTriangleIcon,
    color: severityColors.fatal,
  },
  {
    label: "Other",
    value: "other",
    icon: CircleIcon,
    color: severityColors.other,
  },
]
