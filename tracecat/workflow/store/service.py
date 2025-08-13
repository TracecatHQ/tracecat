from tracecat.db.schemas import User
from tracecat.dsl.common import DSLInput
from tracecat.git import parse_git_url
from tracecat.logger import logger
from tracecat.service import BaseWorkspaceService
from tracecat.sync import Author, PushOptions
from tracecat.types.exceptions import TracecatValidationError
from tracecat.workflow.store.models import WorkflowDslPublish
from tracecat.workflow.store.sync import WorkflowSyncService
from tracecat.workspaces.service import WorkspaceService


class WorkflowStoreService(BaseWorkspaceService):
    service_name = "workflow_store"

    async def publish_workflow_dsl(
        self, dsl: DSLInput, params: WorkflowDslPublish
    ) -> None:
        """Take the latest version of the workflow definition and publish it to the store."""
        # Get workspace settings for git configuration
        if self.role.workspace_id is None:
            raise TracecatValidationError("Workspace ID is required")

        workspace_service = WorkspaceService(session=self.session, role=self.role)
        workspace = await workspace_service.get_workspace(self.role.workspace_id)

        if not workspace:
            raise TracecatValidationError("Workspace not found")

        # Extract git configuration from workspace settings
        git_repo_url = workspace.settings.get("git_repo_url")
        if not git_repo_url:
            raise TracecatValidationError(
                "Git repository URL not configured for this workspace. "
                "Please configure it in the workspace settings."
            )

        # Parse git allowed domains from workspace settings
        git_allowed_domains_str = workspace.settings.get(
            "git_allowed_domains", "github.com"
        )
        allowed_domains = {
            domain.strip() for domain in git_allowed_domains_str.split(",")
        }

        logger.info(
            "Publishing workflow to store",
            workflow_title=dsl.title,
            repo_url=git_repo_url,
            workspace_id=self.role.workspace_id,
        )

        # Parse the Git URL using workspace settings
        git_url = parse_git_url(git_repo_url, allowed_domains=allowed_domains)
        # Note: We could add ref support later if needed via params or workspace settings

        # Build base message
        base_message = params.message or f"Publish workflow: {dsl.title}"

        # Default author
        author_name = "Tracecat"
        author_email = "noreply@tracecat.com"
        augmented_message = base_message

        # Try to get user info
        if self.role.type == "user" and self.role.user_id:
            db_user = await self.session.get(User, self.role.user_id)
            if db_user and db_user.email:
                # Build display name
                display_name = (
                    " ".join([p for p in [db_user.first_name, db_user.last_name] if p])
                    or db_user.email.split("@")[0]
                )
                author_name = display_name
                author_email = db_user.email
                augmented_message = (
                    f"{base_message}\n\nAuthored by {display_name} <{author_email}>"
                )

        # Create Author and PushOptions
        author = Author(name=author_name, email=author_email)
        push_options = PushOptions(
            message=augmented_message,
            author=author,
            create_pr=True,  # Create PR for review
        )

        # Use WorkflowSyncService to push the workflow
        sync_service = WorkflowSyncService(session=self.session, role=self.role)
        commit_info = await sync_service.push(
            objects=[dsl],
            url=git_url,
            options=push_options,
        )

        logger.info(
            "Successfully published workflow",
            workflow_title=dsl.title,
            commit_sha=commit_info.sha,
            ref=commit_info.ref,
        )
