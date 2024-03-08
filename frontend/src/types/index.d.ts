export type SiteConfig = {
  name: string
  author: string
  description: string
  keywords: Array<string>
  url: {
    base: string
    author: string
  }
  links: {
    github: string
  }
  ogImage: string
}

export type ActionType =
  | "webhook"
  | "http_request"
  | "data_transform"
  | "condition.compare"
  | "condition.regex"
  | "condition.membership"
  | "open_case"
  | "receive_email"
  | "send_email"
  | "llm.extract"
  | "llm.label"
  | "llm.translate"
  | "llm.choice"
  | "llm.summarize"
