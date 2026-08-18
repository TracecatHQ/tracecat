"use client"

import type { CaseFieldRead, CaseUpdate } from "@/client"
import { CASE_PANEL_GROUP_LABEL_CLASS } from "@/components/cases/case-panel-common"
import { CustomField } from "@/components/cases/case-panel-custom-fields"
import {
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
} from "@/components/ui/sidebar"

interface CasePanelFieldsGroupProps {
  customFields: CaseFieldRead[]
  visibleCustomFields: CaseFieldRead[]
  showAll: boolean
  onToggleShowAll: () => void
  updateCase: (caseUpdate: Partial<CaseUpdate>) => Promise<void>
  rowClassName: string
  labelClassName: string
  controlClassName: string
  inputClassName?: string
  onRowClick: (event: React.MouseEvent<HTMLDivElement>) => void
}

/**
 * The "Fields" sidebar group of the case panel: one row per custom field,
 * with a toggle between showing all fields and hiding empty ones. Every row
 * starts with its label span so labels align identically in both modes.
 */
export function CasePanelFieldsGroup({
  customFields,
  visibleCustomFields,
  showAll,
  onToggleShowAll,
  updateCase,
  rowClassName,
  labelClassName,
  controlClassName,
  inputClassName,
  onRowClick,
}: CasePanelFieldsGroupProps) {
  return (
    // mt-4 opens the section gap after Properties in both the docked rail and
    // the stacked layout, which wrap this group in different parents (neither
    // of which applies a flex gap between the groups).
    <SidebarGroup className="mt-4">
      <SidebarGroupLabel className={CASE_PANEL_GROUP_LABEL_CLASS}>
        Fields
      </SidebarGroupLabel>
      <SidebarGroupContent className="px-2">
        {/* gap-1, not gap-2: grouping is read from the ratio of between-section
            to within-section whitespace. A 4px row gap against the ~32px
            section gap gives ~8:1, so the sections separate clearly without
            spending more vertical space. Keep in sync with the Properties
            group in `case-panel-view.tsx`. */}
        <div className="flex flex-col gap-1">
          {visibleCustomFields.map((field) => {
            const label = field.display_name
            return (
              <div key={field.id} className={rowClassName} onClick={onRowClick}>
                <span className={labelClassName} title={label}>
                  {label}
                </span>
                <div className={controlClassName}>
                  <div className="flex h-7 w-full items-center gap-2">
                    <div className="min-w-0 flex-1">
                      <CustomField
                        customField={field}
                        updateCase={updateCase}
                        formClassName="w-full min-w-0 max-w-full"
                        inputClassName={inputClassName}
                      />
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
          {customFields.length === 0 && (
            <span className="text-sm text-muted-foreground">
              No custom fields configured
            </span>
          )}
          {customFields.length > 0 && (
            <button
              type="button"
              className="h-7 text-left text-sm text-muted-foreground underline-offset-4 hover:underline"
              onClick={onToggleShowAll}
            >
              {showAll ? "Hide empty fields" : "View all fields"}
            </button>
          )}
        </div>
      </SidebarGroupContent>
    </SidebarGroup>
  )
}
