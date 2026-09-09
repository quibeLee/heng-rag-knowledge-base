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

class UnauthorizedError(AppException):
    """未认证 / 凭证无效；前端拦截 401 会清登录态并跳转 /login。"""
    code = "unauthorized"
    message = "请先登录"
    http_status = HTTPStatus.UNAUTHORIZED
class ConflictError(AppException):
    code = "conflict"
    message = "资源冲突"
    http_status = HTTPStatus.CONFLICT

class RateLimitedError(AppException):
    """滑动窗口限流命中。"""
    code = "rate_limited"
    message = "请求过于频繁，请稍后再试"
    http_status = HTTPStatus.TOO_MANY_REQUESTS