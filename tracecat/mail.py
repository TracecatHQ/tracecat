import asyncio
import re
import time
from collections.abc import Sequence
from typing import Any

import aiosmtplib
import yagmail

EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


class AsyncSMTPClient(yagmail.SMTP):
    """Wrapper around aioyagmail.SMTP."""

    async def login(self):
        # aiosmtplib implementation specific
        use_tls = str(self.port) == "465"
        self.smtp_starttls = not use_tls
        if self.oauth2_file is not None:
            await self._login_oauth2(self.credentials, use_tls)
        else:
            await self._login(self.credentials, use_tls=use_tls)

    async def _login_oauth2(self, oauth2_info: dict[str, Any], use_tls: bool) -> None:
        if "email_address" in oauth2_info:
            oauth2_info.pop("email_address")
        self.smtp = self.connection(
            hostname=self.host,
            port=self.port,
            use_tls=use_tls,
            **self.kwargs,
        )
        await self.smtp.connect()
        auth_string = self.get_oauth_string(self.user, oauth2_info)
        await self.smtp.ehlo(hostname=oauth2_info["google_client_id"])
        if self.starttls is True:
            await self.smtp.starttls()
        await self.smtp.execute_command(
            b"AUTH", b"XOAUTH2", bytes(auth_string, "ascii")
        )

    @property
    def connection(self):
        return aiosmtplib.SMTP

    async def send(
        self,
        to: str | None = None,
        subject: str | None = None,
        contents: str | None = None,
        attachments: str | list[str] | None = None,
        cc: str | list[str] | None = None,
        bcc: str | list[str] | None = None,
        preview_only: bool = False,
        headers: Any | None = None,
    ) -> tuple[str, str] | bool:
        """Use this to send an email with gmail"""
        recipients, msg_string = self.prepare_send(
            to, subject, contents, attachments, cc, bcc, headers
        )
        if preview_only:
            return (recipients, msg_string)
        return await self._attempt_send(recipients, msg_string)

    async def _attempt_send(
        self, recipients: str | Sequence[str], msg_string: str | bytes
    ) -> dict[str, str] | bool:
        attempts = 0
        while attempts < 3:
            try:
                result = await self.smtp.sendmail(self.user, recipients, msg_string)
                self.log.info("Message sent to %s", recipients)
                self.num_mail_sent += 1
                return result
            except aiosmtplib.SMTPServerDisconnected as e:
                self.log.error(e)
                attempts += 1
                time.sleep(attempts * 3)
        self.unsent.append((recipients, msg_string))
        return False

    async def send_unsent(self):
        """
        Emails that were not being able to send will be stored in :attr:`self.unsent`.
        Use this function to attempt to send these again
        """
        await asyncio.gather([self._attempt_send(*x) for x in self.unsent])
        while self.unsent:
            futures = [self._attempt_send(*self.unsent.pop()) for x in self.unsent]
            await asyncio.gather(*futures)

    async def close(self):
        raise ValueError("Should be `async with`")

    async def __aenter__(self):
        await self.login()
        return self

    async def __exit(self):
        if not self.is_closed:
            await self.aclose()
        return False

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if not self.is_closed:
            await self.aclose()
        return False

    async def aclose(self):
        """Close the connection to the SMTP server"""
        self.is_closed = True
        try:
            await self.smtp.quit()
        except (TypeError, AttributeError, aiosmtplib.SMTPServerDisconnected):
            pass

    async def _login(self, password: str, use_tls: bool | None = None) -> None:
        """
        Login to the SMTP server using password. `login` only needs to be manually run when the
        connection to the SMTP server was closed by the user.
        """
        self.smtp = self.connection(hostname=self.host, port=self.port)
        await self.smtp.connect(port=self.port, use_tls=use_tls)
        if self.starttls:
            await self.smtp.starttls()
        if not self.smtp_skip_login:
            password = self.handle_password(self.user, password)
            await self.smtp.login(self.user, password)
        self.is_closed = False

    def __del__(self):
        """Not required in async"""
        return NotImplemented
