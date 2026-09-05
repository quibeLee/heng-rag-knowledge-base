""" 自定义异常基类 """
from http import HTTPStatus


class AppException(Exception):
    """应用异常基类"""

    code: str = "internal_error"
    message: str = "服务内部错误"
    http_status: HTTPStatus = HTTPStatus.INTERNAL_SERVER_ERROR

    def __init__(self, message: str | None = None, *, code: str | None = None) -> None:
        if message is not None:
            self.message = message
        if code is not None:
            self.code = code
        super().__init__(self.message)


class NotFoundError(AppException):
    code = "not_found"
    message = "资源不存在"
    http_status = HTTPStatus.NOT_FOUND


class PermissionDeniedError(AppException):
    code = "permission_denied"
    message = "无权限访问该资源"
    http_status = HTTPStatus.FORBIDDEN


class ConfigurationError(AppException):
    code = "configuration_error"
    message = "服务配置错误"
    http_status = HTTPStatus.SERVICE_UNAVAILABLE


class ValidationError(AppException):
    code = "validation_error"
    message = "参数校验失败"
    http_status = HTTPStatus.BAD_REQUEST
