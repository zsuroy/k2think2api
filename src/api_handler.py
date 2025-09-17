"""
API处理模块
处理主要的API路由逻辑
"""
import json
import time
import asyncio
import logging
from typing import Dict, List
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse

from src.config import Config
from src.constants import (
    APIConstants, ResponseConstants, LogMessages, 
    ErrorMessages, HeaderConstants
)
from src.exceptions import (
    AuthenticationError, SerializationError, 
    K2ThinkProxyError
)
from src.models import ChatCompletionRequest, ModelsResponse, ModelInfo
from src.tool_handler import ToolHandler
from src.response_processor import ResponseProcessor
from src.token_manager import TokenManager
from src.utils import safe_str

logger = logging.getLogger(__name__)

class APIHandler:
    """API处理器"""
    
    def __init__(self, config: Config):
        self.config = config
        self.tool_handler = ToolHandler(config)
        self.response_processor = ResponseProcessor(config, self.tool_handler)
        self.token_manager = config.get_token_manager()
    
    def validate_api_key(self, authorization: str) -> bool:
        """验证API密钥"""
        if not authorization or not authorization.startswith(APIConstants.BEARER_PREFIX):
            return False
        api_key = authorization[APIConstants.BEARER_PREFIX_LENGTH:]  # 移除 "Bearer " 前缀
        return api_key == self.config.VALID_API_KEY
    
    def should_output_thinking(self, model_name: str) -> bool:
        """根据模型名判断是否应该输出思考内容"""
        return model_name != APIConstants.MODEL_ID_NOTHINK
    
    def get_actual_model_id(self, model_name: str) -> str:
        """获取实际的模型ID（将nothink版本映射回原始模型）"""
        if model_name == APIConstants.MODEL_ID_NOTHINK:
            return APIConstants.MODEL_ID
        return model_name
    
    async def get_models(self) -> ModelsResponse:
        """获取模型列表"""
        model_info_standard = ModelInfo(
            id=APIConstants.MODEL_ID,
            created=int(time.time()),
            owned_by=APIConstants.MODEL_OWNER,
            root=APIConstants.MODEL_ROOT
        )
        model_info_nothink = ModelInfo(
            id=APIConstants.MODEL_ID_NOTHINK,
            created=int(time.time()),
            owned_by=APIConstants.MODEL_OWNER,
            root=APIConstants.MODEL_ROOT
        )
        return ModelsResponse(data=[model_info_standard, model_info_nothink])
    
    async def chat_completions(self, request: ChatCompletionRequest, auth_request: Request):
        """处理聊天补全请求"""
        # 验证API密钥
        authorization = auth_request.headers.get(HeaderConstants.AUTHORIZATION, "")
        if not self.validate_api_key(authorization):
            raise AuthenticationError()
        
        # 判断是否应该输出思考内容
        output_thinking = self.should_output_thinking(request.model)
        actual_model_id = self.get_actual_model_id(request.model)
        
        try:
            # 处理消息
            raw_messages = self._process_raw_messages(request.messages)
            
            # 检查工具是否启用和存在
            has_tools = self._check_tools_enabled(request)
            
            self._log_request_info(raw_messages, has_tools, request.tools)
            
            # 处理工具相关消息
            processed_messages = self._process_messages_with_tools(
                raw_messages, request, has_tools
            )
            
            # 构建K2Think请求
            k2think_payload = self._build_k2think_payload(
                request, processed_messages, actual_model_id
            )
            
            # 验证JSON序列化
            self._validate_json_serialization(k2think_payload)
            
            # 处理响应（带重试机制）
            if request.stream:
                return await self._handle_stream_response_with_retry(
                    request, k2think_payload, has_tools, output_thinking
                )
            else:
                return await self._handle_non_stream_response_with_retry(
                    request, k2think_payload, has_tools, output_thinking
                )
                
        except K2ThinkProxyError:
            # 重新抛出自定义异常
            raise
        except Exception as e:
            logger.error(f"API转发错误: {safe_str(e)}")
            raise HTTPException(
                status_code=APIConstants.HTTP_INTERNAL_ERROR,
                detail={
                    "error": {
                        "message": safe_str(e),
                        "type": ErrorMessages.API_ERROR
                    }
                }
            )
    
    def _process_raw_messages(self, messages: List) -> List[Dict]:
        """处理原始消息"""
        raw_messages = []
        for msg in messages:
            try:
                raw_messages.append({
                    "role": msg.role, 
                    "content": msg.content,  # 保持原始格式，稍后再转换
                    "tool_calls": msg.tool_calls
                })
            except Exception as e:
                logger.error(f"处理消息时出错: {safe_str(e)}, 消息: {safe_str(msg)}")
                # 使用默认值
                raw_messages.append({
                    "role": msg.role, 
                    "content": safe_str(msg.content) if msg.content else "", 
                    "tool_calls": msg.tool_calls
                })
        return raw_messages
    
    def _check_tools_enabled(self, request: ChatCompletionRequest) -> bool:
        """检查工具是否启用"""
        return (
            self.config.TOOL_SUPPORT and 
            request.tools is not None and 
            len(request.tools) > 0 and 
            request.tool_choice != "none"
        )
    
    def _log_request_info(self, raw_messages: List[Dict], has_tools: bool, tools: List):
        """记录请求信息"""
        logger.info(LogMessages.TOOL_STATUS.format(
            has_tools, len(tools) if tools else 0
        ))
        logger.info(LogMessages.MESSAGE_RECEIVED.format(len(raw_messages)))
        
        # 记录原始消息的角色分布
        role_count = {}
        for msg in raw_messages:
            role = msg.get("role", "unknown")
            role_count[role] = role_count.get(role, 0) + 1
        logger.info(LogMessages.ROLE_DISTRIBUTION.format("原始", role_count))
    
    def _process_messages_with_tools(
        self, 
        raw_messages: List[Dict], 
        request: ChatCompletionRequest, 
        has_tools: bool
    ) -> List[Dict]:
        """处理工具相关消息"""
        if has_tools:
            processed_messages = self.tool_handler.process_messages_with_tools(
                raw_messages,
                request.tools,
                request.tool_choice
            )
            logger.info(LogMessages.MESSAGE_PROCESSED.format(
                len(raw_messages), len(processed_messages)
            ))
            
            # 记录处理后消息的角色分布
            processed_role_count = {}
            for msg in processed_messages:
                role = msg.get("role", "unknown")
                processed_role_count[role] = processed_role_count.get(role, 0) + 1
            logger.info(LogMessages.ROLE_DISTRIBUTION.format("处理后", processed_role_count))
        else:
            processed_messages = raw_messages
            logger.info(LogMessages.NO_TOOLS)
        
        return processed_messages
    
    def _build_k2think_payload(
        self, 
        request: ChatCompletionRequest, 
        processed_messages: List[Dict],
        actual_model_id: str = None
    ) -> Dict:
        """构建K2Think请求负载"""
        # 构建K2Think格式的请求体 - 支持多模态内容
        k2think_messages = []
        for msg in processed_messages:
            try:
                # 使用多模态内容转换函数
                content = self.response_processor.content_to_multimodal(msg.get("content", ""))
                k2think_messages.append({
                    "role": msg["role"], 
                    "content": content
                })
            except Exception as e:
                logger.error(f"构建K2Think消息时出错: {safe_str(e)}, 消息: {safe_str(msg)}")
                # 使用安全的默认值
                fallback_content = self.tool_handler._content_to_string(msg.get("content", ""))
                k2think_messages.append({
                    "role": msg.get("role", "user"), 
                    "content": fallback_content
                })
        
        # 使用实际的模型ID
        model_id = actual_model_id or APIConstants.MODEL_ID
        
        return {
            "stream": request.stream,
            "model": model_id,
            "messages": k2think_messages,
            "params": {},
            "tool_servers": [],
            "features": {
                "image_generation": False,
                "code_interpreter": False,
                "web_search": False
            },
            "variables": self.response_processor.get_current_datetime_info(),
            "model_item": {
                "id": model_id,
                "object": ResponseConstants.MODEL_OBJECT,
                "owned_by": APIConstants.MODEL_OWNER,
                "root": APIConstants.MODEL_ROOT,
                "parent": None,
                "status": "active",
                "connection_type": "external",
                "name": model_id
            },
            "background_tasks": {
                "title_generation": True,
                "tags_generation": True
            },
            "chat_id": self.response_processor.generate_chat_id(),
            "id": self.response_processor.generate_session_id(),
            "session_id": self.response_processor.generate_session_id()
        }
    
    def _validate_json_serialization(self, k2think_payload: Dict):
        """验证JSON序列化"""
        try:
            # 测试JSON序列化
            json.dumps(k2think_payload, ensure_ascii=False)
            logger.info(LogMessages.JSON_VALIDATION_SUCCESS)
        except Exception as e:
            logger.error(LogMessages.JSON_VALIDATION_FAILED.format(e))
            # 尝试修复序列化问题
            try:
                k2think_payload = json.loads(json.dumps(k2think_payload, default=str, ensure_ascii=False))
                logger.info(LogMessages.JSON_FIXED)
            except Exception as fix_error:
                logger.error(f"无法修复序列化问题: {fix_error}")
                raise SerializationError()
    
    def _build_request_headers(self, request: ChatCompletionRequest, k2think_payload: Dict, token: str) -> Dict[str, str]:
        """构建请求头"""
        return {
            HeaderConstants.ACCEPT: (
                HeaderConstants.EVENT_STREAM_JSON if request.stream 
                else HeaderConstants.APPLICATION_JSON
            ),
            HeaderConstants.CONTENT_TYPE: HeaderConstants.APPLICATION_JSON,
            HeaderConstants.AUTHORIZATION: f"{APIConstants.BEARER_PREFIX}{token}",
            HeaderConstants.ORIGIN: "https://www.k2think.ai",
            HeaderConstants.REFERER: "https://www.k2think.ai/c/" + k2think_payload["chat_id"],
            HeaderConstants.USER_AGENT: HeaderConstants.DEFAULT_USER_AGENT
        }
    
    async def _handle_stream_response(
        self, 
        k2think_payload: Dict, 
        headers: Dict[str, str], 
        has_tools: bool,
        output_thinking: bool = True,
        original_model: str = None
    ) -> StreamingResponse:
        """处理流式响应"""
        return StreamingResponse(
            self.response_processor.process_stream_response_with_tools(
                k2think_payload, headers, has_tools, output_thinking, original_model
            ),
            media_type=HeaderConstants.TEXT_EVENT_STREAM,
            headers={
                HeaderConstants.CACHE_CONTROL: HeaderConstants.NO_CACHE,
                HeaderConstants.CONNECTION: HeaderConstants.KEEP_ALIVE,
                HeaderConstants.X_ACCEL_BUFFERING: HeaderConstants.NO_BUFFERING
            }
        )
    
    async def _handle_non_stream_response(
        self, 
        k2think_payload: Dict, 
        headers: Dict[str, str], 
        has_tools: bool,
        output_thinking: bool = True,
        original_model: str = None
    ) -> JSONResponse:
        """处理非流式响应"""
        full_content, token_info = await self.response_processor.process_non_stream_response(
            k2think_payload, headers, output_thinking
        )
        
        # 处理工具调用
        tool_calls = None
        message_content = full_content
        
        if has_tools:
            tool_calls = self.tool_handler.extract_tool_invocations(full_content)
            if tool_calls:
                # 当存在工具调用时，内容必须为null（OpenAI规范）
                message_content = None
                logger.info(LogMessages.TOOL_CALLS_EXTRACTED.format(
                    json.dumps(tool_calls, ensure_ascii=False)
                ))
            else:
                # 从内容中移除工具JSON
                message_content = self.tool_handler.remove_tool_json_content(full_content)
                if not message_content:
                    message_content = full_content  # 保留原内容如果清理后为空
        
        openai_response = self.response_processor.create_completion_response(
            message_content, tool_calls, token_info, original_model
        )
        
        return JSONResponse(content=openai_response)
    
    async def _handle_stream_response_with_retry(
        self, 
        request: ChatCompletionRequest,
        k2think_payload: Dict, 
        has_tools: bool,
        output_thinking: bool = True,
        max_retries: int = 3
    ) -> StreamingResponse:
        """处理流式响应（带重试机制）"""
        last_exception = None
        
        for attempt in range(max_retries):
            # 获取下一个可用token
            token = self.token_manager.get_next_token()
            if not token:
                logger.error("没有可用的token")
                raise HTTPException(
                    status_code=APIConstants.HTTP_SERVICE_UNAVAILABLE,
                    detail={
                        "error": {
                            "message": "所有token都已失效，请检查token配置",
                            "type": ErrorMessages.API_ERROR
                        }
                    }
                )
            
            # 构建请求头
            headers = self._build_request_headers(request, k2think_payload, token)
            
            try:
                logger.info(f"尝试流式请求 (第{attempt + 1}次)")
                
                # 使用现有的响应处理器，但在异常时标记token失败
                async def stream_generator():
                    try:
                        async for chunk in self.response_processor.process_stream_response_with_tools(
                            k2think_payload, headers, has_tools, output_thinking, request.model
                        ):
                            yield chunk
                        # 流式响应成功完成，标记token成功
                        self.token_manager.mark_token_success(token)
                    except Exception as e:
                        # 流式响应过程中出现错误，标记token失败
                        self.token_manager.mark_token_failure(token, safe_str(e))
                        raise e
                
                return StreamingResponse(
                    stream_generator(),
                    media_type=HeaderConstants.TEXT_EVENT_STREAM,
                    headers={
                        HeaderConstants.CACHE_CONTROL: HeaderConstants.NO_CACHE,
                        HeaderConstants.CONNECTION: HeaderConstants.KEEP_ALIVE,
                        HeaderConstants.X_ACCEL_BUFFERING: HeaderConstants.NO_BUFFERING
                    }
                )
            except Exception as e:
                last_exception = e
                logger.warning(f"流式请求失败 (第{attempt + 1}次): {safe_str(e)}")
                
                # 标记token失败
                token_failed = self.token_manager.mark_token_failure(token, safe_str(e))
                if token_failed:
                    logger.error(f"Token已被标记为失效")
                
                # 如果是最后一次尝试，抛出异常
                if attempt == max_retries - 1:
                    break
                
                # 短暂延迟后重试
                await asyncio.sleep(0.5)
        
        # 所有重试都失败了
        logger.error(f"所有流式请求重试都失败了，最后错误: {safe_str(last_exception)}")
        raise HTTPException(
            status_code=APIConstants.HTTP_INTERNAL_ERROR,
            detail={
                "error": {
                    "message": f"流式请求失败: {safe_str(last_exception)}",
                    "type": ErrorMessages.API_ERROR
                }
            }
        )
    
    async def _handle_non_stream_response_with_retry(
        self, 
        request: ChatCompletionRequest,
        k2think_payload: Dict, 
        has_tools: bool,
        output_thinking: bool = True,
        max_retries: int = 3
    ) -> JSONResponse:
        """处理非流式响应（带重试机制）"""
        last_exception = None
        
        for attempt in range(max_retries):
            # 获取下一个可用token
            token = self.token_manager.get_next_token()
            if not token:
                logger.error("没有可用的token")
                raise HTTPException(
                    status_code=APIConstants.HTTP_SERVICE_UNAVAILABLE,
                    detail={
                        "error": {
                            "message": "所有token都已失效，请检查token配置",
                            "type": ErrorMessages.API_ERROR
                        }
                    }
                )
            
            # 构建请求头
            headers = self._build_request_headers(request, k2think_payload, token)
            
            try:
                logger.info(f"尝试非流式请求 (第{attempt + 1}次)")
                
                # 处理响应
                full_content, token_info = await self.response_processor.process_non_stream_response(
                    k2think_payload, headers, output_thinking
                )
                
                # 标记token成功
                self.token_manager.mark_token_success(token)
                
                # 处理工具调用
                tool_calls = None
                message_content = full_content
                
                if has_tools:
                    tool_calls = self.tool_handler.extract_tool_invocations(full_content)
                    if tool_calls:
                        # 当存在工具调用时，内容必须为null（OpenAI规范）
                        message_content = None
                        logger.info(LogMessages.TOOL_CALLS_EXTRACTED.format(
                            json.dumps(tool_calls, ensure_ascii=False)
                        ))
                    else:
                        # 从内容中移除工具JSON
                        message_content = self.tool_handler.remove_tool_json_content(full_content)
                        if not message_content:
                            message_content = full_content  # 保留原内容如果清理后为空
                
                openai_response = self.response_processor.create_completion_response(
                    message_content, tool_calls, token_info, request.model
                )
                
                return JSONResponse(content=openai_response)
                
            except Exception as e:
                last_exception = e
                logger.warning(f"非流式请求失败 (第{attempt + 1}次): {safe_str(e)}")
                
                # 标记token失败
                token_failed = self.token_manager.mark_token_failure(token, safe_str(e))
                if token_failed:
                    logger.error(f"Token已被标记为失效")
                
                # 如果是最后一次尝试，抛出异常
                if attempt == max_retries - 1:
                    break
                
                # 短暂延迟后重试
                await asyncio.sleep(0.5)
        
        # 所有重试都失败了
        logger.error(f"所有非流式请求重试都失败了，最后错误: {safe_str(last_exception)}")
        raise HTTPException(
            status_code=APIConstants.HTTP_INTERNAL_ERROR,
            detail={
                "error": {
                    "message": f"非流式请求失败: {safe_str(last_exception)}",
                    "type": ErrorMessages.API_ERROR
                }
            }
        )