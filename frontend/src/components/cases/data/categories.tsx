import {
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowRightIcon,
  ArrowUpIcon,
  CheckCircleIcon,
  CircleIcon,
  FlagTriangleRightIcon,
  InfoIcon,
  ShieldAlertIcon,
  ShieldOffIcon,
  TrafficConeIcon,
} from "lucide-react"

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

export const priorities = [
  {
    label: "Low",
    value: "low",
    icon: ArrowDownIcon,
  },
  {
    label: "Medium",
    value: "medium",
    icon: ArrowRightIcon,
  },
  {
    label: "High",
    value: "high",
    icon: ArrowUpIcon,
  },
  {
    label: "Critical",
    value: "critical",
    icon: AlertTriangleIcon,
  },
]

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
