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
        if not private or (owner and user.id!=owner) or (not owner and isinstance(event,types.CallbackQuery)):
            if isinstance(event,types.CallbackQuery):await event.answer(_('auth_not_authorized'),show_alert=True)
            elif (getattr(event,'text','') or '').split('@',1)[0]=='/start':await event.answer(_('auth_not_authorized'))
            return
        return await handler(event,data)
