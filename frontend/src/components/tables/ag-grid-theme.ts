import { themeQuartz } from "ag-grid-community"

export const tracecatTheme = themeQuartz.withParams({
  backgroundColor: "hsl(var(--background))",
  foregroundColor: "hsl(var(--foreground))",
  borderColor: "hsl(var(--border))",
  headerBackgroundColor: "hsl(var(--muted))",
  headerTextColor: "hsl(var(--muted-foreground))",
  accentColor: "hsl(var(--primary))",
  selectedRowBackgroundColor: "hsl(var(--accent))",
  headerFontSize: 12,
  fontSize: 13,
  spacing: 6,
  wrapperBorderRadius: 0,
  wrapperBorder: false,
  cellHorizontalPadding: 12,
  popupShadow: "none",
})
