import asyncio
import os
import threading

_client = None
_loop = None
_thread = None


def _ensure_loop():
    global _loop, _thread
    if _loop and _loop.is_running():
        return _loop
    _loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(_loop)
        _loop.run_forever()

    _thread = threading.Thread(target=_run, daemon=True)
    _thread.start()
    return _loop


def _run_sync(coro):
    loop = _ensure_loop()
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=30)


def _session_path():
    configured = os.environ.get("TELETHON_SESSION_PATH", "")
    if configured:
        return configured[:-8] if configured.endswith(".session") else configured
    root = os.path.dirname(os.path.dirname(__file__))
    return os.path.join(root, "telethon_session")


def get_client():
    global _client
    if _client is not None:
        return _client
    api_id = os.environ.get("TELETHON_API_ID")
    api_hash = os.environ.get("TELETHON_API_HASH")
    if not api_id or not api_hash:
        return None
    try:
        from telethon import TelegramClient
    except ImportError:
        return None
    loop = _ensure_loop()

    async def _connect():
        client = TelegramClient(_session_path(), int(api_id), api_hash, loop=loop)
        await client.connect()
        if not await client.is_user_authorized():
            await client.disconnect()
            return None
        return client

    _client = _run_sync(_connect())
    return _client


def is_available():
    return get_client() is not None


def create_temp_group(title, user_ids, bot_token=None, bot_username=None):
    client = get_client()
    if not client:
        return None
    bot_id = bot_token.split(":")[0] if bot_token else None

    async def _create():
        from telethon.tl.functions.channels import CreateChannelRequest, EditAdminRequest, InviteToChannelRequest
        from telethon.tl.types import ChatAdminRights

        result = await client(CreateChannelRequest(title=title, about="Temporary livestream reminder", megagroup=True))
        channel = result.chats[0]
        chat_id = -1000000000000 - channel.id
        entities = []
        for uid in user_ids:
            try:
                entities.append(await client.get_entity(int(uid)))
            except Exception as e:
                print(f"[TempGroup] Could not resolve user {uid}: {e}")
        bot_entity = None
        if bot_username:
            try:
                bot_entity = await client.get_entity(bot_username)
            except Exception as e:
                print(f"[TempGroup] Could not resolve bot @{bot_username}: {e}")
        elif bot_id:
            try:
                bot_entity = await client.get_entity(int(bot_id))
            except Exception as e:
                print(f"[TempGroup] Could not resolve bot {bot_id}: {e}")
        if bot_entity:
            entities.append(bot_entity)
        if entities:
            await client(InviteToChannelRequest(channel, entities))
        if bot_entity:
            try:
                await client(EditAdminRequest(
                    channel,
                    bot_entity,
                    admin_rights=ChatAdminRights(
                        post_messages=True,
                        edit_messages=True,
                        delete_messages=True,
                        ban_users=True,
                    ),
                    rank="Bot",
                ))
            except Exception as e:
                print(f"[TempGroup] Could not promote bot: {e}")
        return chat_id

    return _run_sync(_create())


def delete_group(chat_id):
    client = get_client()
    if not client:
        return False

    async def _delete():
        from telethon.tl.functions.channels import DeleteChannelRequest
        from telethon.tl.types import PeerChannel

        numeric_chat_id = int(chat_id)
        channel_id = -(numeric_chat_id + 1000000000000) if numeric_chat_id < -1000000000000 else abs(numeric_chat_id)
        try:
            entity = await client.get_entity(PeerChannel(channel_id))
            await client(DeleteChannelRequest(entity))
            return True
        except Exception as e:
            print(f"[TempGroup] Delete failed for {chat_id}: {e}")
            return False

    return _run_sync(_delete())
