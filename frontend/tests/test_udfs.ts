import { UDF } from "@/lib/udf"

export const CoreHttpRequest: UDF = {
  args: {
    properties: {
      headers: {
        additionalProperties: {
          type: "string",
        },
        default: null,
        description: "HTTP request headers",
        title: "Headers",
        type: "object",
      },
      method: {
        default: "GET",
        description: "HTTP reqest method",
        enum: ["GET", "POST", "PUT", "DELETE"],
        type: "string",
      },
      payload: {
        default: null,
        description: "HTTP request payload",
        title: "Payload",
        type: "object",
      },
      url: {
        description: "The destination of the HTTP request",
        format: "uri",
        maxLength: 100,
        minLength: 1,
        title: "Url",
        type: "string",
      },
      testStringProperty: {
        description: "Test string property for the UDF schema",
        type: "string",
        title: "Test String Property",
      },
      testArrayProperty: {
        description: "Test array property for the UDF schema",
        type: "array",
        title: "Test Array Property",
      },
    },
    required: ["url", "testStringProperty", "testArrayProperty"],
    title: "CoreHttpRequest",
    type: "object",
  },
  description: "Perform a HTTP request to a given URL.",
  key: "core.http_request",
  metadata: {},
  namespace: "core",
  rtype: {
    properties: {
      data: {
        anyOf: [
          {
            type: "string",
          },
          {
            type: "object",
          },
        ],
        title: "Data",
      },
      headers: {
        additionalProperties: {
          type: "string",
        },
        title: "Headers",
        type: "object",
      },
      status_code: {
        title: "Status Code",
        type: "integer",
      },
    },
    required: ["status_code", "headers", "data"],
    title: "HTTPResponse",
    type: "object",
  },
  secrets: null,
  version: "0.1.0",
}

export const ProjectDiscoveryGetAllScanResults: UDF = {
  args: {
    properties: {
      limit: {
        anyOf: [
          {
            type: "integer",
          },
          {
            type: "null",
          },
        ],
        default: null,
        title: "Limit",
      },
      offset: {
        anyOf: [
          {
            type: "integer",
          },
          {
            type: "null",
          },
        ],
        default: null,
        title: "Offset",
      },
      search: {
        anyOf: [
          {
            type: "string",
          },
          {
            type: "null",
          },
        ],
        default: null,
        title: "Search",
      },
      severity: {
        enum: ["info", "low", "medium", "high", "critical", "none"],
        title: "Severity",
        type: "string",
      },
      time: {
        enum: ["last_day", "last_week", "last_month", "none"],
        title: "Time",
        type: "string",
      },
      vuln_status: {
        enum: ["open", "closedfalse_positive", "fixed", "none"],
        title: "Vuln Status",
        type: "string",
      },
    },
    required: ["severity", "time", "vuln_status"],
    title: "ProjectDiscoveryGetAllScanResults",
    type: "object",
  },
  description: "Get all scan results",
  key: "project_discovery.get_all_scan_results",
  metadata: {},
  namespace: "project_discovery",
  rtype: {
    type: "object",
  },
  secrets: ["project_discovery"],
  version: null,
}

export const DatadogListSecuritySignals = {
  args: {
    properties: {
      end: {
        anyOf: [
          {
            format: "date-time",
            type: "string",
          },
          {
            type: "null",
          },
        ],
        default: null,
        title: "End",
      },
      limit: {
        default: 100,
        title: "Limit",
        type: "integer",
      },
      query: {
        anyOf: [
          {
            type: "string",
          },
          {
            type: "null",
          },
        ],
        default: null,
        title: "Query",
      },
      region: {
        default: "us1",
        title: "Region",
        type: "string",
      },
      start: {
        anyOf: [
          {
            format: "date-time",
            type: "string",
          },
          {
            type: "null",
          },
        ],
        default: null,
        title: "Start",
      },
    },
    title: "DatadogListSecuritySignals",
    type: "object",
  },
  description:
    "Get Datadog SIEM security signals. Requires `security_monitoring_signals_read` scope.",
  key: "datadog.list_security_signals",
  metadata: {},
  namespace: "datadog",
  rtype: {
    items: {
      type: "object",
    },
    type: "array",
  },
  secrets: ["datadog-security-monitoring"],
  version: null,
}
