"""Wazuh agents integration.

Wazuh supports multiple scans types:

* [CIS-CAT](https://documentation.wazuh.com/current/user-manual/api/reference.html#operation/api.controllers.ciscat_controller.get_agents_ciscat_results)
* [Rootcheck](https://documentation.wazuh.com/current/user-manual/api/reference.html#operation/api.controllers.rootcheck_controller.put_rootcheck)
* [SCA (Software Configuration Assessment)](https://documentation.wazuh.com/current/user-manual/api/reference.html#operation/api.controllers.sca_controller.get_sca_agent)
* [Syscheck (File Integrity Monitoring)](https://documentation.wazuh.com/current/user-manual/api/reference.html#operation/api.controllers.syscheck_controller.get_syscheck_agent)

Note: a few of the scans require the agent ID, which can be retrieved from the [agents list](https://documentation.wazuh.com/current/user-manual/api/reference.html#operation/api.controllers.agent_controller.get_agents).

Authentication method: JWT (with username / password basic auth to get the token)

Supported APIs:

```python
list_findings: {
    "endpoint": "/web/api/v2.1/cloud-detection/alerts",
    "method": "GET",
    "ocsf_schema": "array[vulnerability_finding]",
    "reference": "https://github.com/criblio/collector-templates/tree/main/collectors/rest/sentinel_one"
}
```
"""
