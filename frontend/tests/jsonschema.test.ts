import { JSONSchema7 } from "json-schema"

import { getType, jsonSchemaToParams } from "@/lib/jsonschema"

const jsonSchema: JSONSchema7 = {
  properties: {
    url: {
      description: "The destination of the HTTP request",
      format: "uri",
      maxLength: 100,
      minLength: 1,
      title: "Url",
      type: "string",
    },
    headers: {
      additionalProperties: {
        type: "string",
      },
      default: null,
      description: "HTTP request headers",
      title: "Headers",
      type: "object",
    },
    payload: {
      anyOf: [
        {
          type: "object",
        },
        {
          items: {},
          type: "array",
        },
      ],
      default: null,
      description: "HTTP request payload",
      title: "Payload",
    },
    params: {
      default: null,
      description: "URL query parameters",
      title: "Params",
      type: "object",
    },
    method: {
      default: "GET",
      description: "HTTP reqest method",
      enum: ["GET", "POST", "PUT", "DELETE"],
      title: "Method",
      type: "string",
    },
  },
  required: ["url"],
  title: "CoreHttpRequest",
  type: "object",
}

describe("jsonSchemaToParams", () => {
  it("should transform a JSON schema to table params", () => {
    const params = jsonSchemaToParams(jsonSchema)
    expect(params).toEqual([
      {
        parameter: "url",
        type: "string",
        default: "-",
        description: "The destination of the HTTP request",
        constraints: "format: uri\nmaxLength: 100\nminLength: 1",
        required: true,
      },
      {
        parameter: "headers",
        type: "object",
        default: "-",
        description: "HTTP request headers",
        constraints: "additionalProperties: string",
        required: false,
      },
      {
        parameter: "payload",
        type: "object | array",
        default: "-",
        description: "HTTP request payload",
        constraints: "",
        required: false,
      },
      {
        parameter: "params",
        type: "object",
        default: "-",
        description: "URL query parameters",
        constraints: "",
        required: false,
      },
      {
        parameter: "method",
        type: '"GET" | "POST" | "PUT" | "DELETE"',
        default: '"GET"',
        description: "HTTP reqest method",
        constraints: 'enum: ["GET", "POST", "PUT", "DELETE"]',
        required: false,
      },
    ])
  })
})

describe("getType", () => {
  it("should handle anyOf with enum values and null type (Python's Literal | None)", () => {
    const schema: JSONSchema7 = {
      anyOf: [
        {
          enum: ["a", "b", "c"],
          type: "string",
        },
        {
          type: "null",
        },
      ],
    }

    expect(getType(schema)).toBe('"a" | "b" | "c" | null')
  })

  it("should handle regular enum types", () => {
    const schema: JSONSchema7 = {
      enum: ["GET", "POST", "PUT", "DELETE"],
      type: "string",
    }

    expect(getType(schema)).toBe('"GET" | "POST" | "PUT" | "DELETE"')
  })

  it("should handle regular primitive types", () => {
    const schema: JSONSchema7 = {
      type: "string",
    }

    expect(getType(schema)).toBe("string")
  })

  it("should handle nested anyOf structures", () => {
    const schema: JSONSchema7 = {
      anyOf: [
        {
          type: "string",
        },
        {
          anyOf: [
            {
              type: "number",
            },
            {
              type: "null",
            },
          ],
        },
      ],
    }

    expect(getType(schema)).toBe("string | number | null")
  })

  it("should handle oneOf structures", () => {
    const schema: JSONSchema7 = {
      oneOf: [
        {
          type: "string",
        },
        {
          type: "number",
        },
      ],
    }

    expect(getType(schema)).toBe("string | number")
  })

  it("should handle allOf structures", () => {
    const schema: JSONSchema7 = {
      allOf: [
        {
          type: "object",
        },
        {
          type: "object",
        },
      ],
    }

    expect(getType(schema)).toBe("object & object")
  })

  it("should handle type as array", () => {
    const schema: JSONSchema7 = {
      type: ["string", "number", "null"],
    }

    expect(getType(schema)).toBe("string | number | null")
  })
})
