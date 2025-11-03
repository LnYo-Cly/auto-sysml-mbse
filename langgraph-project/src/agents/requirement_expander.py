import sys
sys.path.append('..')

import logging
import os
from typing import Optional
import datetime

from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser

from graph.workflow_state import WorkflowState, ProcessStatus
from config.settings import settings

logger = logging.getLogger(__name__)

# 系统提示模板 - 第一阶段：初始扩展
SYSTEM_PROMPT_INITIAL = """
你是一个MBSE (Model-Based Systems Engineering) 系统设计专家，具有丰富的SysML建模经验。
你的任务是接收一个高层级的简短需求，并将其扩展为一份详细的系统设计文档，提供足够的信息以支持后续的SysML建模工作。

请在扩展中详细阐述以下方面的内容，确保每个部分都有足够的细节和具体的技术描述：

## 1. 需求规格 (Requirements) - 重点关注
- 明确功能性需求（系统应该做什么）
- 定义非功能性需求（性能、可靠性、安全性、可用性）
- 说明设计约束和限制条件
- 描述验证标准和接受准则
- 明确优先级和依赖关系

## 2. 系统结构 (Block Definition and Internal Block) - 重点关注
- 定义系统的主要组件/模块/子系统（至少5-8个具体命名的块）
- 详细说明每个组件的职责、属性和操作
- 描述组件之间的关系（组合、聚合、关联、依赖）
- 明确接口定义，包括提供的服务和所需的服务
- 说明组件的内部结构和连接关系

## 3. 活动流程 (Activity) - 重点关注
- 以步骤序列形式描述系统的主要活动流程
- 明确标识决策点、分支条件和合并点
- 描述并行执行的活动和同步机制
- 定义活动的输入/输出对象和数据流
- 说明循环结构和终止条件

## 4. 状态机行为 (State Machine) - 重点关注
- 明确定义系统或组件的所有可能状态（至少5-8个具体命名的状态）
- 详细描述状态之间的转换条件和触发事件
- 指定每个状态的进入动作和退出动作
- 描述状态持续期间的活动
- 标识初始状态、终止状态和关键中间状态

## 5. 用例场景 (Use Case)
- 明确识别所有系统参与者（人类用户和外部系统）
- 详细描述每个用例的名称、目标和范围
- 提供详细的基本流程步骤（至少5-10个具体步骤）
- 描述至少2-3个备选流程和异常流程
- 明确用例之间的关系（包含、扩展、泛化）

## 6. 参数关系 (Parametric)
- 识别系统中的关键参数和变量
- 定义参数之间的数学/物理关系和约束公式
- 描述性能指标和计算方法
- 明确单位、范围和精度要求
- 说明参数如何影响系统行为和性能

## 7. 交互序列 (Sequence)
- 详细描述系统组件之间的消息交换序列
- 明确时序关系和消息内容
- 说明同步点和异步操作
- 描述条件执行和循环交互模式
- 标识关键场景的完整交互流程

请确保你的扩展内容：
1. 使用专业的系统工程术语和概念
2. 提供具体的技术细节，避免泛泛而谈
3. 包含足够的数值指标和量化描述
4. 使用清晰的结构和层次，便于后续建模
5. 保持内部一致性，避免矛盾的描述
6. 特别关注SysML中最重要且常用的的图表类型（需求、块定义和内部块、活动、状态机）
7. 不要在文档末尾添加总结、结语或其他元内容，只提供文档本体内容
8. 与数学公式有关的内容，不要使用LaTeX格式，而是直接提供数学公式文本形式
9. 除了上面提到了4种常用的图表类型，其他图表类型也需要生成
10. 生成的简短即可，每一个类型描述100字左右即可。

你的输出应是一份结构化、专业且全面的技术文档，使用Markdown格式，包含适当的标题、列表和强调。
"""

# 系统提示模板 - 第二阶段：质量提升
SYSTEM_PROMPT_ENHANCE = """
你是一个资深的MBSE (Model-Based Systems Engineering) 系统设计专家和技术文档审阅者。
你的任务是审阅并显著提升一份初步扩展的系统设计文档的质量，使其更加专业、详细和实用。

请对文档进行以下方面的改进：

1. **技术深度增强**：
   - 添加更多专业术语和行业标准
   - 深化技术细节，使描述更加具体
   - 确保每个功能和组件都有明确的技术参数和规格

2. **一致性和完整性检查**：
   - 确保文档各部分之间的一致性
   - 补充缺失的关键信息
   - 解决可能存在的逻辑矛盾

3. **量化指标补充**：
   - 添加具体的性能指标和数值
   - 明确时间、空间、能耗等关键参数
   - 提供可测量的验收标准

4. **系统关系明确化**：
   - 强化组件间的关系描述
   - 明确接口定义和数据流
   - 完善状态转换条件和触发事件

5. **实用性提升**：
   - 确保文档内容直接支持SysML建模
   - 添加适当的图表描述和示意
   - 提供更多实际应用场景和示例

请特别关注以下四个SysML中最重要的部分，确保它们有足够的细节和质量：
1. **需求规格 (Requirements)**
2. **块定义和内部块图 (Block Definition and Internal Block)**
3. **活动图 (Activity)**
4. **状态机图 (State Machine)**

请保持文档的整体结构不变，但显著提升每个部分的质量、深度和实用性。
最终输出应是一份高质量的、可直接用于SysML建模的专业技术文档。

重要说明：
1. 不要在文档末尾添加总结、结语或其他元内容（如"重点提升部分"、"此版本可作为..."等）
2. 不要添加任何关于文档本身的评论或说明
3. 只提供纯粹的技术文档内容，不要包含对文档的描述或评价
4. 删除任何"增强版"、"修订版"等标记
"""

# 用户提示模板 - 初始扩展
USER_PROMPT_INITIAL = """
请详细扩展以下系统需求，提供足够的信息以支持后续的SysML建模工作：

{requirement}

请确保你的回答涵盖所有7个方面，并提供具体的技术细节。请特别关注需求规格、块定义和内部块、活动流程以及状态机行为这四个最重要的部分。
请不要在文档末尾添加总结或结语，只提供文档本体内容。
"""

# 用户提示模板 - 质量提升
USER_PROMPT_ENHANCE = """
请审阅并显著提升以下系统设计文档的质量，使其更加专业、详细和实用：

{initial_content}

请特别关注技术深度、一致性、量化指标、系统关系和实用性方面的提升，确保文档能够直接支持高质量的SysML建模工作。
重点提升需求规格、块定义和内部块图、活动图和状态机图这四个SysML中最重要的部分。

请不要在文档末尾添加总结、结语或其他元内容，只提供文档本体内容。不要添加"增强版"、"修订版"等标记。
"""


def save_doc_to_file(content, stage_name, output_dir=None):
    """将文档内容保存到文件"""
    if not output_dir:
        output_dir = os.path.join("data", "output")
        os.makedirs(output_dir, exist_ok=True)
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{stage_name}_{timestamp}.md"
    file_path = os.path.join(output_dir, filename)
    
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
        logger.info(f"{stage_name}已保存至: {file_path}")
        return file_path
    except Exception as e:
        logger.error(f"保存{stage_name}失败: {str(e)}")
        return None


def expand_requirement(state: WorkflowState) -> WorkflowState:
    """
    扩展用户的简短需求描述
    
    参数:
        state: 当前工作流状态
        
    返回:
        更新后的工作流状态
    """
    # 检查是否有用户输入
    if not state.input_short_req:
        logger.info("⏭️ 没有提供简短需求描述，跳过需求扩展步骤")
        # 不设置为失败，只是跳过这一步
        return state
        
    try:
        logger.info("="*80)
        logger.info("开始扩展用户需求")
        logger.info("="*80)
        logger.info(f"用户输入: {state.input_short_req}")
        state.status = ProcessStatus.PROCESSING
        
        # 第一阶段：初始扩展
        logger.info("第一阶段：生成初始扩展文档")
        initial_prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT_INITIAL),
            ("human", USER_PROMPT_INITIAL)
        ])
        
        initial_llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.base_url,
            temperature=0.4
        )
        
        initial_chain = initial_prompt | initial_llm | StrOutputParser()
        initial_content = ""
        for chunk in initial_chain.stream({"requirement": state.input_short_req}):
            initial_content += chunk
            print(chunk, end="", flush=True)

        if state.save_stages:
            save_doc_to_file(initial_content, "初始扩展文档")
            state.initial_expanded_content = initial_content
        
        # 第二阶段：质量提升
        if state.enable_quality_enhancement:
            logger.info("第二阶段：提升文档质量")
            enhance_prompt = ChatPromptTemplate.from_messages([
                ("system", SYSTEM_PROMPT_ENHANCE),
                HumanMessagePromptTemplate.from_template(USER_PROMPT_ENHANCE)
            ])
            
            enhance_llm = ChatOpenAI(
                model=settings.llm_model,
                api_key=settings.openai_api_key,
                base_url=settings.base_url,
                temperature=0.1
            )
            
            enhance_chain = enhance_prompt | enhance_llm | StrOutputParser()
            enhanced_content = ""
            for chunk in enhance_chain.stream({"initial_content": initial_content}):
                enhanced_content += chunk
                print(chunk, end="", flush=True)

            if state.save_stages:
                save_doc_to_file(enhanced_content, "质量提升文档")
            
            state.expanded_content = enhanced_content
        else:
            state.expanded_content = initial_content
        
        state.status = ProcessStatus.COMPLETED
        logger.info("需求扩展完成")
        return state
        
    except Exception as e:
        logger.error(f"需求扩展失败: {str(e)}", exc_info=True)
        state.error_message = f"需求扩展失败: {str(e)}"
        state.status = ProcessStatus.FAILED
        return state