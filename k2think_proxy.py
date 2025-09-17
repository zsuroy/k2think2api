"""
K2Think API 代理服务 - 重构版本
提供OpenAI兼容的API接口，代理到K2Think服务
"""
import time
import logging
from contextlib import asynccontextmanager
from typing import Optional, Annotated

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from src.config import Config
from src.constants import APIConstants
from src.exceptions import K2ThinkProxyError
from src.models import ChatCompletionRequest
from src.api_handler import APIHandler
from src.utils import configure_logging_encoding, safe_str

# 初始化配置
try:
    Config.validate()
    Config.setup_logging()
    Config.switch_proxy_mode(Config.PROXY_MODE)
    # 配置日志编码以支持Unicode字符
    configure_logging_encoding()
except Exception as e:
    print(f"配置错误: {safe_str(e)}")
    exit(1)

logger = logging.getLogger(__name__)

# 全局HTTP客户端管理
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("K2Think API Proxy 启动中...")
    yield
    logger.info("K2Think API Proxy 关闭中...")

# 创建FastAPI应用
app = FastAPI(
    title="K2Think API Proxy", 
    description="OpenAI兼容的K2Think API代理服务",
    version="2.0.0",
    lifespan=lifespan
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=Config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 初始化API处理器
api_handler = APIHandler(Config)

@app.get("/")
async def homepage():
    """首页 - 返回服务状态"""
    return JSONResponse(content={
        "status": "success",
        "message": "K2Think API Proxy is running",
        "service": "K2Think API Gateway", 
        "model": APIConstants.MODEL_ID,
        "version": "2.1.0",
        "features": [
            "Token轮询和负载均衡",
            "自动失效检测和重试",
            "Token池管理"
        ],
        "endpoints": {
            "chat": "/v1/chat/completions",
            "models": "/v1/models",
            "health": "/health",
            "admin": {
                "token_stats": "/admin/tokens/stats",
                "reset_token": "/admin/tokens/reset/{token_index}",
                "reset_all": "/admin/tokens/reset-all", 
                "reload_tokens": "/admin/tokens/reload"
            }
        }
    })

@app.get("/health")
async def health_check():
    """健康检查"""
    token_manager = Config.get_token_manager()
    token_stats = token_manager.get_token_stats()
    
    return JSONResponse(content={
        "status": "healthy",
        "timestamp": int(time.time()),
        "config": {
            "tool_support": Config.TOOL_SUPPORT,
            "debug_logging": Config.DEBUG_LOGGING,
            "note": "思考内容输出现在通过模型名控制"
        },
        "tokens": {
            "total": token_stats["total_tokens"],
            "active": token_stats["active_tokens"],
            "inactive": token_stats["inactive_tokens"]
        }
    })

@app.get("/favicon.ico")
async def favicon():
    """返回favicon"""
    return Response(content="", media_type="image/x-icon")

@app.get("/v1/models")
async def get_models():
    """获取模型列表"""
    return await api_handler.get_models()


# 添加 token 验证依赖函数
security = HTTPBearer()
async def authenticate_client(auth: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    if not auth or auth.credentials != Config.VALID_API_KEY:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")

AuthDep = Annotated[str, Depends(authenticate_client)]

@app.post("/v1/chat/completions")
async def chat_completions(request: ChatCompletionRequest, auth_request: Request, token: AuthDep):
    """处理聊天补全请求"""
    return await api_handler.chat_completions(request, auth_request)


# 添加管理页面路由
@app.get(f"{Config.ADMIN_PAGE_PATH}")
async def admin_page():
    """管理页面"""
    return FileResponse('templates/admin.html')


@app.get(f"{Config.ADMIN_PAGE_PATH}/status")
async def admin_status():
    """获取管理页面状态信息"""
    token_manager = Config.get_token_manager()
    token_stats = token_manager.get_token_stats()

    return JSONResponse(content={
        "mode": Config.PROXY_MODE,
        "token_stats": token_stats,
        "config": {
            "tokens_file": Config.TOKENS_FILE,
            "api_url": Config.K2THINK_API_URL,
            "tool_support": Config.TOOL_SUPPORT,
            "debug_logging": Config.DEBUG_LOGGING
        }
    })


@app.post(f"{Config.ADMIN_PAGE_PATH}/switch_mode")
async def switch_proxy_mode(request: Request):
    """切换代理模式"""
    try:
        data = await request.json()
        mode = data.get("mode")

        if mode not in ["guest", "user"]:
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "无效的模式，必须是 'guest' 或 'user'"}
            )

        success = Config.switch_proxy_mode(mode)
        if success:
            return JSONResponse(content={
                "status": "success",
                "message": f"已切换到{'游客代理' if mode == 'guest' else '用户代理'}模式",
                "mode": mode
            })
        else:
            return JSONResponse(
                status_code=500,
                content={"status": "error", "message": "切换模式失败"}
            )
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"切换模式时出错: {safe_str(e)}"}
        )


@app.get(f"{Config.ADMIN_PAGE_PATH}/tokens/content")
async def get_tokens_content():
    """获取当前Token文件内容"""
    try:
        token_manager = Config.get_token_manager()
        tokens = [t['token'] for t in token_manager.tokens]

        return JSONResponse(content={
            "status": "success",
            "tokens": tokens
        })
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"获取Token内容失败: {safe_str(e)}"}
        )


@app.post(f"{Config.ADMIN_PAGE_PATH}/tokens/update")
async def update_tokens(request: Request):
    """更新Token文件内容"""
    try:
        data = await request.json()
        tokens = data.get("tokens", [])

        if not isinstance(tokens, list):
            return JSONResponse(
                status_code=400,
                content={"status": "error", "message": "Token必须是列表格式"}
            )

        tm = Config.get_token_manager()
        tm.save_tokens(tokens)

        return JSONResponse(content={
            "status": "success",
            "message": "Token更新成功"
        })
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"status": "error", "message": f"更新Token失败: {safe_str(e)}"}
        )


@app.get("/admin/tokens/stats")
async def get_token_stats():
    """获取token池统计信息"""
    token_manager = Config.get_token_manager()
    stats = token_manager.get_token_stats()
    return JSONResponse(content={
        "status": "success",
        "data": stats
    })

@app.post("/admin/tokens/reset/{token_index}")
async def reset_token(token_index: int):
    """重置指定索引的token"""
    token_manager = Config.get_token_manager()
    success = token_manager.reset_token(token_index)
    if success:
        return JSONResponse(content={
            "status": "success",
            "message": f"Token {token_index} 已重置"
        })
    else:
        return JSONResponse(
            status_code=400,
            content={
                "status": "error",
                "message": f"无效的token索引: {token_index}"
            }
        )

@app.post("/admin/tokens/reset-all")
async def reset_all_tokens():
    """重置所有token"""
    token_manager = Config.get_token_manager()
    token_manager.reset_all_tokens()
    return JSONResponse(content={
        "status": "success",
        "message": "所有token已重置"
    })

@app.post("/admin/tokens/reload")
async def reload_tokens():
    """重新加载token文件"""
    try:
        Config.reload_tokens()
        token_manager = Config.get_token_manager()
        stats = token_manager.get_token_stats()
        return JSONResponse(content={
            "status": "success",
            "message": "Token文件已重新加载",
            "data": stats
        })
    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "message": f"重新加载失败: {safe_str(e)}"
            }
        )

@app.exception_handler(K2ThinkProxyError)
async def proxy_exception_handler(request: Request, exc: K2ThinkProxyError):
    """处理自定义代理异常"""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "message": exc.message,
                "type": exc.error_type
            }
        }
    )

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    """处理404错误"""
    return JSONResponse(
        status_code=404,
        content={"error": "Not Found"}
    )

if __name__ == "__main__":
    import uvicorn
    
    # 配置日志级别
    log_level = "debug" if Config.DEBUG_LOGGING else "info"
    
    logger.info(f"启动服务器: {Config.HOST}:{Config.PORT}")
    logger.info(f"代理模式: {Config.PROXY_MODE}")
    logger.info(f"工具支持: {Config.TOOL_SUPPORT}")
    logger.info("思考内容输出: 通过模型名控制 (MBZUAI-IFM/K2-Think vs MBZUAI-IFM/K2-Think-nothink)")
    
    uvicorn.run(
        app, 
        host=Config.HOST, 
        port=Config.PORT, 
        access_log=Config.ENABLE_ACCESS_LOG,
        log_level=log_level
    )