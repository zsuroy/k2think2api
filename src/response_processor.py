"""
响应处理模块
处理流式和非流式响应的所有逻辑
"""
import json
import time
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Dict, AsyncGenerator, Tuple, Optional
import pytz
import httpx

from src.constants import (
    ToolConstants,APIConstants, ResponseConstants, ContentConstants, 
    NumericConstants, TimeConstants, HeaderConstants
)
from src.exceptions import UpstreamError, TimeoutError as ProxyTimeoutError
from src.tool_handler import ToolHandler
from src.utils import safe_str

logger = logging.getLogger(__name__)

class ResponseProcessor:
    """响应处理器"""
    
    def __init__(self, config, tool_handler: ToolHandler):
        self.config = config
        self.tool_handler = tool_handler
    
    def extract_answer_content(self, full_content: str, output_thinking: bool = True) -> str:
        """删除第一个<answer>标签和最后一个</answer>标签，保留内容"""
        if not full_content:
            return full_content
        
        # 完全通过模型名控制思考内容输出，默认显示思考内容
        should_output_thinking = output_thinking
        
        if should_output_thinking:
            # 删除第一个<answer>
            answer_start = full_content.find(ContentConstants.ANSWER_START_TAG)
            if answer_start != -1:
                full_content = full_content[:answer_start] + full_content[answer_start + len(ContentConstants.ANSWER_START_TAG):]

            # 删除最后一个</answer>
            answer_end = full_content.rfind(ContentConstants.ANSWER_END_TAG)
            if answer_end != -1:
                full_content = full_content[:answer_end] + full_content[answer_end + len(ContentConstants.ANSWER_END_TAG):]

            return full_content.strip()
        else:
            # 删除<think>部分（包括标签）
            think_start = full_content.find(ContentConstants.THINK_START_TAG)
            think_end = full_content.find(ContentConstants.THINK_END_TAG)
            if think_start != -1 and think_end != -1:
                full_content = full_content[:think_start] + full_content[think_end + len(ContentConstants.THINK_END_TAG):]
            
            # 删除<answer>标签及其内容之外的部分
            answer_start = full_content.find(ContentConstants.ANSWER_START_TAG)
            answer_end = full_content.rfind(ContentConstants.ANSWER_END_TAG)
            if answer_start != -1 and answer_end != -1:
                content = full_content[answer_start + len(ContentConstants.ANSWER_START_TAG):answer_end]
                return content.strip()

            return full_content.strip()
    
    def calculate_dynamic_chunk_size(self, content_length: int) -> int:
        """
        动态计算流式输出的chunk大小
        确保总输出时间不超过MAX_STREAM_TIME秒
        
        Args:
            content_length: 待输出内容的总长度
        
        Returns:
            int: 动态计算的chunk大小，最小为50
        """
        if content_length <= 0:
            return self.config.STREAM_CHUNK_SIZE
        
        # 计算需要的总chunk数量以满足时间限制
        # 总时间 = chunk数量 * STREAM_DELAY
        # chunk数量 = content_length / chunk_size
        # 所以：总时间 = (content_length / chunk_size) * STREAM_DELAY
        # 解出：chunk_size = (content_length * STREAM_DELAY) / MAX_STREAM_TIME
        
        calculated_chunk_size = int((content_length * self.config.STREAM_DELAY) / self.config.MAX_STREAM_TIME)
        
        # 确保chunk_size不小于最小值
        dynamic_chunk_size = max(calculated_chunk_size, NumericConstants.MIN_CHUNK_SIZE)
        
        # 如果计算出的chunk_size太大（比如内容很短），使用默认值
        if dynamic_chunk_size > content_length:
            dynamic_chunk_size = min(self.config.STREAM_CHUNK_SIZE, content_length)
        
        logger.debug(f"动态chunk_size计算: 内容长度={content_length}, 计算值={calculated_chunk_size}, 最终值={dynamic_chunk_size}")
        
        return dynamic_chunk_size
    
    def content_to_multimodal(self, content) -> str | list[dict]:
        """将内容转换为多模态格式用于K2Think API"""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            # 检查是否包含图像内容
            has_image = False
            result_parts = []
            
            for p in content:
                if hasattr(p, 'type'):  # ContentPart object
                    if getattr(p, 'type') == ContentConstants.TEXT_TYPE and getattr(p, 'text', None):
                        result_parts.append({
                            "type": ContentConstants.TEXT_TYPE,
                            "text": getattr(p, 'text')
                        })
                    elif getattr(p, 'type') == ContentConstants.IMAGE_URL_TYPE and getattr(p, 'image_url', None):
                        has_image = True
                        image_url_obj = getattr(p, 'image_url')
                        if hasattr(image_url_obj, 'url'):
                            url = getattr(image_url_obj, 'url')
                        else:
                            url = image_url_obj.get('url') if isinstance(image_url_obj, dict) else str(image_url_obj)
                        
                        result_parts.append({
                            "type": ContentConstants.IMAGE_URL_TYPE,
                            "image_url": {
                                "url": url
                            }
                        })
                elif isinstance(p, dict):
                    if p.get("type") == ContentConstants.TEXT_TYPE and p.get("text"):
                        result_parts.append({
                            "type": ContentConstants.TEXT_TYPE, 
                            "text": p.get("text")
                        })
                    elif p.get("type") == ContentConstants.IMAGE_URL_TYPE and p.get("image_url"):
                        has_image = True
                        result_parts.append({
                            "type": ContentConstants.IMAGE_URL_TYPE,
                            "image_url": p.get("image_url")
                        })
                elif isinstance(p, str):
                    result_parts.append({
                        "type": ContentConstants.TEXT_TYPE,
                        "text": p
                    })
            
            # 如果包含图像，返回多模态格式；否则返回纯文本
            if has_image and result_parts:
                return result_parts
            else:
                # 提取所有文本内容
                text_parts = []
                for part in result_parts:
                    if part.get("type") == ContentConstants.TEXT_TYPE:
                        text_parts.append(part.get("text", ""))
                return " ".join(text_parts)
        
        # 处理其他类型
        try:
            return str(content)
        except:
            return ""
    
    def get_current_datetime_info(self) -> Dict[str, str]:
        """获取当前时间信息"""
        # 设置时区为上海
        tz = pytz.timezone(ContentConstants.DEFAULT_TIMEZONE)
        now = datetime.now(tz)
        
        return {
            "{{USER_NAME}}": ContentConstants.DEFAULT_USER_NAME,
            "{{USER_LOCATION}}": ContentConstants.DEFAULT_USER_LOCATION,
            "{{CURRENT_DATETIME}}": now.strftime(TimeConstants.DATETIME_FORMAT),
            "{{CURRENT_DATE}}": now.strftime(TimeConstants.DATE_FORMAT),
            "{{CURRENT_TIME}}": now.strftime(TimeConstants.TIME_FORMAT),
            "{{CURRENT_WEEKDAY}}": now.strftime(TimeConstants.WEEKDAY_FORMAT),
            "{{CURRENT_TIMEZONE}}": ContentConstants.DEFAULT_TIMEZONE,
            "{{USER_LANGUAGE}}": ContentConstants.DEFAULT_USER_LANGUAGE
        }
    
    def generate_session_id(self) -> str:
        """生成会话ID"""
        return str(uuid.uuid4())
    
    def generate_chat_id(self) -> str:
        """生成聊天ID"""
        return str(uuid.uuid4())
    
    async def create_http_client(self) -> httpx.AsyncClient:
        """创建HTTP客户端"""
        base_kwargs = {
            "timeout": httpx.Timeout(timeout=None, connect=10.0),
            "limits": httpx.Limits(
                max_keepalive_connections=self.config.MAX_KEEPALIVE_CONNECTIONS, 
                max_connections=self.config.MAX_CONNECTIONS
            ),
            "follow_redirects": True
        }
        
        try:
            return httpx.AsyncClient(**base_kwargs)
        except Exception as e:
            logger.error(f"创建客户端失败: {e}")
            raise e
    
    async def make_request(
        self, 
        method: str, 
        url: str, 
        headers: dict, 
        json_data: dict = None, 
        stream: bool = False
    ) -> httpx.Response:
        """发送HTTP请求"""
        client = None
        
        try:
            client = await self.create_http_client()
            
            if stream:
                # 流式请求返回context manager
                return client.stream(method, url, headers=headers, json=json_data, timeout=None)
            else:
                response = await client.request(
                    method, url, headers=headers, json=json_data, 
                    timeout=self.config.REQUEST_TIMEOUT
                )
                
                # 详细记录非200响应
                if response.status_code != APIConstants.HTTP_OK:
                    logger.error(f"上游API返回错误状态码: {response.status_code}")
                    logger.error(f"响应头: {dict(response.headers)}")
                    try:
                        error_body = response.text
                        logger.error(f"错误响应体: {safe_str(error_body)}")
                    except Exception as e:
                        logger.error(f"无法读取错误响应体: {safe_str(e)}")
                
                response.raise_for_status()
                return response
                
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP状态错误: {e.response.status_code} - {safe_str(e.response.text)}")
            if client and not stream:
                await client.aclose()
            raise UpstreamError(f"上游服务错误: {e.response.status_code}", e.response.status_code)
        except httpx.TimeoutException as e:
            logger.error(f"请求超时: {e}")
            if client and not stream:
                await client.aclose()
            raise ProxyTimeoutError("请求超时")
        except Exception as e:
            logger.error(f"请求异常: {safe_str(e)}")
            if client and not stream:
                await client.aclose()
            raise e
    
    async def process_non_stream_response(self, k2think_payload: dict, headers: dict, output_thinking: bool = None) -> Tuple[str, dict]:
        """处理非流式响应"""
        try:
            response = await self.make_request(
                "POST", 
                self.config.K2THINK_API_URL, 
                headers, 
                k2think_payload, 
                stream=False
            )
            
            # K2Think 非流式请求返回标准JSON格式
            result = response.json()
            
            # 提取内容
            full_content = ""
            if result.get('choices') and len(result['choices']) > 0:
                choice = result['choices'][0]
                if choice.get('message') and choice['message'].get('content'):
                    raw_content = choice['message']['content']
                    # 提取<answer>标签中的内容，去除标签
                    full_content = self.extract_answer_content(raw_content, output_thinking)
            
            # 提取token信息
            token_info = result.get('usage', {
                "prompt_tokens": NumericConstants.DEFAULT_PROMPT_TOKENS, 
                "completion_tokens": NumericConstants.DEFAULT_COMPLETION_TOKENS, 
                "total_tokens": NumericConstants.DEFAULT_TOTAL_TOKENS
            })
            
            await response.aclose()
            return full_content, token_info
                        
        except Exception as e:
            logger.error(f"处理非流式响应错误: {safe_str(e)}")
            raise
    
    async def process_stream_response_with_tools(
        self, 
        k2think_payload: dict, 
        headers: dict, 
        has_tools: bool = False,
        output_thinking: bool = None,
        original_model: str = None
    ) -> AsyncGenerator[str, None]:
        """处理流式响应 - 支持工具调用，优化性能"""
        try:
            # 发送开始chunk
            start_chunk = self._create_chunk_data(
                delta={"role": "assistant", "content": ""},
                finish_reason=None,
                model=original_model
            )
            yield f"{ResponseConstants.STREAM_DATA_PREFIX}{json.dumps(start_chunk)}\n\n"
            
            # 优化的模拟流式输出 - 立即开始获取响应并流式发送
            k2think_payload_copy = k2think_payload.copy()
            k2think_payload_copy["stream"] = False
            
            headers_copy = headers.copy()
            headers_copy[HeaderConstants.ACCEPT] = HeaderConstants.APPLICATION_JSON
            
            # 获取完整响应
            full_content, token_info = await self.process_non_stream_response(k2think_payload_copy, headers_copy, output_thinking)
            
            if not full_content:
                yield ResponseConstants.STREAM_DONE_MARKER
                return
            
            # 处理工具调用的流式响应
            finish_reason = ResponseConstants.FINISH_REASON_STOP
            if has_tools:
                tool_calls = self.tool_handler.extract_tool_invocations(full_content)
                if tool_calls:
                    # 发送工具调用
                    for i, tc in enumerate(tool_calls):
                        tool_call_delta = {
                            "index": i,
                            "id": tc.get("id"),
                            "type": tc.get("type", ToolConstants.FUNCTION_TYPE),
                            "function": tc.get("function", {}),
                        }
                        
                        tool_chunk = self._create_chunk_data(
                            delta={"tool_calls": [tool_call_delta]},
                            finish_reason=None,
                            model=original_model
                        )
                        yield f"{ResponseConstants.STREAM_DATA_PREFIX}{json.dumps(tool_chunk)}\n\n"
                    
                    finish_reason = ResponseConstants.FINISH_REASON_TOOL_CALLS
                else:
                    # 发送常规内容
                    trimmed_content = self.tool_handler.remove_tool_json_content(full_content)
                    if trimmed_content:
                        async for chunk in self._stream_content(trimmed_content, original_model):
                            yield chunk
            else:
                # 无工具 - 发送常规内容
                async for chunk in self._stream_content(full_content, original_model):
                    yield chunk
            
            # 发送结束chunk
            end_chunk = self._create_chunk_data(
                delta={},
                finish_reason=finish_reason,
                model=original_model
            )
            yield f"{ResponseConstants.STREAM_DATA_PREFIX}{json.dumps(end_chunk)}\n\n"
            yield ResponseConstants.STREAM_DONE_MARKER
            
        except Exception as e:
            logger.error(f"流式响应处理错误: {safe_str(e)}")
            error_chunk = self._create_chunk_data(
                delta={},
                finish_reason=ResponseConstants.FINISH_REASON_ERROR,
                model=original_model
            )
            yield f"{ResponseConstants.STREAM_DATA_PREFIX}{json.dumps(error_chunk)}\n\n"
            yield ResponseConstants.STREAM_DONE_MARKER
    
    async def _stream_content(self, content: str, model: str = None) -> AsyncGenerator[str, None]:
        """流式发送内容"""
        chunk_size = self.calculate_dynamic_chunk_size(len(content))
        
        for i in range(0, len(content), chunk_size):
            chunk_content = content[i:i + chunk_size]
            
            chunk = self._create_chunk_data(
                delta={"content": chunk_content},
                finish_reason=None,
                model=model
            )
            
            yield f"{ResponseConstants.STREAM_DATA_PREFIX}{json.dumps(chunk)}\n\n"
            # 添加延迟模拟真实流式效果
            await asyncio.sleep(self.config.STREAM_DELAY)
    
    def _create_chunk_data(self, delta: dict, finish_reason: Optional[str], model: str = None) -> dict:
        """创建流式响应chunk数据"""
        return {
            "id": f"chatcmpl-{int(time.time() * 1000)}",
            "object": ResponseConstants.CHAT_COMPLETION_CHUNK_OBJECT,
            "created": int(time.time()),
            "model": model or APIConstants.MODEL_ID,
            "choices": [{
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason
            }]
        }
    
    def create_completion_response(
        self, 
        content: Optional[str], 
        tool_calls: Optional[list] = None,
        token_info: Optional[dict] = None,
        model: str = None
    ) -> dict:
        """创建完整的聊天补全响应"""
        finish_reason = ResponseConstants.FINISH_REASON_TOOL_CALLS if tool_calls else ResponseConstants.FINISH_REASON_STOP
        
        message = {
            "role": "assistant",
            "content": content,
        }
        
        if tool_calls:
            message["tool_calls"] = tool_calls
        
        return {
            "id": f"chatcmpl-{int(time.time())}",
            "object": ResponseConstants.CHAT_COMPLETION_OBJECT,
            "created": int(time.time()),
            "model": model or APIConstants.MODEL_ID,
            "choices": [{
                "index": 0,
                "message": message,
                "finish_reason": finish_reason
            }],
            "usage": token_info or {
                "prompt_tokens": NumericConstants.DEFAULT_PROMPT_TOKENS,
                "completion_tokens": NumericConstants.DEFAULT_COMPLETION_TOKENS,
                "total_tokens": NumericConstants.DEFAULT_TOTAL_TOKENS
            }
        }