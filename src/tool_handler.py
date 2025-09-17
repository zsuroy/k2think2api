"""
工具处理模块
处理工具调用相关的所有逻辑
"""
import json
import re
import time
import logging
from typing import List, Dict, Optional, Union

from src.constants import (
    ToolConstants, ContentConstants, LogMessages, 
    TimeConstants
)
from src.exceptions import ToolProcessingError

logger = logging.getLogger(__name__)

class ToolHandler:
    """工具调用处理器"""
    
    # 工具调用提取模式
    TOOL_CALL_FENCE_PATTERN = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)
    FUNCTION_CALL_PATTERN = re.compile(
        r"调用函数\s*[：:]\s*([\w\-\.]+)\s*(?:参数|arguments)[：:]\s*(\{.*?\})", 
        re.DOTALL
    )
    
    def __init__(self, config):
        self.config = config
        self.tool_support = config.TOOL_SUPPORT
    
    def generate_tool_prompt(self, tools: List[Dict]) -> str:
        """生成简洁的工具注入提示"""
        if not tools:
            return ""

        tool_definitions = []
        for tool in tools:
            if tool.get("type") != ToolConstants.FUNCTION_TYPE:
                continue

            function_spec = tool.get("function", {}) or {}
            function_name = function_spec.get("name", "unknown")
            function_description = function_spec.get("description", "")
            parameters = function_spec.get("parameters", {}) or {}

            # 创建简洁的工具定义
            tool_info = f"{function_name}: {function_description}"
            
            # 添加简化的参数信息
            parameter_properties = parameters.get("properties", {}) or {}
            required_parameters = set(parameters.get("required", []) or [])

            if parameter_properties:
                param_list = []
                for param_name, param_details in parameter_properties.items():
                    param_desc = (param_details or {}).get("description", "")
                    is_required = param_name in required_parameters
                    param_list.append(f"{param_name}{'*' if is_required else ''}: {param_desc}")
                tool_info += f" Parameters: {', '.join(param_list)}"

            tool_definitions.append(tool_info)

        if not tool_definitions:
            return ""

        # 构建简洁的工具提示
        prompt_template = (
            f"\n\nAvailable tools: {'; '.join(tool_definitions)}. "
            "To use a tool, respond with JSON: "
            '{"tool_calls":[{"id":"call_xxx","type":"function","function":{"name":"tool_name","arguments":"{\\"param\\":\\"value\\"}"}}]}'
        )

        return prompt_template
    
    def process_messages_with_tools(
        self, 
        messages: List[Dict], 
        tools: Optional[List[Dict]] = None, 
        tool_choice: Optional[Union[str, Dict]] = None
    ) -> List[Dict]:
        """处理消息并注入工具提示"""
        if not tools or not self.tool_support or (tool_choice == "none"):
            # 如果没有工具或禁用工具，直接返回原消息
            return [dict(m) for m in messages]
        
        tools_prompt = self.generate_tool_prompt(tools)
        
        # 限制工具提示长度，避免过长导致上游API拒绝
        if len(tools_prompt) > ToolConstants.MAX_TOOL_PROMPT_LENGTH:
            logger.warning(LogMessages.TOOL_PROMPT_TOO_LONG.format(len(tools_prompt)))
            tools_prompt = tools_prompt[:ToolConstants.MAX_TOOL_PROMPT_LENGTH] + ToolConstants.TOOL_PROMPT_TRUNCATE_SUFFIX
        
        processed = []
        has_system = any(m.get("role") == "system" for m in messages)

        if has_system:
            # 如果已有系统消息，在第一个系统消息中添加工具提示
            for m in messages:
                if m.get("role") == "system":
                    mm = dict(m)
                    content = self._content_to_string(mm.get("content", ""))
                    # 不限制系统消息长度
                    new_content = content + tools_prompt
                    mm["content"] = new_content
                    processed.append(mm)
                    # 只在第一个系统消息中添加工具提示
                    tools_prompt = ""
                else:
                    processed.append(dict(m))
        else:
            # 如果没有系统消息，需要添加一个，但只有当确实需要工具时
            if tools_prompt.strip():
                processed = [{"role": "system", "content": "你是一个有用的助手。" + tools_prompt}]
                processed.extend([dict(m) for m in messages])
            else:
                processed = [dict(m) for m in messages]

        # 添加简化的工具选择提示
        if tool_choice == "required":
            if processed and processed[-1].get("role") == "user":
                last = processed[-1]
                content = self._content_to_string(last.get("content", ""))
                last["content"] = content + "\n请使用工具来处理这个请求。"
        elif isinstance(tool_choice, dict) and tool_choice.get("type") == ToolConstants.FUNCTION_TYPE:
            fname = (tool_choice.get("function") or {}).get("name")
            if fname and processed and processed[-1].get("role") == "user":
                last = processed[-1]
                content = self._content_to_string(last.get("content", ""))
                last["content"] = content + f"\n请使用 {fname} 工具。"

        # 处理工具/函数消息
        final_msgs = []
        for m in processed:
            role = m.get("role")
            if role in ("tool", "function"):
                tool_name = m.get("name", "unknown")
                tool_content = self._content_to_string(m.get("content", ""))
                if isinstance(tool_content, dict):
                    tool_content = json.dumps(tool_content, ensure_ascii=False)

                # 简化工具结果消息
                content = f"工具 {tool_name} 结果: {tool_content}"
                if not content.strip():
                    content = f"工具 {tool_name} 执行完成"

                final_msgs.append({
                    "role": "assistant",
                    "content": content,
                })
            else:
                # 对于常规消息，确保内容是字符串格式
                final_msg = dict(m)
                content = self._content_to_string(final_msg.get("content", ""))
                final_msg["content"] = content
                final_msgs.append(final_msg)

        return final_msgs
    
    def extract_tool_invocations(self, text: str) -> Optional[List[Dict]]:
        """从响应文本中提取工具调用"""
        if not text:
            return None

        # 使用全文扫描，不限制长度
        scannable_text = text

        # 尝试1：从JSON代码块中提取
        json_blocks = self.TOOL_CALL_FENCE_PATTERN.findall(scannable_text)
        for json_block in json_blocks:
            try:
                parsed_data = json.loads(json_block)
                tool_calls = parsed_data.get("tool_calls")
                if tool_calls and isinstance(tool_calls, list):
                    # 确保arguments字段是字符串
                    self._normalize_tool_calls(tool_calls)
                    return tool_calls
            except (json.JSONDecodeError, AttributeError):
                continue

        # 尝试2：使用括号平衡方法提取内联JSON对象
        tool_calls = self._extract_inline_json_tool_calls(scannable_text)
        if tool_calls:
            return tool_calls

        # 尝试3：解析自然语言函数调用
        natural_lang_match = self.FUNCTION_CALL_PATTERN.search(scannable_text)
        if natural_lang_match:
            function_name = natural_lang_match.group(1).strip()
            arguments_str = natural_lang_match.group(2).strip()
            try:
                # 验证JSON格式
                json.loads(arguments_str)
                return [
                    {
                        "id": f"{ToolConstants.CALL_ID_PREFIX}{int(time.time() * TimeConstants.MICROSECONDS_MULTIPLIER)}",
                        "type": ToolConstants.FUNCTION_TYPE,
                        "function": {"name": function_name, "arguments": arguments_str},
                    }
                ]
            except json.JSONDecodeError:
                return None

        return None
    
    def remove_tool_json_content(self, text: str) -> str:
        """从响应文本中移除工具JSON内容 - 使用括号平衡方法"""
        
        def remove_tool_call_block(match: re.Match) -> str:
            json_content = match.group(1)
            try:
                parsed_data = json.loads(json_content)
                if "tool_calls" in parsed_data:
                    return ""
            except (json.JSONDecodeError, AttributeError):
                pass
            return match.group(0)
        
        # 步骤1：移除围栏工具JSON块
        cleaned_text = self.TOOL_CALL_FENCE_PATTERN.sub(remove_tool_call_block, text)
        
        # 步骤2：移除内联工具JSON - 使用基于括号平衡的智能方法
        result = []
        i = 0
        while i < len(cleaned_text):
            if cleaned_text[i] == '{':
                # 尝试找到匹配的右括号
                brace_count = 1
                j = i + 1
                in_string = False
                escape_next = False
                
                while j < len(cleaned_text) and brace_count > 0:
                    if escape_next:
                        escape_next = False
                    elif cleaned_text[j] == '\\':
                        escape_next = True
                    elif cleaned_text[j] == '"' and not escape_next:
                        in_string = not in_string
                    elif not in_string:
                        if cleaned_text[j] == '{':
                            brace_count += 1
                        elif cleaned_text[j] == '}':
                            brace_count -= 1
                    j += 1
                
                if brace_count == 0:
                    # 找到了完整的JSON对象
                    json_str = cleaned_text[i:j]
                    try:
                        parsed = json.loads(json_str)
                        if "tool_calls" in parsed:
                            # 这是一个工具调用，跳过它
                            i = j
                            continue
                    except:
                        pass
                
                # 不是工具调用或无法解析，保留这个字符
                result.append(cleaned_text[i])
                i += 1
            else:
                result.append(cleaned_text[i])
                i += 1
        
        return ''.join(result).strip()
    
    def _extract_inline_json_tool_calls(self, text: str) -> Optional[List[Dict]]:
        """使用括号平衡方法提取内联JSON工具调用"""
        i = 0
        while i < len(text):
            if text[i] == '{':
                # 尝试找到匹配的右括号
                brace_count = 1
                j = i + 1
                in_string = False
                escape_next = False
                
                while j < len(text) and brace_count > 0:
                    if escape_next:
                        escape_next = False
                    elif text[j] == '\\':
                        escape_next = True
                    elif text[j] == '"' and not escape_next:
                        in_string = not in_string
                    elif not in_string:
                        if text[j] == '{':
                            brace_count += 1
                        elif text[j] == '}':
                            brace_count -= 1
                    j += 1
                
                if brace_count == 0:
                    # 找到了完整的JSON对象
                    json_str = text[i:j]
                    try:
                        parsed_data = json.loads(json_str)
                        tool_calls = parsed_data.get("tool_calls")
                        if tool_calls and isinstance(tool_calls, list):
                            # 确保arguments字段是字符串
                            self._normalize_tool_calls(tool_calls)
                            return tool_calls
                    except (json.JSONDecodeError, AttributeError):
                        pass
                
                i += 1
            else:
                i += 1
        
        return None
    
    def _normalize_tool_calls(self, tool_calls: List[Dict]) -> None:
        """标准化工具调用，确保arguments字段是字符串"""
        for tc in tool_calls:
            if "function" in tc:
                func = tc["function"]
                if "arguments" in func:
                    if isinstance(func["arguments"], dict):
                        # 将字典转换为JSON字符串
                        func["arguments"] = json.dumps(func["arguments"], ensure_ascii=False)
                    elif not isinstance(func["arguments"], str):
                        func["arguments"] = json.dumps(func["arguments"], ensure_ascii=False)
    
    def _content_to_string(self, content) -> str:
        """将各种格式的内容转换为字符串"""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for p in content:
                if hasattr(p, 'text'):  # ContentPart object
                    if getattr(p, 'text', None):
                        parts.append(getattr(p, 'text', ''))
                elif isinstance(p, dict):
                    if p.get("type") == ContentConstants.TEXT_TYPE:
                        parts.append(p.get("text", ""))
                    elif p.get("type") == ContentConstants.IMAGE_URL_TYPE:
                        # 处理图像内容，添加描述性文本
                        parts.append(ContentConstants.IMAGE_PLACEHOLDER)
                elif isinstance(p, str):
                    parts.append(p)
                else:
                    # 处理其他类型的对象
                    try:
                        if hasattr(p, '__dict__'):
                            # 如果是对象，尝试获取text属性或转换为字符串
                            text_attr = getattr(p, 'text', None)
                            if text_attr:
                                parts.append(str(text_attr))
                        else:
                            parts.append(str(p))
                    except:
                        continue
            return " ".join(parts)
        # 处理其他类型
        try:
            return str(content)
        except:
            return ""