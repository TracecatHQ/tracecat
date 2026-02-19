import {
  groupEventsByActionRef,
  WF_COMPLETED_EVENT_REF,
  WF_FAILURE_EVENT_REF,
  type WorkflowExecutionEventCompact,
} from "@/lib/event-history"

describe("event-history", () => {
  describe("WF_FAILURE_EVENT_REF", () => {
    it("should export the correct sentinel value", () => {
      expect(WF_FAILURE_EVENT_REF).toBe("__workflow_failure__")
    })
  })

  describe("WF_COMPLETED_EVENT_REF", () => {
    it("should export the correct sentinel value", () => {
      expect(WF_COMPLETED_EVENT_REF).toBe("__workflow_completed__")
    })
  })

  describe("groupEventsByActionRef", () => {
    it("should group regular events by their action_ref", () => {
      const events: WorkflowExecutionEventCompact[] = [
        {
          source_event_id: 1,
          schedule_time: "2023-01-01T10:00:00Z",
          start_time: "2023-01-01T10:00:00Z",
          close_time: null,
          curr_event_type: "ACTIVITY_TASK_COMPLETED",
          status: "COMPLETED",
          action_name: "Send Email",
          action_ref: "send_email",
          action_input: null,
          action_result: null,
        },
        {
          source_event_id: 2,
          schedule_time: "2023-01-01T10:00:01Z",
          start_time: "2023-01-01T10:00:01Z",
          close_time: null,
          curr_event_type: "ACTIVITY_TASK_STARTED",
          status: "STARTED",
          action_name: "Send Email",
          action_ref: "send_email",
          action_input: null,
          action_result: null,
        },
        {
          source_event_id: 3,
          schedule_time: "2023-01-01T10:00:02Z",
          start_time: "2023-01-01T10:00:02Z",
          close_time: null,
          curr_event_type: "ACTIVITY_TASK_COMPLETED",
          status: "COMPLETED",
          action_name: "Get Data",
          action_ref: "get_data",
          action_input: null,
          action_result: null,
        },
      ]

      const result = groupEventsByActionRef(events)

      expect(result.send_email).toHaveLength(2)
      expect(result.send_email[0].action_ref).toBe("send_email")
      expect(result.send_email[1].action_ref).toBe("send_email")
      expect(result.get_data).toHaveLength(1)
      expect(result.get_data[0].action_ref).toBe("get_data")
    })

    it("should group workflow sentinel events by their raw action_ref", () => {
      const events: WorkflowExecutionEventCompact[] = [
        {
          source_event_id: 1,
          schedule_time: "2023-01-01T10:00:00Z",
          start_time: "2023-01-01T10:00:00Z",
          close_time: null,
          curr_event_type: "WORKFLOW_EXECUTION_FAILED",
          status: "FAILED",
          action_name: "Workflow",
          action_ref: WF_FAILURE_EVENT_REF,
          action_input: null,
          action_result: null,
        },
        {
          source_event_id: 2,
          schedule_time: "2023-01-01T10:00:01Z",
          start_time: "2023-01-01T10:00:01Z",
          close_time: null,
          curr_event_type: "WORKFLOW_EXECUTION_STARTED",
          status: "STARTED",
          action_name: "Workflow",
          action_ref: WF_FAILURE_EVENT_REF,
          action_input: null,
          action_result: null,
        },
        {
          source_event_id: 3,
          schedule_time: "2023-01-01T10:00:02Z",
          start_time: "2023-01-01T10:00:02Z",
          close_time: null,
          curr_event_type: "ACTIVITY_TASK_COMPLETED",
          status: "COMPLETED",
          action_name: "Regular Action",
          action_ref: "regular_action",
          action_input: null,
          action_result: null,
        },
      ]

      const result = groupEventsByActionRef(events)

      expect(result[WF_FAILURE_EVENT_REF]).toHaveLength(2)
      expect(result[WF_FAILURE_EVENT_REF][0].action_ref).toBe(
        WF_FAILURE_EVENT_REF
      )
      expect(result[WF_FAILURE_EVENT_REF][1].action_ref).toBe(
        WF_FAILURE_EVENT_REF
      )
      expect(result.regular_action).toHaveLength(1)
      expect(result.regular_action[0].action_ref).toBe("regular_action")
    })

    it("should handle empty events array", () => {
      const events: WorkflowExecutionEventCompact[] = []
      const result = groupEventsByActionRef(events)
      expect(result).toEqual({})
    })

    it("should handle mix of workflow and regular events", () => {
      const events: WorkflowExecutionEventCompact[] = [
        {
          source_event_id: 1,
          schedule_time: "2023-01-01T10:00:00Z",
          start_time: "2023-01-01T10:00:00Z",
          close_time: null,
          curr_event_type: "ACTIVITY_TASK_COMPLETED",
          status: "COMPLETED",
          action_name: "Regular Action",
          action_ref: "regular_action",
          action_input: null,
          action_result: null,
        },
        {
          source_event_id: 2,
          schedule_time: "2023-01-01T10:00:01Z",
          start_time: "2023-01-01T10:00:01Z",
          close_time: null,
          curr_event_type: "WORKFLOW_EXECUTION_FAILED",
          status: "FAILED",
          action_name: "Workflow",
          action_ref: WF_FAILURE_EVENT_REF,
          action_input: null,
          action_result: null,
        },
        {
          source_event_id: 3,
          schedule_time: "2023-01-01T10:00:02Z",
          start_time: "2023-01-01T10:00:02Z",
          close_time: null,
          curr_event_type: "ACTIVITY_TASK_STARTED",
          status: "STARTED",
          action_name: "Another Action",
          action_ref: "another_action",
          action_input: null,
          action_result: null,
        },
        {
          source_event_id: 4,
          schedule_time: "2023-01-01T10:00:03Z",
          start_time: "2023-01-01T10:00:03Z",
          close_time: null,
          curr_event_type: "WORKFLOW_EXECUTION_COMPLETED",
          status: "COMPLETED",
          action_name: "Workflow",
          action_ref: WF_FAILURE_EVENT_REF,
          action_input: null,
          action_result: null,
        },
      ]

      const result = groupEventsByActionRef(events)

      expect(result.regular_action).toHaveLength(1)
      expect(result.regular_action[0].action_ref).toBe("regular_action")

      expect(result[WF_FAILURE_EVENT_REF]).toHaveLength(2)
      expect(result[WF_FAILURE_EVENT_REF][0].action_ref).toBe(
        WF_FAILURE_EVENT_REF
      )
      expect(result[WF_FAILURE_EVENT_REF][1].action_ref).toBe(
        WF_FAILURE_EVENT_REF
      )

      expect(result.another_action).toHaveLength(1)
      expect(result.another_action[0].action_ref).toBe("another_action")
    })

    it("should handle multiple events with same action_ref including duplicates", () => {
      const events: WorkflowExecutionEventCompact[] = [
        {
          source_event_id: 1,
          schedule_time: "2023-01-01T10:00:00Z",
          start_time: "2023-01-01T10:00:00Z",
          close_time: null,
          curr_event_type: "ACTIVITY_TASK_STARTED",
          status: "STARTED",
          action_name: "Action A",
          action_ref: "action_a",
          action_input: null,
          action_result: null,
        },
        {
          source_event_id: 2,
          schedule_time: "2023-01-01T10:00:01Z",
          start_time: "2023-01-01T10:00:01Z",
          close_time: null,
          curr_event_type: "ACTIVITY_TASK_COMPLETED",
          status: "COMPLETED",
          action_name: "Action A",
          action_ref: "action_a",
          action_input: null,
          action_result: null,
        },
        {
          source_event_id: 3,
          schedule_time: "2023-01-01T10:00:02Z",
          start_time: "2023-01-01T10:00:02Z",
          close_time: null,
          curr_event_type: "ACTIVITY_TASK_FAILED",
          status: "FAILED",
          action_name: "Action A",
          action_ref: "action_a",
          action_input: null,
          action_result: null,
        },
      ]

      const result = groupEventsByActionRef(events)

      expect(result.action_a).toHaveLength(3)
      expect(result.action_a[0].source_event_id).toBe(1)
      expect(result.action_a[1].source_event_id).toBe(2)
      expect(result.action_a[2].source_event_id).toBe(3)
    })
  })
})
