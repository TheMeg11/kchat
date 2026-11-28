"""Streaming request limits, including chunked multipart bodies."""
from starlette.responses import JSONResponse
from .config import settings


class BodyTooLarge(Exception):
    pass


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app=app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope,receive,send)
        path=scope.get("path", "")
        limit=settings.restore_max_bytes+1024*1024 if path.endswith("/restore/validate-file") else settings.webhook_max_bytes if path.startswith("/webhook/") else 1024*1024
        headers=dict(scope.get("headers",[]))
        try:
            length=int(headers.get(b"content-length",b"0"))
        except ValueError:
            return await JSONResponse({"detail":"Invalid Content-Length"},status_code=400)(scope,receive,send)
        if length > limit:
            return await JSONResponse({"detail":"حجم درخواست بیش از حد مجاز است.","code":"TOO_LARGE"},status_code=413)(scope,receive,send)
        size=0
        started=False
        async def bounded_receive():
            nonlocal size
            message=await receive()
            if message["type"]=="http.request":
                size+=len(message.get("body",b""))
                if size > limit: raise BodyTooLarge()
            return message
        async def track_send(message):
            nonlocal started
            if message["type"]=="http.response.start":started=True
            await send(message)
        try:
            await self.app(scope,bounded_receive,track_send)
        except BodyTooLarge:
            if started:raise
            await JSONResponse({"detail":"حجم درخواست بیش از حد مجاز است.","code":"TOO_LARGE"},status_code=413)(scope,receive,send)
