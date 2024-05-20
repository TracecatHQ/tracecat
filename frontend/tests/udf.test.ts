// tests/myFunction.test.ts
import { assert } from "console"
import { JSONSchemaType } from "ajv"

import {
  generateFormSchema,
  getFieldOptions,
  handleStringField,
} from "@/lib/udf"

import { CoreHttpRequest, ProjectDiscoveryGetAllScanResults } from "./test_udfs"

describe("generateFieldSchema", () => {
  it("should add minLength to required string fields", () => {
    const schema = CoreHttpRequest.args as JSONSchemaType<any>
    const processedSchema = generateFormSchema(schema)
    expect(processedSchema.properties.url.minLength).toBe(1)
    expect(processedSchema.properties.testStringProperty.minLength).toBe(1)
  })
})

describe("generateFieldSchema", () => {
  it("should add minLength to required array fields", () => {
    const schema = CoreHttpRequest.args as JSONSchemaType<any>
    const processedSchema = generateFormSchema(schema)
    expect(processedSchema.properties.url.minLength).toBe(1)
    expect(processedSchema.properties.testArrayProperty.minItems).toBe(1)
  })
})

describe("getFieldOptions", () => {
  it("Check proper parsing", () => {
    const args = ProjectDiscoveryGetAllScanResults.args as JSONSchemaType<any>
    Object.entries(args.properties).forEach(([key, value]) => {
      const fieldConfig = getFieldOptions(value as JSONSchemaType<any>)
      console.log(key, fieldConfig)
    })
  })
})

describe("getAnyOfFieldOptions", () => {
  it("Check proper parsing", () => {
    const args = ProjectDiscoveryGetAllScanResults.args as JSONSchemaType<any>

    const limitFieldConfig = getFieldOptions(args.properties.limit)
    expect(limitFieldConfig.kind).toBe("input")

    const searchFieldConfig = getFieldOptions(args.properties.search)
    expect(searchFieldConfig.kind).toBe("input")
  })
})

describe("handleStringField", () => {
  it("Datetime", () => {
    const fieldConfig = handleStringField({
      format: "date-time",
      type: "string",
    })
    expect(fieldConfig.kind).toBe("datetime")
    if (fieldConfig.kind !== "datetime") throw new Error("Never")
    expect(fieldConfig.inputType).toBe("datetime-local")
  })
  it("String Enum", () => {
    const fieldConfig = handleStringField({
      default: "GET",
      description: "HTTP reqest method",
      enum: ["GET", "POST", "PUT", "DELETE"],
      type: "string",
    })
    expect(fieldConfig.kind).toBe("select")
    if (fieldConfig.kind !== "select") throw new Error("Never")
    expect(fieldConfig?.options).toStrictEqual(["GET", "POST", "PUT", "DELETE"])
  })
  it("String", () => {
    const fieldConfig = handleStringField({
      type: "string",
    })
    expect(fieldConfig.kind).toBe("input")
  })
})
