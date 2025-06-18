# Microsoft Teams Integration with MSAL

This module provides Microsoft Teams integration for Tracecat using MSAL (Microsoft Authentication Library) for secure OAuth 2.0 authentication.

## Features

- **OAuth 2.0 Authentication**: Secure authentication using Microsoft Azure AD
- **Microsoft Graph API Integration**: Access Teams, channels, and messaging capabilities
- **Token Management**: Automatic token storage, expiration tracking, and refresh support
- **User Integration Management**: Per-user integration storage and management

## Architecture

### Components

1. **Models** (`models.py`): Database models and Pydantic schemas for user integrations
2. **Service** (`service.py`): Service layer for managing user integrations
3. **Microsoft Provider** (`microsoft.py`): MSAL-based OAuth provider and Teams API client
4. **Router** (`router.py`): FastAPI routes for integration endpoints

### Database Schema

The `user_integrations` table stores OAuth tokens and metadata:

- **user_id**: Foreign key to the user
- **provider**: Integration provider name (e.g., "microsoft-teams")
- **access_token**: OAuth access token (encrypted in production)
- **refresh_token**: OAuth refresh token for token renewal
- **expires_at**: Token expiration timestamp
- **scope**: Granted OAuth scopes
- **metadata**: Additional provider-specific data

## API Endpoints

### Authentication Flow

1. **GET /auth/integrations/microsoft/connect**

   - Initiates Microsoft OAuth flow
   - Returns authorization URL for user to authenticate

2. **GET /auth/integrations/microsoft/callback**

   - Handles OAuth callback from Microsoft
   - Exchanges authorization code for access tokens
   - Stores tokens for the authenticated user

3. **DELETE /auth/integrations/microsoft/disconnect**

   - Removes Microsoft Teams integration for user

4. **GET /auth/integrations/microsoft/status**
   - Returns integration status and token expiration info

### Teams API Endpoints

1. **GET /auth/integrations/microsoft/teams**

   - Lists Teams the user is a member of

2. **GET /auth/integrations/microsoft/teams/{team_id}/channels**

   - Lists channels for a specific team

3. **POST /auth/integrations/microsoft/teams/{team_id}/channels/{channel_id}/messages**
   - Sends a message to a Teams channel

### General Integration Management

1. **GET /auth/integrations/integrations**
   - Lists all integrations for the current user

## Configuration

Set these environment variables:

```bash
# Required
MICROSOFT_CLIENT_ID=your_client_id
MICROSOFT_CLIENT_SECRET=your_client_secret

# Optional
MICROSOFT_TENANT_ID=common  # or specific tenant ID
MICROSOFT_REDIRECT_URI=http://localhost:8000/auth/integrations/microsoft/callback
```

## Microsoft Graph Permissions

The integration requests these Microsoft Graph scopes:

- `Team.ReadBasic.All`: Read basic team information
- `Channel.ReadBasic.All`: Read basic channel information
- `ChannelMessage.Send`: Send messages to channels
- `User.Read`: Read user profile information

## Usage Example

```python
from tracecat.auth.integrations.service import IntegrationService
from tracecat.auth.integrations.microsoft import MicrosoftTeamsClient

# Get user's integration
integration = await integration_service.get_user_integration(
    user_id=user.id,
    provider="microsoft-teams"
)

if integration and not integration.is_expired:
    # Use Teams client
    teams_client = MicrosoftTeamsClient(integration.access_token)
    teams = await teams_client.get_user_teams()
```

## Security Considerations

- Access tokens are stored in the database (consider encryption at rest)
- Token expiration is automatically tracked
- Refresh tokens enable seamless token renewal
- User isolation ensures users can only access their own integrations
- State parameter prevents CSRF attacks during OAuth flow

## Error Handling

The integration includes comprehensive error handling:

- **Invalid OAuth state**: Prevents CSRF attacks
- **Expired tokens**: Clear error messages with reconnect instructions
- **Missing integrations**: Appropriate 404 responses
- **Microsoft Graph API errors**: Detailed logging and error propagation

## Future Enhancements

- Automatic token refresh before expiration
- Webhook support for real-time Teams events
- Additional Microsoft Graph API integrations (Calendar, Files, etc.)
- Token encryption at rest
- Rate limiting and request throttling
