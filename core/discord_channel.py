"""Optional Discord bridge: text the assistant from Discord and get replies
back — same shape as core/telegram_channel.py (an on_message callback locked
to one channel), just on a different transport. Telegram's Bot API supports
plain long-polling over HTTPS, so that bridge needs nothing but `requests`.
Discord has no equivalent: a bot only receives messages over a persistent
Gateway websocket with its own handshake/heartbeat/reconnect protocol, which
is complex enough to be worth a real library instead of hand-rolling it — see
the optional `discord.py` dependency in requirements.txt (only needed if
DISCORD_BOT_TOKEN is set; everything else runs fine without it).

Locked to one channel by DISCORD_CHANNEL_ID, same reasoning as Telegram's
chat-id lock: until it's set, the bot replies to a first message with the
channel id to paste into .env; once set, every other channel is silently
ignored — not even acknowledged.
"""
import asyncio
import threading

import config

_MAX_MESSAGE_CHARS = 2000  # Discord's hard per-message cap


class DiscordChannel:
    def __init__(self, on_message):
        """on_message: callable(text: str) -> str, returning the reply to
        send back. Run via asyncio.to_thread from the Gateway's own event
        loop so a slow brain turn never stalls the heartbeat — see
        main.py/app.py for how it's wired to Brain.ask() with an unattended
        confirm, matching TelegramChannel."""
        self.on_message = on_message
        self._thread: threading.Thread | None = None
        self._client = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def start(self) -> bool:
        if not config.DISCORD_BOT_TOKEN:
            return False
        if self._thread is not None:
            return True
        try:
            import discord
        except ImportError:
            print(
                "[discord] DISCORD_BOT_TOKEN is set but the 'discord.py' "
                "package isn't installed. Run: pip install discord.py"
            )
            return False

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        @client.event
        async def on_message(message):
            if client.user is not None and message.author.id == client.user.id:
                return
            reply = await asyncio.to_thread(
                self._route, str(message.channel.id), message.content
            )
            if reply is not None:
                await self._send(message.channel, reply)

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(client.start(config.DISCORD_BOT_TOKEN))
            except Exception as e:  # noqa: BLE001 - a dead gateway thread must not kill the app
                print(f"[discord] {e}")
            finally:
                loop.close()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        if self._client is None or self._loop is None or self._loop.is_closed():
            return
        asyncio.run_coroutine_threadsafe(self._client.close(), self._loop)

    # -- internals -------------------------------------------------------------

    def _route(self, channel_id: str, content: str) -> str | None:
        """Decide what (if anything) to reply with. Kept synchronous and
        free of any discord.py object so it's directly testable without a
        live Gateway connection — see tests/test_discord_channel.py."""
        if not content:
            return None

        allowed = config.DISCORD_CHANNEL_ID
        if not allowed:
            print(
                f"[discord] channel_id={channel_id} messaged the bot; "
                "DISCORD_CHANNEL_ID is not set yet."
            )
            return f"Set DISCORD_CHANNEL_ID={channel_id} in .env and restart to link this channel."
        if channel_id != str(allowed):
            return None  # not the linked channel — ignored, not even acknowledged

        try:
            reply = self.on_message(content)
        except Exception as e:  # noqa: BLE001 - a bad turn must not kill the bridge
            reply = f"Something went wrong: {e}"
        return reply or "Done."

    async def _send(self, channel, text: str) -> None:
        if len(text) > _MAX_MESSAGE_CHARS:
            text = text[: _MAX_MESSAGE_CHARS - len("\n\n[truncated]")] + "\n\n[truncated]"
        try:
            await channel.send(text)
        except Exception as e:  # noqa: BLE001
            print(f"[discord] couldn't send a reply: {e}")
