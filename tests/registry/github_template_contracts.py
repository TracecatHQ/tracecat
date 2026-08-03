"""Explicit endpoint contracts for the GitHub YAML template catalog."""

type EndpointContract = tuple[str, str, str, str]

CODE_ENDPOINTS: dict[str, EndpointContract] = {
    "get_authenticated_user": (
        "GET",
        "/user",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_repository": (
        "GET",
        "/repos/{owner}/{repo}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_repository": (
        "PATCH",
        "/repos/{owner}/{repo}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_organization_repositories": ("GET", "/orgs/{org}/repos", "read:org", "json"),
    "get_repository_content": (
        "GET",
        "/repos/{owner}/{repo}/contents/{path}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_or_update_repository_content": (
        "PUT",
        "/repos/{owner}/{repo}/contents/{path}",
        "repo (default; omit only for public "
        "repository data); workflow when modifying "
        ".github/workflows",
        "json",
    ),
    "delete_repository_content": (
        "DELETE",
        "/repos/{owner}/{repo}/contents/{path}",
        "repo (default; omit only for public repository data); "
        "workflow when modifying .github/workflows",
        "json",
    ),
    "get_git_tree": (
        "GET",
        "/repos/{owner}/{repo}/git/trees/{tree_sha}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_commits": (
        "GET",
        "/repos/{owner}/{repo}/commits",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_commit": (
        "GET",
        "/repos/{owner}/{repo}/commits/{ref}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "compare_commits": (
        "GET",
        "/repos/{owner}/{repo}/compare/{basehead}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_branches": (
        "GET",
        "/repos/{owner}/{repo}/branches",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_branch": (
        "GET",
        "/repos/{owner}/{repo}/branches/{branch}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
}

SEARCH_ENDPOINTS: dict[str, EndpointContract] = {
    "search_repositories": (
        "GET",
        "/search/repositories",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "search_code": (
        "GET",
        "/search/code",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "search_commits": (
        "GET",
        "/search/commits",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "search_issues": (
        "GET",
        "/search/issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
}

ISSUES_ENDPOINTS: dict[str, EndpointContract] = {
    "list_repository_issues": (
        "GET",
        "/repos/{owner}/{repo}/issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_issue": (
        "POST",
        "/repos/{owner}/{repo}/issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_issue": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_issue": (
        "PATCH",
        "/repos/{owner}/{repo}/issues/{issue_number}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_issue_comments": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/comments",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_issue_comment": (
        "POST",
        "/repos/{owner}/{repo}/issues/{issue_number}/comments",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_issue_comment": (
        "PATCH",
        "/repos/{owner}/{repo}/issues/comments/{comment_id}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_issue_timeline_events": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/timeline",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_labels": (
        "GET",
        "/repos/{owner}/{repo}/labels",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "add_issue_labels": (
        "POST",
        "/repos/{owner}/{repo}/issues/{issue_number}/labels",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "replace_issue_labels": (
        "PUT",
        "/repos/{owner}/{repo}/issues/{issue_number}/labels",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "remove_issue_label": (
        "DELETE",
        "/repos/{owner}/{repo}/issues/{issue_number}/labels/{name}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_issue_types": (
        "GET",
        "/repos/{owner}/{repo}/issue-types",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_sub_issues": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/sub_issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "add_sub_issue": (
        "POST",
        "/repos/{owner}/{repo}/issues/{issue_number}/sub_issues",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "remove_sub_issue": (
        "DELETE",
        "/repos/{owner}/{repo}/issues/{issue_number}/sub_issue",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "reprioritize_sub_issue": (
        "PATCH",
        "/repos/{owner}/{repo}/issues/{issue_number}/sub_issues/priority",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_blocked_by_dependencies": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_blocking_dependencies": (
        "GET",
        "/repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocking",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "add_blocked_by_dependency": (
        "POST",
        "/repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "remove_blocked_by_dependency": (
        "DELETE",
        "/repos/{owner}/{repo}/issues/{issue_number}/dependencies/blocked_by/{issue_id}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
}

PULL_REQUESTS_ENDPOINTS: dict[str, EndpointContract] = {
    "list_pull_requests": (
        "GET",
        "/repos/{owner}/{repo}/pulls",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_pull_request": (
        "POST",
        "/repos/{owner}/{repo}/pulls",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_pull_request": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_pull_request": (
        "PATCH",
        "/repos/{owner}/{repo}/pulls/{pull_number}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_pull_request_files": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/files",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_pull_request_commits": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/commits",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_pull_request_merge_state": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/merge",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "merge_pull_request": (
        "PUT",
        "/repos/{owner}/{repo}/pulls/{pull_number}/merge",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_pull_request_branch": (
        "PUT",
        "/repos/{owner}/{repo}/pulls/{pull_number}/update-branch",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_requested_reviewers": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "request_pull_request_reviewers": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "remove_requested_reviewers": (
        "DELETE",
        "/repos/{owner}/{repo}/pulls/{pull_number}/requested_reviewers",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_pull_request_reviews": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_pull_request_review": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/reviews",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "submit_pull_request_review": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}/events",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "dismiss_pull_request_review": (
        "PUT",
        "/repos/{owner}/{repo}/pulls/{pull_number}/reviews/{review_id}/dismissals",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_pull_request_review_comments": (
        "GET",
        "/repos/{owner}/{repo}/pulls/{pull_number}/comments",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_pull_request_review_comment": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/comments",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "reply_to_pull_request_review_comment": (
        "POST",
        "/repos/{owner}/{repo}/pulls/{pull_number}/comments/{comment_id}/replies",
        "repo (default; omit only for public repository data)",
        "json",
    ),
}

SECURITY_REPORTS_ENDPOINTS: dict[str, EndpointContract] = {
    "get_private_vulnerability_reporting": (
        "GET",
        "/repos/{owner}/{repo}/private-vulnerability-reporting",
        "repo (default)",
        "json",
    ),
    "enable_private_vulnerability_reporting": (
        "PUT",
        "/repos/{owner}/{repo}/private-vulnerability-reporting",
        "repo (default)",
        "json",
    ),
    "disable_private_vulnerability_reporting": (
        "DELETE",
        "/repos/{owner}/{repo}/private-vulnerability-reporting",
        "repo (default)",
        "json",
    ),
    "submit_private_vulnerability_report": (
        "POST",
        "/repos/{owner}/{repo}/security-advisories/reports",
        "repo (default)",
        "json",
    ),
    "list_repo_security_advisories": (
        "GET",
        "/repos/{owner}/{repo}/security-advisories",
        "repo (default)",
        "json",
    ),
    "create_repository_security_advisory": (
        "POST",
        "/repos/{owner}/{repo}/security-advisories",
        "repo (default)",
        "json",
    ),
    "get_repo_security_advisory": (
        "GET",
        "/repos/{owner}/{repo}/security-advisories/{ghsa_id}",
        "repo (default)",
        "json",
    ),
    "update_repo_security_advisory": (
        "PATCH",
        "/repos/{owner}/{repo}/security-advisories/{ghsa_id}",
        "repo (default)",
        "json",
    ),
    "request_security_advisory_cve": (
        "POST",
        "/repos/{owner}/{repo}/security-advisories/{ghsa_id}/cve",
        "repo (default)",
        "json",
    ),
    "create_security_advisory_fork": (
        "POST",
        "/repos/{owner}/{repo}/security-advisories/{ghsa_id}/forks",
        "repo (default)",
        "json",
    ),
    "list_organization_security_advisories": (
        "GET",
        "/orgs/{org}/security-advisories",
        "repo (default)",
        "json",
    ),
    "list_global_security_advisories": ("GET", "/advisories", "repo (default)", "json"),
    "get_global_security_advisory": (
        "GET",
        "/advisories/{ghsa_id}",
        "repo (default)",
        "json",
    ),
}

SECURITY_ENDPOINTS: dict[str, EndpointContract] = {
    "list_org_code_scanning_alerts": (
        "GET",
        "/orgs/{org}/code-scanning/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_repo_code_scanning_alerts": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_org_secret_scanning_alerts": (
        "GET",
        "/orgs/{org}/secret-scanning/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_repo_secret_scanning_alerts": (
        "GET",
        "/repos/{owner}/{repo}/secret-scanning/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_org_dependabot_alerts": (
        "GET",
        "/orgs/{org}/dependabot/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_repo_dependabot_alerts": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_code_scanning_alert": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "update_code_scanning_alert": (
        "PATCH",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_code_scanning_alert_instances": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}/instances",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_secret_scanning_alert": (
        "GET",
        "/repos/{owner}/{repo}/secret-scanning/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "update_secret_scanning_alert": (
        "PATCH",
        "/repos/{owner}/{repo}/secret-scanning/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_secret_scanning_alert_locations": (
        "GET",
        "/repos/{owner}/{repo}/secret-scanning/alerts/{alert_number}/locations",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_dependabot_alert": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "update_dependabot_alert": (
        "PATCH",
        "/repos/{owner}/{repo}/dependabot/alerts/{alert_number}",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_code_scanning_default_setup": (
        "GET",
        "/repos/{owner}/{repo}/code-scanning/default-setup",
        "security_events plus repo for private repository access",
        "json",
    ),
    "update_code_scanning_default_setup": (
        "PATCH",
        "/repos/{owner}/{repo}/code-scanning/default-setup",
        "security_events plus repo for private repository access",
        "json",
    ),
    "upload_sarif_and_wait": (
        "POST",
        "/repos/{owner}/{repo}/code-scanning/sarifs",
        "security_events plus repo for private repository access",
        "poll_sarif",
    ),
    "create_code_scanning_autofix_and_wait": (
        "POST",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}/autofix",
        "security_events plus repo for private repository access",
        "poll_autofix",
    ),
    "commit_code_scanning_autofix": (
        "POST",
        "/repos/{owner}/{repo}/code-scanning/alerts/{alert_number}/autofix/commits",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_repository_sbom": (
        "GET",
        "/repos/{owner}/{repo}/dependency-graph/sbom",
        "security_events plus repo for private repository access",
        "json",
    ),
    "submit_dependency_snapshot": (
        "POST",
        "/repos/{owner}/{repo}/dependency-graph/snapshots",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_vulnerability_alert_status": (
        "GET",
        "/repos/{owner}/{repo}/vulnerability-alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "enable_vulnerability_alerts": (
        "PUT",
        "/repos/{owner}/{repo}/vulnerability-alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "disable_vulnerability_alerts": (
        "DELETE",
        "/repos/{owner}/{repo}/vulnerability-alerts",
        "security_events plus repo for private repository access",
        "json",
    ),
    "get_automated_security_fixes_status": (
        "GET",
        "/repos/{owner}/{repo}/automated-security-fixes",
        "security_events plus repo for private repository access",
        "json",
    ),
    "list_organization_code_security_configurations": (
        "GET",
        "/orgs/{org}/code-security/configurations",
        "admin:org",
        "json",
    ),
    "list_default_code_security_configurations": (
        "GET",
        "/orgs/{org}/code-security/configurations/defaults",
        "admin:org",
        "json",
    ),
    "get_code_security_configuration": (
        "GET",
        "/orgs/{org}/code-security/configurations/{configuration_id}",
        "admin:org",
        "json",
    ),
    "attach_code_security_configuration": (
        "POST",
        "/orgs/{org}/code-security/configurations/{configuration_id}/attach",
        "admin:org",
        "json",
    ),
    "detach_code_security_configuration": (
        "DELETE",
        "/orgs/{org}/code-security/configurations/detach",
        "admin:org",
        "json",
    ),
    "set_default_code_security_configuration": (
        "PUT",
        "/orgs/{org}/code-security/configurations/{configuration_id}/defaults",
        "admin:org",
        "json",
    ),
    "list_code_security_configuration_repositories": (
        "GET",
        "/orgs/{org}/code-security/configurations/{configuration_id}/repositories",
        "admin:org",
        "json",
    ),
}

DEPENDABOT_ENDPOINTS: dict[str, EndpointContract] = {
    "list_repository_dependabot_secrets": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/secrets",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_repository_dependabot_public_key": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/secrets/public-key",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_repository_dependabot_secret": (
        "GET",
        "/repos/{owner}/{repo}/dependabot/secrets/{secret_name}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "upsert_repository_dependabot_secret": (
        "PUT",
        "/repos/{owner}/{repo}/dependabot/secrets/{secret_name}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "delete_repository_dependabot_secret": (
        "DELETE",
        "/repos/{owner}/{repo}/dependabot/secrets/{secret_name}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_organization_dependabot_secrets": (
        "GET",
        "/orgs/{org}/dependabot/secrets",
        "admin:org",
        "json",
    ),
    "get_organization_dependabot_public_key": (
        "GET",
        "/orgs/{org}/dependabot/secrets/public-key",
        "admin:org",
        "json",
    ),
    "get_organization_dependabot_secret": (
        "GET",
        "/orgs/{org}/dependabot/secrets/{secret_name}",
        "admin:org",
        "json",
    ),
    "upsert_organization_dependabot_secret": (
        "PUT",
        "/orgs/{org}/dependabot/secrets/{secret_name}",
        "admin:org",
        "json",
    ),
    "delete_organization_dependabot_secret": (
        "DELETE",
        "/orgs/{org}/dependabot/secrets/{secret_name}",
        "admin:org",
        "json",
    ),
    "list_organization_dependabot_secret_repositories": (
        "GET",
        "/orgs/{org}/dependabot/secrets/{secret_name}/repositories",
        "admin:org",
        "json",
    ),
    "set_organization_dependabot_secret_repositories": (
        "PUT",
        "/orgs/{org}/dependabot/secrets/{secret_name}/repositories",
        "admin:org",
        "json",
    ),
}

ACTIONS_ENDPOINTS: dict[str, EndpointContract] = {
    "list_repository_workflows": (
        "GET",
        "/repos/{owner}/{repo}/actions/workflows",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_workflow": (
        "GET",
        "/repos/{owner}/{repo}/actions/workflows/{workflow_id}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "dispatch_workflow": (
        "POST",
        "/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_workflow_runs": (
        "GET",
        "/repos/{owner}/{repo}/actions/runs",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_workflow_run": (
        "GET",
        "/repos/{owner}/{repo}/actions/runs/{run_id}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_workflow_run_jobs": (
        "GET",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "download_workflow_job_logs": (
        "GET",
        "/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
        "repo (default; omit only for public repository data)",
        "binary",
    ),
    "cancel_workflow_run": (
        "POST",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/cancel",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "rerun_workflow": (
        "POST",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/rerun",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "rerun_failed_workflow_jobs": (
        "POST",
        "/repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_artifacts": (
        "GET",
        "/repos/{owner}/{repo}/actions/artifacts",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "download_artifact": (
        "GET",
        "/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/{archive_format}",
        "repo (default; omit only for public repository data)",
        "binary",
    ),
    "get_combined_commit_status": (
        "GET",
        "/repos/{owner}/{repo}/commits/{ref}/status",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_commit_statuses": (
        "GET",
        "/repos/{owner}/{repo}/commits/{ref}/statuses",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_commit_status": (
        "POST",
        "/repos/{owner}/{repo}/statuses/{sha}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_releases": (
        "GET",
        "/repos/{owner}/{repo}/releases",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "create_release": (
        "POST",
        "/repos/{owner}/{repo}/releases",
        "repo (default; omit only for public repository data); workflow "
        "when the resolved target commit modifies .github/workflows",
        "json",
    ),
    "update_release": (
        "PATCH",
        "/repos/{owner}/{repo}/releases/{release_id}",
        "repo (default; omit only for public repository data); workflow "
        "when the resolved target commit modifies .github/workflows",
        "json",
    ),
    "get_release_by_tag": (
        "GET",
        "/repos/{owner}/{repo}/releases/tags/{tag}",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "generate_release_notes": (
        "POST",
        "/repos/{owner}/{repo}/releases/generate-notes",
        "repo (default; omit only for public repository data)",
        "json",
    ),
}

ORGANIZATION_ENDPOINTS: dict[str, EndpointContract] = {
    "get_organization": ("GET", "/orgs/{org}", "read:org", "json"),
    "get_organization_audit_log": (
        "GET",
        "/orgs/{org}/audit-log",
        "read:audit_log",
        "json",
    ),
    "list_organization_github_app_installations": (
        "GET",
        "/orgs/{org}/installations",
        "admin:read",
        "json",
    ),
    "list_organization_members": ("GET", "/orgs/{org}/members", "read:org", "json"),
    "get_organization_membership": (
        "GET",
        "/orgs/{org}/memberships/{username}",
        "read:org",
        "json",
    ),
    "list_outside_collaborators": (
        "GET",
        "/orgs/{org}/outside_collaborators",
        "read:org",
        "json",
    ),
}

POSTURE_ENDPOINTS: dict[str, EndpointContract] = {
    "list_repository_collaborators": (
        "GET",
        "/repos/{owner}/{repo}/collaborators",
        "repo plus read:org for organization repositories",
        "json",
    ),
    "get_repository_collaborator_permission": (
        "GET",
        "/repos/{owner}/{repo}/collaborators/{username}/permission",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_branch_protection": (
        "GET",
        "/repos/{owner}/{repo}/branches/{branch}/protection",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "update_branch_protection": (
        "PUT",
        "/repos/{owner}/{repo}/branches/{branch}/protection",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "get_required_commit_signature_protection": (
        "GET",
        "/repos/{owner}/{repo}/branches/{branch}/protection/required_signatures",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_repository_rulesets": (
        "GET",
        "/repos/{owner}/{repo}/rulesets",
        "repo (default; omit only for public repository data)",
        "json",
    ),
    "list_organization_rulesets": ("GET", "/orgs/{org}/rulesets", "admin:org", "json"),
}
