"""
工具函数模块
包含通用的工具函数
"""
import logging
import sys


def safe_str(obj) -> str:
    """
    安全地将对象转换为字符串，处理Unicode编码问题
    
    Args:
        obj: 需要转换为字符串的对象
        
    Returns:
        str: 安全转换后的字符串
    """
    try:
        if isinstance(obj, str):
            return obj
        elif isinstance(obj, bytes):
            return obj.decode('utf-8', errors='replace')
        else:
            return str(obj)
    except Exception:
        # 如果所有转换都失败，返回repr形式
        try:
            return repr(obj)
        except Exception:
            return '<无法转换的对象>'

def safe_encode(text: str, encoding: str = 'utf-8') -> bytes:
    """
    安全地将字符串编码为字节
    
    Args:
        text: 要编码的字符串
        encoding: 编码格式，默认为utf-8
        
    Returns:
        bytes: 编码后的字节
    """
    try:
        if isinstance(text, bytes):
            return text
        elif isinstance(text, str):
            return text.encode(encoding, errors='replace')
        else:
            return str(text).encode(encoding, errors='replace')
    except Exception:
        # 如果编码失败，返回安全的默认字节
        return b'<encoding_error>'


def configure_logging_encoding():
    """
    配置日志系统以支持UTF-8编码，避免ASCII编码错误
    """
    try:
        # 设置stdout和stderr的编码
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        
        # 为现有的日志处理器设置编码
        root_logger = logging.getLogger()
        for handler in root_logger.handlers:
            if hasattr(handler, 'stream'):
                if hasattr(handler.stream, 'reconfigure'):
                    handler.stream.reconfigure(encoding='utf-8', errors='replace')
                
    except Exception as e:
        # 如果配置失败，记录错误但不影响程序运行
        print(f"配置日志编码时出错: {e}")


def safe_format_exception(exc) -> str:
    """
    安全地格式化异常信息，避免编码问题
    
    Args:
        exc: 异常对象
        
    Returns:
        str: 格式化后的异常信息
    """
    try:
        import traceback
        # 获取异常的traceback信息
        tb_str = traceback.format_exception(type(exc), exc, exc.__traceback__)
        return safe_str(''.join(tb_str))
    except Exception:
        # 如果格式化失败，返回基本的异常信息
        return safe_str(f"{type(exc).__name__}: {exc}")
