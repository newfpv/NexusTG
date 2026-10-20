"""Controller UI is private to the assigned owner of this individual bot."""
import os
from aiogram import BaseMiddleware, types
from core.utils import plugins
from core.config import _


class OwnerMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        cfg=await plugins.db.get_global_config()
        owner=int(os.environ.get('NEXUS_PROFILE_OWNER_ID') or 0) or cfg.admin_id
        user=getattr(event,'from_user',None)
        message=event.message if isinstance(event,types.CallbackQuery) else event
        private=bool(message and user and message.chat.id==user.id)
        if private and not owner and isinstance(event,types.Message):
            command=(getattr(event,'text','') or '').split(maxsplit=1)[0].split('@',1)[0] if getattr(event,'text',None) else ''
            if command!='/start' or not await plugins.db.claim_owner(user.id):
                return
            owner=user.id
        if private and owner and user.id==owner and not os.environ.get('NEXUS_PROFILE_OWNER_ID'):
            # Adopt an existing database owner before handling a settings reset.
            from core.environment import bind_claimed_owner
            bind_claimed_owner(owner)
        if not private or (owner and user.id!=owner) or (not owner and isinstance(event,types.CallbackQuery)):
            if isinstance(event,types.CallbackQuery):await event.answer(_('auth_not_authorized'),show_alert=True)
            elif (getattr(event,'text','') or '').split('@',1)[0]=='/start':await event.answer(_('auth_not_authorized'))
            return
        return await handler(event,data)
