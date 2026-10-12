import type { ComponentType } from "react"
import type { FormWidget } from "@/client"
import { CardsWidget } from "@/components/forms/widgets/cards"
import { DateWidget } from "@/components/forms/widgets/date"
import { DatetimeWidget } from "@/components/forms/widgets/datetime"
import { FileWidget } from "@/components/forms/widgets/file"
import { LongTextWidget } from "@/components/forms/widgets/long-text"
import { MultiSelectWidget } from "@/components/forms/widgets/multi-select"
import { NumberWidget } from "@/components/forms/widgets/number"
import { SelectWidget } from "@/components/forms/widgets/select"
import { TagsWidget } from "@/components/forms/widgets/tags"
import { TextWidget } from "@/components/forms/widgets/text"
import { ToggleWidget } from "@/components/forms/widgets/toggle"
import type { FormWidgetProps } from "@/components/forms/widgets/types"
import { UserWidget } from "@/components/forms/widgets/user"

/**
 * The component for every widget the backend can resolve a field to.
 *
 * Keyed by the generated `FormWidget` union, so a widget added on the backend
 * fails typecheck here until it has a component.
 */
export const FORM_WIDGETS: Record<
  FormWidget,
  ComponentType<FormWidgetProps>
> = {
  text: TextWidget,
  long_text: LongTextWidget,
  number: NumberWidget,
  toggle: ToggleWidget,
  select: SelectWidget,
  multi_select: MultiSelectWidget,
  cards: CardsWidget,
  tags: TagsWidget,
  date: DateWidget,
  datetime: DatetimeWidget,
  user: UserWidget,
  file: FileWidget,
}
