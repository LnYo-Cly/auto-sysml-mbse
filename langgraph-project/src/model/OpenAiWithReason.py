import os
from typing import Any, Dict, List, Optional, Iterator, cast

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.outputs import ChatGenerationChunk
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import BaseMessage, AIMessageChunk
from langchain_openai import ChatOpenAI

# openai 库中的类型，用于类型提示
from openai.types.chat.chat_completion_chunk import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import ChoiceDelta

# --- 我们自己实现的、稳健的消息转换函数 ---
def _convert_lc_messages_to_openai_format(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    openai_messages = []
    for message in messages:
        if message.type == "human":
            role = "user"
        elif message.type == "system":
            role = "system"
        elif message.type == "ai":
            role = "assistant"
        else:
            raise ValueError(f"Unsupported LangChain message type: {message.type}")
        
        openai_messages.append({"role": role, "content": message.content})
    return openai_messages

# --- 关键修正：一个更智能的、保留所有自定义字段的转换函数 ---
def _convert_delta_to_message_chunk_with_custom_fields(
    _delta: ChoiceDelta
) -> AIMessageChunk:
    """
    这个增强版函数会自动查找并保留 delta 中的所有非标准字段。
    """
    role = _delta.role or "assistant"
    content = _delta.content or ""
    
    # 将 delta 对象转换为字典，以方便操作
    raw_delta_dict = _delta.model_dump()
    
    # 定义 OpenAI 的标准、已知字段
    standard_fields = {"role", "content", "tool_calls", "function_call"}
    
    # 查找所有不属于标准字段的键，并将它们放入 additional_kwargs
    additional_kwargs = {
        key: value
        for key, value in raw_delta_dict.items()
        if key not in standard_fields and value is not None
    }

    # 如果有标准的 tool_calls，也标准地处理它
    if _delta.tool_calls:
        additional_kwargs["tool_calls"] = [dict(tc) for tc in _delta.tool_calls]

    if role == "assistant":
        return AIMessageChunk(content=content, additional_kwargs=additional_kwargs)
    else:
        raise ValueError(f"Unknown streaming role: {role}")

# 最终版的 CustomChatOpenAI 类
class CustomChatOpenAI(ChatOpenAI):
    def _get_openai_request_params(
        self,
        stop: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        valid_params = {
            "temperature", "top_p", "n", "max_tokens", 
            "presence_penalty", "frequency_penalty", "logit_bias", "user",
            "tools", "tool_choice"
        }
        params = self.model_kwargs.copy()
        for param in valid_params:
            if hasattr(self, param) and (value := getattr(self, param)) is not None:
                params[param] = value
        params.update(kwargs)
        if stop:
            params["stop"] = stop
        elif self.stop is not None and "stop" not in kwargs:
             params["stop"] = self.stop
        return params

    def _stream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs: Any,
    ) -> Iterator[ChatGenerationChunk]:
        
        request_params = self._get_openai_request_params(stop=stop, **kwargs)
        request_params["model"] = self.model_name
        request_params["messages"] = _convert_lc_messages_to_openai_format(messages)

        stream = self.client.create(stream=True, **request_params)

        for raw_chunk in stream:
            if not isinstance(raw_chunk, ChatCompletionChunk) or not raw_chunk.choices:
                continue
            
            choice = raw_chunk.choices[0]
            if choice.delta is None:
                continue

            delta = cast(ChoiceDelta, choice.delta)
            
            # --- 关键修正：调用我们新的、智能的转换函数 ---
            message_chunk = _convert_delta_to_message_chunk_with_custom_fields(delta)

            generation_chunk = ChatGenerationChunk(message=message_chunk)

            if run_manager:
                run_manager.on_llm_new_token(
                    generation_chunk.text, chunk=generation_chunk
                )
            yield generation_chunk

# --- 使用示例 (在您的主程序中) ---
if __name__ == '__main__':
    initial_llm = CustomChatOpenAI(model="gpt-3.5-turbo", temperature=0.4)
    initial_prompt = ChatPromptTemplate.from_messages([
        ("system", "You are a helpful assistant."),
        ("user", "{requirement}")
    ])
    initial_chain = initial_prompt | initial_llm

    print("--- 开始流式处理 (现在可以正确解析了) ---")
    for chunk in initial_chain.stream({"requirement": "你好"}):
        # 现在，当你打印整个chunk时，它将包含正确的数据
        print(chunk)