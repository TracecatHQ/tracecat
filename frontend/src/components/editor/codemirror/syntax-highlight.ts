import { HighlightStyle } from "@codemirror/language"
import { tags } from "@lezer/highlight"

/** Syntax colors shared by the workflow builder editors and JSON field editors. */
export const tracecatSyntaxHighlightStyle = HighlightStyle.define([
  { tag: tags.content, color: "hsl(var(--syntax-content))" },
  {
    tag: tags.propertyName,
    color: "hsl(var(--syntax-property))",
    fontWeight: "500",
  },
  { tag: tags.string, color: "hsl(var(--syntax-string))" },
  { tag: tags.number, color: "hsl(var(--syntax-number))" },
  { tag: tags.bool, color: "hsl(var(--syntax-literal))" },
  { tag: tags.null, color: "hsl(var(--syntax-literal))" },
  { tag: tags.atom, color: "hsl(var(--syntax-literal))", fontWeight: "600" },
  { tag: tags.keyword, color: "hsl(var(--syntax-literal))" },
  {
    tag: tags.comment,
    color: "hsl(var(--syntax-comment))",
    fontStyle: "italic",
  },
  {
    tag: [tags.punctuation, tags.bracket, tags.brace],
    color: "hsl(var(--syntax-content))",
  },
])
