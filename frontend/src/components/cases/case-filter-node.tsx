"use client"

import { format } from "date-fns"
import { useRef, useState } from "react"
import type { CaseFilterField, Condition, Filter } from "@/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  childrenOf,
  includeUnsetCondition,
  newCondition,
  OP_LABELS,
} from "./case-filter-utils"

const SELECT_CLASS =
  "h-8 rounded-md border border-input bg-background px-2 text-xs focus:ring-1 focus:ring-inset focus:ring-ring"
interface NodeProps {
  node: Filter
  onChange: (node: Filter) => void
  onRemove: () => void
  fields: CaseFilterField[]
  depth: number
  maxDepth: number
  canAdd: boolean
  choiceLabels?: Record<string, Record<string, string>>
}

/** Render one condition or explicit boolean group in the case query. */
export function FilterNode({
  node,
  onChange,
  onRemove,
  fields,
  depth,
  maxDepth,
  canAdd,
  choiceLabels = {},
}: NodeProps) {
  const unsetCondition = includeUnsetCondition(node)
  const condition = "field" in node ? node : unsetCondition
  const [payloadValueType, setPayloadValueType] = useState(() => {
    const type = typeof condition?.value
    return type === "number" || type === "boolean" ? type : "text"
  })
  const childKeys = useRef(new WeakMap<Filter, number>())
  const nextChildKey = useRef(0)
  function childKey(child: Filter): number {
    const existing = childKeys.current.get(child)
    if (existing !== undefined) return existing
    const key = nextChildKey.current++
    childKeys.current.set(child, key)
    return key
  }
  if (condition) {
    const field = fields.find(
      (item) =>
        item.field === condition.field ||
        (item.kind === "json_scalar" && condition.field.startsWith("payload."))
    )
    function update(next: Condition, includeUnset = Boolean(unsetCondition)) {
      onChange(
        includeUnset && next.op !== "is_null"
          ? { or: [next, { field: next.field, op: "is_null" }] }
          : next
      )
    }
    let valueType = "text"
    if (field?.kind === "json_scalar") {
      const actualType = typeof condition.value
      if (actualType === "number" || actualType === "boolean")
        valueType = actualType
      else if (condition.value === "" || condition.value == null)
        valueType = payloadValueType
    }
    let choices = field?.choices
    if (field?.kind === "boolean" || valueType === "boolean")
      choices = ["true", "false"]
    const isList = condition.op === "in" || condition.op === "not_in"
    let inputType = field?.input_type ?? "text"
    if (valueType === "number") inputType = "number"
    if (isList) inputType = "text"
    function parseValue(raw: string): string | number | boolean {
      if (field?.kind === "boolean" || valueType === "boolean")
        return raw === "true"
      // Preserve numeric strings for the server's physical-type validation.
      // Payload numbers need a JSON number to retain equality type semantics.
      if (field?.kind === "json_scalar" && valueType === "number" && raw !== "")
        return Number(raw)
      if (inputType === "datetime-local" && raw !== "") {
        const instant = new Date(raw)
        if (!Number.isNaN(instant.getTime())) return instant.toISOString()
      }
      return raw
    }
    function displayValue(raw: Condition["value"]): string {
      if (Array.isArray(raw)) return raw.join(", ")
      const value = String(raw ?? "")
      if (inputType === "datetime-local" && value !== "") {
        const instant = new Date(value)
        if (!Number.isNaN(instant.getTime()))
          return format(instant, "yyyy-MM-dd'T'HH:mm:ss.SSS")
      }
      return value
    }
    return (
      <div className="flex flex-wrap items-center gap-2 py-1">
        <select
          aria-label="Condition field"
          className={SELECT_CLASS}
          value={field?.field ?? condition.field}
          onChange={(event) => {
            const selected = fields.find(
              (item) => item.field === event.target.value
            )
            if (!selected) return
            setPayloadValueType("text")
            const op = selected.operators.includes("eq")
              ? "eq"
              : selected.operators[0]
            let value: Condition["value"] = selected.choices?.[0] ?? ""
            if (selected.kind === "boolean") value = true
            if (op === "in") value = []
            update(
              {
                field:
                  selected.field === "payload.<path>"
                    ? "payload."
                    : selected.field,
                op,
                value: op === "is_null" ? null : value,
              },
              false
            )
          }}
        >
          {!field && (
            <option value={condition.field}>
              {condition.field} (unavailable)
            </option>
          )}
          {fields.map((item) => (
            <option key={item.field} value={item.field}>
              {item.label}
            </option>
          ))}
        </select>
        {field?.kind === "json_scalar" && (
          <Input
            required
            aria-label="Payload path"
            className="h-8 w-64 text-xs"
            placeholder="metadata.product.vendor_name"
            value={condition.field.slice("payload.".length)}
            onChange={(event) =>
              update({ ...condition, field: `payload.${event.target.value}` })
            }
          />
        )}
        <select
          aria-label="Condition operator"
          className={SELECT_CLASS}
          value={condition.op}
          onChange={(event) => {
            const op = event.target.value as Condition["op"]
            let value = condition.value
            if (op === "is_null") {
              if (field?.kind === "json_scalar") setPayloadValueType(valueType)
              value = null
            } else if (op === "in" || op === "not_in")
              value = Array.isArray(value) ? value : [value ?? ""]
            else if (Array.isArray(value)) value = value[0] ?? ""
            else if (value === null || value === undefined) {
              value = field?.choices?.[0] ?? ""
              if (field?.kind === "boolean" || valueType === "boolean")
                value = true
              else if (valueType === "number") value = 0
            }
            update({ ...condition, op, value })
          }}
        >
          {(!field || !field.operators.includes(condition.op)) && (
            <option value={condition.op}>
              {OP_LABELS[condition.op]} (unavailable)
            </option>
          )}
          {field?.operators.map((op) => (
            <option key={op} value={op}>
              {OP_LABELS[op]}
            </option>
          ))}
        </select>
        {condition.op !== "is_null" && (
          <>
            {field?.kind === "json_scalar" && (
              <select
                aria-label="Payload value type"
                className={SELECT_CLASS}
                value={valueType}
                onChange={(event) => {
                  setPayloadValueType(event.target.value)
                  let value: Condition["value"] = ""
                  if (event.target.value === "number") value = 0
                  if (event.target.value === "boolean") value = true
                  update({ ...condition, value })
                }}
              >
                <option value="text">Text</option>
                <option value="number">Number</option>
                <option value="boolean">Boolean</option>
              </select>
            )}
            {choices ? (
              <select
                aria-label="Condition value"
                multiple={isList}
                required
                className={SELECT_CLASS}
                value={
                  isList
                    ? (condition.value as string[])
                    : String(condition.value ?? "")
                }
                onChange={(event) =>
                  update({
                    ...condition,
                    value: isList
                      ? Array.from(
                          event.target.selectedOptions,
                          (option) => option.value
                        )
                      : parseValue(event.target.value),
                  })
                }
              >
                {/* Keep removed options visible so a saved filter never changes silently. */}
                {(Array.isArray(condition.value)
                  ? condition.value
                  : [condition.value]
                )
                  .filter(
                    (value) => value != null && !choices.includes(String(value))
                  )
                  .map((value) => (
                    <option key={String(value)} value={String(value)}>
                      {String(value)} (unavailable)
                    </option>
                  ))}
                {choices.map((value) => (
                  <option key={value} value={value}>
                    {choiceLabels[condition.field]?.[value] ?? value}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                aria-label="Condition value"
                className="h-8 w-48 text-xs"
                type={inputType}
                step="any"
                required={inputType !== "text"}
                placeholder={isList ? "Comma-separated values" : "Value"}
                value={displayValue(condition.value)}
                onChange={(event) =>
                  update({
                    ...condition,
                    value: isList
                      ? event.target.value
                          .split(",")
                          .map((value) => value.trim())
                      : parseValue(event.target.value),
                  })
                }
              />
            )}
            {field?.operators.includes("is_null") && (
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={Boolean(unsetCondition)}
                  disabled={!unsetCondition && (depth >= maxDepth || !canAdd)}
                  onChange={(event) => update(condition, event.target.checked)}
                />
                Include unset
              </label>
            )}
          </>
        )}
        {!field && (
          <span className="text-xs text-destructive">
            This field is unavailable. Edit or remove this condition.
          </span>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label="Remove condition"
          onClick={onRemove}
        >
          Remove
        </Button>
      </div>
    )
  }
  const children = childrenOf(node)
  const mode = "and" in node ? "all" : "or" in node ? "any" : "not"
  function replaceChildren(next: Filter[]) {
    if (next.length === 0) {
      onRemove()
      return
    }
    if (mode === "not") onChange({ not: next[0] })
    else onChange(mode === "all" ? { and: next } : { or: next })
  }
  return (
    <div className="space-y-2 border-l border-border pl-3">
      <div className="flex items-center gap-2">
        {mode === "not" ? (
          <span className="text-xs">Not</span>
        ) : (
          <select
            aria-label="Group matching"
            className={SELECT_CLASS}
            value={mode}
            onChange={(event) =>
              onChange(
                event.target.value === "all"
                  ? { and: children }
                  : { or: children }
              )
            }
          >
            <option value="all">All conditions</option>
            <option value="any">Any condition</option>
          </select>
        )}
        <Button
          type="button"
          variant="ghost"
          size="sm"
          aria-label="Remove group"
          onClick={onRemove}
        >
          Remove group
        </Button>
      </div>
      {children.map((child, index) => (
        <FilterNode
          key={childKey(child)}
          node={child}
          fields={fields}
          depth={depth + 1}
          maxDepth={maxDepth}
          canAdd={canAdd}
          choiceLabels={choiceLabels}
          onChange={(next) => {
            childKeys.current.set(next, childKey(child))
            replaceChildren(
              children.map((item, i) => (i === index ? next : item))
            )
          }}
          onRemove={() =>
            replaceChildren(children.filter((_, i) => i !== index))
          }
        />
      ))}
      {mode !== "not" && (
        <div className="flex gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!canAdd || depth >= maxDepth}
            onClick={() => replaceChildren([...children, newCondition()])}
          >
            Add condition
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={!canAdd || depth + 1 >= maxDepth}
            onClick={() =>
              replaceChildren([...children, { and: [newCondition()] }])
            }
          >
            Add group
          </Button>
        </div>
      )}
    </div>
  )
}
