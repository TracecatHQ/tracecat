"""FastAPI Users database adapter for SQLModel.

Adapted from https://github.com/fastapi-users/fastapi-users-db-sqlmodel for our internal use
as the original package does not plan on having official support for SQLModel.
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi_users.authentication.strategy.db import AccessTokenDatabase
from fastapi_users.db.base import BaseUserDatabase
from fastapi_users.models import OAP
from fastapi_users_db_sqlalchemy.generics import TIMESTAMPAware, now_utc
from pydantic import UUID4, ConfigDict, EmailStr
from sqlalchemy import Column, types
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlmodel import AutoString, Field, SQLModel, func, select


class SQLModelBaseUserDB(SQLModel):
    __tablename__: str = "user"

    model_config: ConfigDict = ConfigDict(from_attributes=True)
    id: UUID4 = Field(default_factory=uuid.uuid4, primary_key=True, nullable=False)
    if TYPE_CHECKING:  # pragma: no cover
        email: str
    else:
        email: EmailStr = Field(
            sa_column_kwargs={"unique": True, "index": True},
            nullable=False,
            sa_type=AutoString,
        )
    hashed_password: str

    is_active: bool = Field(True, nullable=False)
    is_superuser: bool = Field(False, nullable=False)
    is_verified: bool = Field(False, nullable=False)


class SQLModelBaseOAuthAccount(SQLModel):
    __tablename__: str = "oauthaccount"

    model_config: ConfigDict = ConfigDict(from_attributes=True)
    id: UUID4 = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: UUID4 = Field(foreign_key="user.id", nullable=False)
    oauth_name: str = Field(index=True, nullable=False)
    access_token: str = Field(nullable=False)
    expires_at: int | None = Field(nullable=True)
    refresh_token: str | None = Field(nullable=True)
    account_id: str = Field(index=True, nullable=False)
    account_email: str = Field(nullable=False)


class SQLModelUserDatabaseAsync[UP, ID](BaseUserDatabase[UP, ID]):
    """
    Database adapter for SQLModel working purely asynchronously.

    :param user_model: SQLModel model of a DB representation of a user.
    :param session: SQLAlchemy async session.
    """

    session: AsyncSession
    user_model: type[UP]
    oauth_account_model: type[SQLModelBaseOAuthAccount] | None

    def __init__(
        self,
        session: AsyncSession,
        user_model: type[UP],
        oauth_account_model: type[SQLModelBaseOAuthAccount] | None = None,
    ):
        self.session = session
        self.user_model = user_model
        self.oauth_account_model = oauth_account_model

    async def get(self, id: ID) -> UP | None:
        """Get a single user by id."""
        return await self.session.get(self.user_model, id)

    async def get_by_email(self, email: str) -> UP | None:
        """Get a single user by email."""
        statement = select(self.user_model).where(  # type: ignore
            func.lower(self.user_model.email) == func.lower(email)
        )
        results = await self.session.execute(statement)
        object = results.first()
        if object is None:
            return None
        return object[0]

    async def get_by_oauth_account(self, oauth: str, account_id: str) -> UP | None:
        """Get a single user by OAuth account id."""
        if self.oauth_account_model is None:
            raise NotImplementedError()
        statement = (
            select(self.oauth_account_model)
            .where(self.oauth_account_model.oauth_name == oauth)
            .where(self.oauth_account_model.account_id == account_id)
            .options(selectinload(self.oauth_account_model.user))  # type: ignore
        )
        results = await self.session.execute(statement)
        oauth_account = results.first()
        if oauth_account:
            user = oauth_account[0].user  # type: ignore
            return user
        return None

    async def create(self, create_dict: dict[str, Any]) -> UP:
        """Create a user."""
        user = self.user_model(**create_dict)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def update(self, user: UP, update_dict: dict[str, Any]) -> UP:
        for key, value in update_dict.items():
            setattr(user, key, value)
        self.session.add(user)
        await self.session.commit()
        await self.session.refresh(user)
        return user

    async def delete(self, user: UP) -> None:
        await self.session.delete(user)
        await self.session.commit()

    async def add_oauth_account(self, user: UP, create_dict: dict[str, Any]) -> UP:
        if self.oauth_account_model is None:
            raise NotImplementedError()

        oauth_account = self.oauth_account_model(**create_dict)
        user.oauth_accounts.append(oauth_account)  # type: ignore
        self.session.add(user)

        await self.session.commit()

        return user

    async def update_oauth_account(
        self,
        user: UP,
        oauth_account: OAP,  # type: ignore
        update_dict: dict[str, Any],
    ) -> UP:
        if self.oauth_account_model is None:
            raise NotImplementedError()

        for key, value in update_dict.items():
            setattr(oauth_account, key, value)
        self.session.add(oauth_account)
        await self.session.commit()

        return user


class SQLModelBaseAccessToken(SQLModel):
    __tablename__: str = "accesstoken"

    model_config: ConfigDict = ConfigDict(from_attributes=True)
    token: str = Field(
        sa_column=Column("token", types.String(length=43), primary_key=True)
    )
    created_at: datetime = Field(
        default_factory=now_utc,
        sa_column=Column(
            "created_at", TIMESTAMPAware(timezone=True), nullable=False, index=True
        ),
    )
    user_id: UUID4 = Field(foreign_key="user.id", nullable=False)


class SQLModelAccessTokenDatabaseAsync[AP](AccessTokenDatabase[AP]):
    """
    Access token database adapter for SQLModel working purely asynchronously.

    :param session: SQLAlchemy async session.
    :param access_token_model: SQLModel access token model.
    """

    def __init__(self, session: AsyncSession, access_token_model: type[AP]):
        self.session = session
        self.access_token_model = access_token_model

    async def get_by_token(
        self, token: str, max_age: datetime | None = None
    ) -> AP | None:
        statement = select(self.access_token_model).where(  # type: ignore
            self.access_token_model.token == token
        )
        if max_age is not None:
            statement = statement.where(self.access_token_model.created_at >= max_age)

        results = await self.session.execute(statement)
        access_token = results.first()
        if access_token is None:
            return None
        return access_token[0]

    async def create(self, create_dict: dict[str, Any]) -> AP:
        access_token = self.access_token_model(**create_dict)
        self.session.add(access_token)
        await self.session.commit()
        await self.session.refresh(access_token)
        return access_token

    async def update(self, access_token: AP, update_dict: dict[str, Any]) -> AP:
        for key, value in update_dict.items():
            setattr(access_token, key, value)
        self.session.add(access_token)
        await self.session.commit()
        await self.session.refresh(access_token)
        return access_token

    async def delete(self, access_token: AP) -> None:
        await self.session.delete(access_token)
        await self.session.commit()
