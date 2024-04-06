import {
  Blend,
  BookText,
  CheckSquare,
  Container,
  FlaskConical,
  GitCompareArrows,
  Globe,
  Languages,
  LucideIcon,
  Mail,
  Regex,
  Send,
  ShieldAlert,
  Sparkles,
  Split,
  Tags,
  Webhook,
} from "lucide-react"

import { ActionType } from "@/types/schemas"

export type ActionTile = {
  type?: ActionType
  title?: string
  icon: LucideIcon
  variant: "default" | "ghost"
  hierarchy?: "groupItem" | "group"
  availability?: "comingSoon"
}

export const actionTiles: ActionTile[] = [
  {
    type: "webhook",
    title: "Webhook",
    icon: Webhook,
    variant: "ghost",
  },
  {
    type: "http_request",
    title: "HTTP Request",
    icon: Globe,
    variant: "ghost",
  },
  {
    type: "data_transform",
    title: "Data Transform",
    icon: Blend,
    variant: "ghost",
    availability: "comingSoon",
  },
  {
    title: "Condition",
    icon: Split,
    variant: "ghost",
    hierarchy: "group",
  },
  {
    type: "condition.compare",
    title: "Compare",
    icon: GitCompareArrows,
    variant: "ghost",
    hierarchy: "groupItem",
  },
  {
    type: "condition.regex",
    title: "Regex",
    icon: Regex,
    variant: "ghost",
    hierarchy: "groupItem",
  },
  {
    type: "condition.membership",
    title: "Membership",
    icon: Container,
    variant: "ghost",
    hierarchy: "groupItem",
    availability: "comingSoon",
  },
  {
    type: "open_case",
    title: "Open Case",
    icon: ShieldAlert,
    variant: "ghost",
  },
  {
    type: "receive_email",
    title: "Receive Email",
    icon: Mail,
    variant: "ghost",
    availability: "comingSoon",
  },
  {
    type: "send_email",
    title: "Send Email",
    icon: Send,
    variant: "ghost",
  },
  {
    title: "AI Actions",
    icon: Sparkles,
    variant: "ghost",
    hierarchy: "group",
  },
  {
    type: "llm.extract",
    title: "Extract",
    icon: FlaskConical,
    variant: "ghost",
    hierarchy: "groupItem",
  },
  {
    type: "llm.label",
    title: "Label",
    icon: Tags,
    variant: "ghost",
    hierarchy: "groupItem",
  },
  {
    type: "llm.translate",
    title: "Translate",
    icon: Languages,
    variant: "ghost",
    hierarchy: "groupItem",
  },
  {
    type: "llm.choice",
    title: "Choice",
    icon: CheckSquare,
    variant: "ghost",
    hierarchy: "groupItem",
  },
  {
    type: "llm.summarize",
    title: "Summarize",
    icon: BookText,
    variant: "ghost",
    hierarchy: "groupItem",
  },
]
