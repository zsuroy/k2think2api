"""
自定义异常类模块
统一管理所有自定义异常
"""

class K2ThinkProxyError(Exception):
    """K2Think代理服务基础异常类"""
    def __init__(self, message: str, error_type: str = "api_error", status_code: int = 500):
        self.message = message
        self.error_type = error_type
        self.status_code = status_code
        super().__init__(self.message)

class ConfigurationError(K2ThinkProxyError):
    """配置错误异常"""
    def __init__(self, message: str):
        super().__init__(message, "configuration_error", 500)

class AuthenticationError(K2ThinkProxyError):
    """认证错误异常"""
    def __init__(self, message: str = "Invalid API key provided"):
        super().__init__(message, "authentication_error", 401)

class UpstreamError(K2ThinkProxyError):
    """上游服务错误异常"""
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message, "upstream_error", status_code)

class TimeoutError(K2ThinkProxyError):
    """超时错误异常"""
    def __init__(self, message: str = "请求超时"):
        super().__init__(message, "timeout_error", 504)

class SerializationError(K2ThinkProxyError):
    """序列化错误异常"""
    def __init__(self, message: str = "请求数据序列化失败"):
        super().__init__(message, "serialization_error", 400)

class ToolProcessingError(K2ThinkProxyError):
    """工具处理错误异常"""
    def __init__(self, message: str):
        super().__init__(message, "tool_processing_error", 400)

class ContentProcessingError(K2ThinkProxyError):
    """内容处理错误异常"""
    def __init__(self, message: str):
        super().__init__(message, "content_processing_error", 400)