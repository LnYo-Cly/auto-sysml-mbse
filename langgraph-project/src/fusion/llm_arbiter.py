# step4_semantic_fusion/llm_arbiter.py

import sys
import os
import json

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from connections import config
import ollama
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser

from config.settings import settings

class EntityComparisonResult(BaseModel):
    """LLM 仲裁响应的结构化模型"""
    is_same_entity: bool = Field(..., description="两个实体是否代表同一个概念")
    reasoning: str = Field(..., description="判断的理由")

class LLMArbiter:
    """
    使用大语言模型（LLM）作为仲裁者，
    判断两个在语义上相似的实体是否真的是同一个概念实体。
    """
    def __init__(self, model_name="qwen3:1.7b"): # 您可以换成任何强大的本地模型
        self.model = model_name
        try:
             # 从 settings 读取配置
            self.llm = ChatOpenAI(
                model=settings.llm_model,
                openai_api_base=settings.base_url,
                openai_api_key=settings.openai_api_key,
                temperature=0.1
            )
            self.client = ollama.Client(host=config.OLLAMA_HOST)
            self.client.list()
            # 创建输出解析器
            self.parser = PydanticOutputParser(pydantic_object=EntityComparisonResult)
            
            # 创建提示模板
            self.prompt_template = ChatPromptTemplate.from_messages([
                ("system", """你是一个专业的系统建模（MBSE/SysML）专家。你的任务是判断两个模型元素是否代表了同一个概念实体。

在系统建模中，即使名称相似，元素也可能代表不同的独立组件（例如：'左轮' vs '右轮', '传感器A' vs '传感器B'）。请仔细思考它们的上下文和功能描述。

{format_instructions}"""),
                ("human", """{entity1_info}

{entity2_info}

问题：这两个元素是否代表了同一个概念实体？请判断并给出理由。""")
            ])
            
            print(f"✅ LLMArbiter 初始化成功，使用模型 '{settings.llm_model}'")
            
            print(f"✅ LLMArbiter 初始化成功，使用模型 '{self.model}'。")
        except Exception as e:
            print(f"❌ LLMArbiter 初始化失败: {e}")
            self.client = None
    
    def _construct_prompt(self, entity1_key: str, entity1_desc: str, entity2_key: str, entity2_desc: str) -> str:
        """构建用于LLM判断的提示。"""
        # 从规范键中解析出类型和名称
        type1, name1 = entity1_key.split('::', 1)
        type2, name2 = entity2_key.split('::', 1)

        # 构建实体信息，如果有 description 则包含
        entity1_info = f"""实体 1:
- 类型: "{type1}"
- 全限定名: "{name1}\""""
        if entity1_desc:
            entity1_info += f'\n- 功能描述: "{entity1_desc}"'

        entity2_info = f"""实体 2 (已存在于模型中):
- 类型: "{type2}"
- 全限定名: "{name2}\""""
        if entity2_desc:
            entity2_info += f'\n- 功能描述: "{entity2_desc}"'

        return f"""你是一个专业的系统建模（MBSE/SysML）专家。你的任务是判断下面两个模型元素是否代表了同一个概念实体。

在系统建模中，即使名称相似，元素也可能代表不同的独立组件（例如：'左轮' vs '右轮', '传感器A' vs '传感器B'）。请仔细思考它们的上下文和功能描述。

{entity1_info}

{entity2_info}

问题：这两个元素是否代表了同一个概念实体？请判断并给出理由。"""

    def _construct_entity_info(
        self, 
        entity_key: str, 
        entity_desc: str, 
        label: str = "实体"
    ) -> str:
        """
        构建实体信息字符串
        
        Args:
            entity_key: 实体的规范键 (格式: "Type::Name")
            entity_desc: 实体的功能描述
            label: 实体标签（如 "实体 1", "实体 2"）
            
        Returns:
            格式化的实体信息字符串
        """
        # 从规范键中解析出类型和名称
        parts = entity_key.split('::', 1)
        if len(parts) == 2:
            entity_type, entity_name = parts
        else:
            entity_type, entity_name = "Unknown", entity_key
        
        # 构建实体信息
        entity_info = f"""{label}:
- 类型: "{entity_type}"
- 全限定名: "{entity_name}\""""
        
        if entity_desc:
            entity_info += f'\n- 功能描述: "{entity_desc}"'
        
        return entity_info
    def are_they_the_same_entity(self, entity1_key: str, entity1_desc: str, entity2_key: str, entity2_desc: str) -> bool:
        """
        调用LLM来判断两个实体是否相同。

        Args:
            entity1_key: 新实体的规范键。
            entity1_desc: 新实体的功能描述（可选，可为空字符串）。
            entity2_key: 数据库中已存在的、语义相似的实体的规范键。
            entity2_desc: 已存在实体的功能描述（可选，可为空字符串）。

        Returns:
            如果LLM认为是同一个实体，返回True，否则返回False。
        """
        if not self.client:
            print("  - LLMArbiter未初始化，默认判断为不同实体。")
            return False
        if not self.llm:
            return False, "LLMArbiter未初始化"
        
        prompt = self._construct_prompt(entity1_key, entity1_desc, entity2_key, entity2_desc)
        
        try:
                    # 构建实体信息
            entity1_info = self._construct_entity_info(
                entity1_key, 
                entity1_desc, 
                "实体 1"
            )
            entity2_info = self._construct_entity_info(
                entity2_key, 
                entity2_desc, 
                "实体 2 (已存在于模型中)"
            )
            
            # 格式化提示
            prompt = self.prompt_template.format_messages(
                format_instructions=self.parser.get_format_instructions(),
                entity1_info=entity1_info,
                entity2_info=entity2_info
            )
            
            # 调用 LLM
            response = self.llm.invoke(prompt)
            
            # 解析响应
            result = self.parser.parse(response.content)
            
            # 提取简短名称用于日志
            name1 = entity1_key.split('::')[-1] if '::' in entity1_key else entity1_key
            name2 = entity2_key.split('::')[-1] if '::' in entity2_key else entity2_key
            
            print(f"\n  - LLM 仲裁: '{name1}' vs '{name2}'")
            print(f"    结论: {'相同' if result.is_same_entity else '不同'}")
            print(f"    理由: {result.reasoning}")
            
            return result.is_same_entity
        
            # # 使用 Pydantic schema 强制结构化输出
            # response = self.client.chat(
            #     model=self.model,
            #     messages=[{'role': 'user', 'content': prompt}],
            #     format=EntityComparisonResult.model_json_schema()  # 使用 Pydantic schema
            # )
            
            # content = response['message']['content']
            
            # # 使用 Pydantic 验证和解析响应
            # try:
            #     result = EntityComparisonResult.model_validate_json(content)
                
            #     print(f"\n  - LLM 仲裁: '{entity1_key.split('::')[1]}' vs '{entity2_key.split('::')[1]}\n' -> 结论: {result.is_same_entity}. 理由: {result.reasoning}")
                
            #     return result.is_same_entity
                
            # except Exception as parse_err:
            #     print(f"  - ❌ LLM 仲裁失败: Pydantic 验证错误 - {parse_err}")
            #     print(f"  - 原始响应内容: {content}")
            #     return False
            
        except Exception as e:
            print(f"  - ❌ LLM 仲裁失败: {type(e).__name__} - {e}")
            # 在失败的情况下，采取保守策略，认为它们是不同的实体
            return False
