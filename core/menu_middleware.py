"""Acknowledge navigation, record slow callbacks and suppress outdated menu renders."""
import asyncio
import ast
import inspect
import logging
import time
import textwrap
from collections import OrderedDict
from contextvars import ContextVar

from aiogram import BaseMiddleware
from aiogram.methods import AnswerCallbackQuery


menu_request = ContextVar("menu_request", default=None)
latest_requests = OrderedDict()


def menu_request_is_current():
    current = menu_request.get()
    return current is None or latest_requests.get(current["key"]) == current["id"]


class CallbackAckTracker:
    async def __call__(self, make_request, bot, method):
        current = menu_request.get()
        if isinstance(method, AnswerCallbackQuery) and current and method.callback_query_id == current["id"]:
            if current["answered"] and not method.text and not method.show_alert and not method.url:
                return True
        result = await make_request(bot, method)
        if isinstance(method, AnswerCallbackQuery) and current and method.callback_query_id == current["id"]:
            current["answered"] = True
        return result


class MenuMiddleware(BaseMiddleware):
    def __init__(self):
        self.manual_answers = {}
        self.recent_navigation = OrderedDict()

    async def _answer(self, event, request):
        if request["answered"]:
            return
        try:
            await asyncio.wait_for(event.answer(), timeout=2.0)
            request["answered"] = True
        except Exception as exc:
            logging.warning("[Menu] Callback acknowledgement failed: %s", type(exc).__name__)

    async def __call__(self, handler, event, data):
        if str(event.data or '').startswith('wizard_'):
            key=(event.from_user.id,event.data,event.message.message_id if event.message else None)
            now=time.monotonic()
            previous=self.recent_navigation.get(key,0)
            self.recent_navigation[key]=now
            while len(self.recent_navigation)>512:self.recent_navigation.popitem(last=False)
            if now-previous<.8:
                try:await event.answer()
                except Exception:pass
                return
        callback = data["handler"].callback
        if callback not in self.manual_answers:
            try:
                tree = ast.parse(textwrap.dedent(inspect.getsource(callback)))
                self.manual_answers[callback] = any(
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "call"
                    and node.func.attr == "answer"
                    and (node.args or any(kw.arg in {"text", "show_alert", "url"} for kw in node.keywords))
                    for node in ast.walk(tree)
                )
            except (OSError, TypeError, SyntaxError):
                self.manual_answers[callback] = True
        key = (event.message.chat.id, event.message.message_id) if event.message else (event.from_user.id, None)
        request = {"key": key, "id": event.id, "answered": False}
        latest_requests[key] = event.id
        latest_requests.move_to_end(key)
        if len(latest_requests) > 1024:
            latest_requests.popitem(last=False)
        token = menu_request.set(request)
        started = time.monotonic()
        try:
            # Preserve handlers' custom notifications and confirmation alerts.
            if not self.manual_answers[callback]:
                await self._answer(event, request)
            return await handler(event, data)
        finally:
            elapsed = time.monotonic() - started
            if elapsed >= 2.0:
                logging.warning("[Menu] Slow callback handler=%s duration=%.2fs", callback.__name__, elapsed)
            try:
                await self._answer(event, request)
            finally:
                menu_request.reset(token)


async def event_loop_monitor():
    while True:
        started = time.monotonic()
        await asyncio.sleep(5)
        lag = time.monotonic() - started - 5
        if lag >= 1.0:
            logging.warning("[Health] Event loop delayed %.2fs", lag)
