import {
  AlertTriangleIcon,
  CheckCircleIcon,
  CircleIcon,
  FlagTriangleRightIcon,
  InfoIcon,
  LucideIcon,
  ShieldAlertIcon,
  ShieldOffIcon,
  SignalHighIcon,
  SignalIcon,
  SignalMediumIcon,
  TrafficConeIcon,
} from "lucide-react"

import { CasePriorityType } from "@/types/schemas"

export const statuses = [
  {
    value: "open",
    label: "Open",
    icon: CircleIcon,
  },
  {
    value: "closed",
    label: "Closed",
    icon: CheckCircleIcon,
  },
  {
    value: "in_progress",
    label: "In Progress",
    icon: TrafficConeIcon,
  },
  {
    value: "reported",
    label: "Reported",
    icon: FlagTriangleRightIcon,
  },
  {
    value: "escalated",
    label: "Escalated",
    icon: ShieldAlertIcon,
  },
]
export type Status = (typeof statuses)[number]["value"]

export const priorities: {
  label: string
  value: CasePriorityType
  icon: LucideIcon
}[] = [
  {
    label: "Low",
    value: "low",
    icon: SignalMediumIcon,
  },
  {
    label: "Medium",
    value: "medium",
    icon: SignalHighIcon,
  },
  {
    label: "High",
    value: "high",
    icon: SignalIcon,
  },
  {
    label: "Critical",
    value: "critical",
    icon: AlertTriangleIcon,
  },
]
export type Priority = (typeof priorities)[number]["value"]

export const indicators = [
  {
    label: "Malicious",
    value: "malicious",
    icon: ShieldOffIcon,
  },
  {
    label: "Benign",
    value: "benign",
    icon: InfoIcon,
  },
]
export type Indicator = (typeof indicators)[number]["value"]
