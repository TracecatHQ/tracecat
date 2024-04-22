import { TimelineItemProps } from "@/components/timeline"

const baseTimelineItemProps = {
  user: {
    src: "https://avatars.githubusercontent.com/u/5508348",
    name: "Daryl Lim",
  },
  updatedAt: "Just now",
}
export const timelineItems: TimelineItemProps[] = [
  {
    ...baseTimelineItemProps,
    action: "opened_case",
  },
  {
    ...baseTimelineItemProps,
    action: "changed_status",
    activity: {
      status: "open",
    },
  },
  {
    ...baseTimelineItemProps,
    action: "changed_priority",
    activity: {
      priority: "critical",
    },
  },
  {
    ...baseTimelineItemProps,
    action: "added_comment",
    detail: "This is a comment",
  },
  {
    ...baseTimelineItemProps,
    action: "closed_case",
  },
]
