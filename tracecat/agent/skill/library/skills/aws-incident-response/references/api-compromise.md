# API compromise

Use for suspicious application requests, WAF signals, injection, authorization
failures, or suspected data extraction through an AWS-hosted API.

## Use the right evidence source

Establish the request path: front door/WAF, API Gateway or load balancer,
authorizer, application, and data store. Locate available access, WAF, application,
and downstream audit logs. Correlate request IDs and authenticated principals
when present; account for trusted-proxy configuration before attributing an IP.

CloudTrail events for API Gateway describe management operations such as API or
stage configuration changes. They do not supply the application's client-request
history. Use request logs for exploitation and CloudTrail for control-plane
tampering. An HTTP 200, WAF match, or burst of errors alone cannot establish
unauthorized data access; examine authorization context and application effects.

For object-level authorization incidents, compare the authenticated principal
with the accessed object's ownership or authorization decision. Preserve only
the evidence necessary to demonstrate the unauthorized access; do not collect
additional sensitive records just to illustrate impact.

## Propose effective, bounded containment

Determine whether containment belongs at the request layer, identity layer, or
application authorization boundary. An IP block may reduce one source's traffic
without fixing an authorization defect. Shared NAT/proxy addresses may include
legitimate callers; account for that impact and alternative request attributes.

Creating a WAF IP set alone does not block requests. A blocking rule must
reference it in the web ACL protecting the affected resource. Before an approved
ACL change, read the current rules and concurrency token, preserve unrelated
rules, check rule priority and action, and account for regional versus CloudFront
scope. Verify the association and propagation, then observe the intended request
being blocked and legitimate traffic still succeeding. Do not overwrite an ACL
from a partial inventory or automatically repeat a stale update.

Treat throttling as mitigation with capacity impact, not an authorization fix.
Check the API type and configured operation schema before suggesting stage or
route changes. When an identity or token is implicated, verify the relevant
service's revocation semantics instead of promising all sessions are invalidated.

## Recovery and handoff

Verify the underlying authorization or input-handling fix, the malicious request
path, legitimate traffic, and any confirmed configuration persistence. Record
temporary containment and the conditions for removing it. Include the affected
routes, identities and objects, request/effect evidence, suspected exposure window,
and gaps such as absent access logging or unavailable application audit records.

## Authoritative references

- [API Gateway management events in CloudTrail](https://docs.aws.amazon.com/apigateway/latest/developerguide/cloudtrail.html).
- [API Gateway execution and access logging](https://docs.aws.amazon.com/apigateway/latest/developerguide/set-up-logging.html).
- [WAF IP sets and their use in rules](https://docs.aws.amazon.com/waf/latest/developerguide/waf-ip-set-managing.html).
