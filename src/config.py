"""
配置管理模块
统一管理所有环境变量和配置项
"""
import os
import logging
from typing import List
from dotenv import load_dotenv
from src.token_manager import TokenManager

# 加载环境变量
load_dotenv()

class Config:
    """应用配置类"""
    
    # API认证配置
    VALID_API_KEY: str = os.getenv("VALID_API_KEY", "")
    # 移除硬编码的K2THINK_TOKEN，使用token管理器
    K2THINK_API_URL: str = os.getenv("K2THINK_API_URL", "https://www.k2think.ai/api/chat/completions")
    
    # Token管理配置
    TOKENS_FILE: str = os.getenv("TOKENS_FILE", "tokens_guest.txt")
    MAX_TOKEN_FAILURES: int = int(os.getenv("MAX_TOKEN_FAILURES", "3"))
    
    # Token管理器实例（延迟初始化）
    _token_manager: TokenManager = None
    
    # 服务器配置
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8001"))
    
    # 功能开关
    TOOL_SUPPORT: bool = os.getenv("TOOL_SUPPORT", "true").lower() == "true"
    DEBUG_LOGGING: bool = os.getenv("DEBUG_LOGGING", "false").lower() == "true"
    ENABLE_ACCESS_LOG: bool = os.getenv("ENABLE_ACCESS_LOG", "true").lower() == "true"

    # 管理页面配置
    ADMIN_PAGE_PATH: str = os.getenv("ADMIN_PAGE_PATH", "/admin")

    # 代理模式配置
    PROXY_MODE: str = os.getenv("PROXY_MODE", "guest")  # 默认为游客模式
    GUEST_TOKENS_FILE: str = os.getenv("GUEST_TOKENS_FILE", "tokens_guest.txt")
    USER_TOKENS_FILE: str = os.getenv("USER_TOKENS_FILE", "tokens.txt")
    GUEST_API_URL: str = os.getenv("GUEST_API_URL", "https://www.k2think.ai/api/guest/chat/completions")
    USER_API_URL: str = os.getenv("USER_API_URL", "https://www.k2think.ai/api/chat/completions")

    # 性能配置
    REQUEST_TIMEOUT: float = float(os.getenv("REQUEST_TIMEOUT", "60"))
    MAX_KEEPALIVE_CONNECTIONS: int = int(os.getenv("MAX_KEEPALIVE_CONNECTIONS", "20"))
    MAX_CONNECTIONS: int = int(os.getenv("MAX_CONNECTIONS", "100"))
    STREAM_DELAY: float = float(os.getenv("STREAM_DELAY", "0.05"))
    STREAM_CHUNK_SIZE: int = int(os.getenv("STREAM_CHUNK_SIZE", "50"))
    MAX_STREAM_TIME: float = float(os.getenv("MAX_STREAM_TIME", "10.0"))

    # 日志配置
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # CORS配置
    CORS_ORIGINS: List[str] = (
        os.getenv("CORS_ORIGINS", "*").split(",")
        if os.getenv("CORS_ORIGINS", "*") != "*"
        else ["*"]
    )

    @classmethod
    def validate(cls) -> None:
        """验证必需的配置项"""
        if not cls.VALID_API_KEY:
            raise ValueError("错误：VALID_API_KEY 环境变量未设置。请在 .env 文件中提供一个安全的API密钥。")

        # 验证数值范围
        if cls.PORT < 1 or cls.PORT > 65535:
            raise ValueError(f"错误：PORT 值 {cls.PORT} 不在有效范围内 (1-65535)")

        if cls.REQUEST_TIMEOUT <= 0:
            raise ValueError(f"错误：REQUEST_TIMEOUT 必须大于0，当前值: {cls.REQUEST_TIMEOUT}")

        if cls.STREAM_DELAY < 0:
            raise ValueError(f"错误：STREAM_DELAY 不能为负数，当前值: {cls.STREAM_DELAY}")

    @classmethod
    def setup_logging(cls) -> None:
        """设置日志配置"""
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR
        }

        log_level = level_map.get(cls.LOG_LEVEL, logging.INFO)
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

    @classmethod
    def get_token_manager(cls) -> TokenManager:
        """获取token管理器实例（单例模式）"""
        if cls._token_manager is None:
            cls._token_manager = TokenManager(
                tokens_file=cls.TOKENS_FILE,
                max_failures=cls.MAX_TOKEN_FAILURES
            )
        return cls._token_manager

    @classmethod
    def reset_token_manager(cls):
        if cls._token_manager is not None:
            cls._token_manager = None
        cls.get_token_manager()

        if cls.PROXY_MODE == 'guest' and not cls._token_manager.tokens_list:
            tokens = cls._token_manager.generate_random_tokens()
            cls._token_manager.save_tokens(tokens)
        else:
            cls.reload_tokens()

    @classmethod
    def reload_tokens(cls) -> None:
        """重新加载token"""
        if cls._token_manager is not None:
            cls._token_manager.reload_tokens()

    @classmethod
    def switch_proxy_mode(cls, mode: str) -> bool:
        """切换代理模式

        Args:
            mode: 代理模式，'guest' 或 'user'

        Returns:
            切换是否成功
        """
        if mode not in ['guest', 'user']:
            return False

        # 更新模式
        cls.PROXY_MODE = mode

        # 根据模式更新配置
        if mode == 'guest':
            cls.TOKENS_FILE = cls.GUEST_TOKENS_FILE
            cls.K2THINK_API_URL = cls.GUEST_API_URL
        else:  # user
            cls.TOKENS_FILE = cls.USER_TOKENS_FILE
            cls.K2THINK_API_URL = cls.USER_API_URL

        # 重新加载token
        cls.reset_token_manager()
        return True
