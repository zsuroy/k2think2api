"""
数据模型定义
定义所有API请求和响应的数据模型
"""
from pydantic import BaseModel
from typing import List, Dict, Optional, Union

class ImageUrl(BaseModel):
    """Image URL model for vision content"""
    url: str
    detail: Optional[str] = "auto"

class ContentPart(BaseModel):
    """Content part model for OpenAI's new content format"""
    type: str
    text: Optional[str] = None
    image_url: Optional[ImageUrl] = None

class Message(BaseModel):
    role: str
    content: Optional[Union[str, List[ContentPart]]] = None
    tool_calls: Optional[List[Dict]] = None

class ChatCompletionRequest(BaseModel):
    model: str = "MBZUAI-IFM/K2-Think"
    messages: List[Message]
    stream: bool = False
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    frequency_penalty: Optional[float] = None
    presence_penalty: Optional[float] = None
    stop: Optional[Union[str, List[str]]] = None
    tools: Optional[List[Dict]] = None
    tool_choice: Optional[Union[str, Dict]] = None

class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int
    owned_by: str
    permission: List[Dict] = []
    root: str
    parent: Optional[str] = None

class ModelsResponse(BaseModel):
    object: str = "list"
    data: List[ModelInfo]