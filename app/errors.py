from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, fields=None):
        self.status_code, self.code, self.message, self.fields = status_code, code, message, fields


def error_response(request: Request, status: int, code: str, message: str, fields=None):
    body = {'code': code, 'message': message, 'request_id': getattr(request.state, 'request_id', str(uuid4()))}
    if fields is not None:
        body['fields'] = fields
    return JSONResponse(status_code=status, content={'error': body}, headers={
        'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
        'Referrer-Policy': 'no-referrer', 'X-Frame-Options': 'DENY',
    })


def install_error_handlers(app: FastAPI):
    @app.exception_handler(ApiError)
    async def domain_error(request, exc):
        return error_response(request, exc.status_code, exc.code, exc.message, exc.fields)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        fields = [{'field': '.'.join(str(x) for x in e['loc']), 'message': e['msg']} for e in exc.errors()]
        return error_response(request, 422, 'validation_error', 'Check the highlighted input values.', fields)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        return error_response(request, 503, 'database_unavailable', 'Data access is unavailable. A save may be unconfirmed; retry with the same request key.')

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return error_response(request, exc.status_code, 'http_error', str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return error_response(request, 500, 'internal_error', 'This request could not be completed. Retry an unconfirmed save with the same request key.')
