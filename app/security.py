"""Small ASGI request-size/deadline guard; headers and logs contain no payloads."""
import asyncio
import json
import logging
from datetime import datetime, timezone
from starlette.responses import JSONResponse


class BodyLimitMiddleware:
    def __init__(self, app, limit=32768):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or scope['method'] not in ('POST', 'PATCH', 'PUT', 'DELETE'):
            return await self.app(scope, receive, send)
        chunks, size = [], 0
        try:
            async with asyncio.timeout(10):
                while True:
                    message = await receive()
                    if message['type'] == 'http.disconnect':
                        return
                    size += len(message.get('body', b''))
                    if size > self.limit:
                        return await JSONResponse({'error': {'code': 'body_too_large', 'message': 'Request body exceeds 32 KiB.', 'request_id': scope.get('state', {}).get('request_id')}}, status_code=413)(scope, receive, send)
                    chunks.append(message.get('body', b''))
                    if not message.get('more_body', False):
                        break
        except TimeoutError:
            return await JSONResponse({'error': {'code': 'request_timeout', 'message': 'Request body did not arrive in time.', 'request_id': scope.get('state', {}).get('request_id')}}, status_code=408)(scope, receive, send)
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': b''.join(chunks), 'more_body': False}
            return await receive()
        await self.app(scope, replay, send)


class RequestJSONFormatter(logging.Formatter):
    def format(self, record):
        data = {'timestamp': datetime.now(timezone.utc).isoformat(), 'level': record.levelname,
                'event': 'http_request'}
        for key in ('request_id', 'method', 'route', 'status', 'duration_ms'):
            data[key] = getattr(record, key, None)
        return json.dumps(data, separators=(',', ':'))


class RedactAccessQuery(logging.Filter):
    """Uvicorn access logs must never retain OAuth codes or state parameters."""
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            args[2] = str(args[2]).split('?', 1)[0]
            record.args = tuple(args)
        return True


def configure_request_logging():
    access = logging.getLogger('uvicorn.access')
    if not any(isinstance(f, RedactAccessQuery) for f in access.filters):
        access.addFilter(RedactAccessQuery())
    logger = logging.getLogger('wellbeing.requests')
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(RequestJSONFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
