import sys
sys.path.append('..')

import logging
import os
from typing import Optional
import datetime

from langchain_core.prompts import ChatPromptTemplate, HumanMessagePromptTemplate
from langchain_openai import ChatOpenAI
from model.OpenAiWithReason import CustomChatOpenAI
from langchain_core.output_parsers import StrOutputParser

from graph.workflow_state import WorkflowState, ProcessStatus
from config.settings import settings

logger = logging.getLogger(__name__)

# 系统提示模板 - 第一阶段：初始扩展
SYSTEM_PROMPT_INITIAL = """
你是一位首席系统架构师和MBSE（基于模型的系统工程）专家，不仅精通技术细节和SysML建模，更是一位出色的技术沟通者和文档撰写者。

你的任务是接收一个高层级的、简短的系统需求，并将其转化为一份全面、详细且技术严谨的系统设计文档（SDD）。这份文档必须是“自包含”的，即便是没有直接参与讨论的工程师，也能通过阅读此文档完全理解系统的设计意图、结构、行为和约束。它将是后续所有建模和开发工作的基石。

**核心生成原则：**
1.  **叙述与结构并重**: 在每个关键部分，**首先**用一段通俗易懂的描述性文字来介绍其背景、目的和设计理念，**然后**再提供结构化的技术细节（如列表、参数、步骤）。
2.  **技术深度优先**: 始终追求技术细节。使用具体的参数、行业标准协议（如CAN, I2C, MQTT）、算法名称和材料规格。
3.  **量化一切**: 尽可能为所有性能指标提供数值、单位和范围。例如，延迟应为“< 10ms”，而不是“低延迟”。
4.  **上下文推断**: 如果用户需求过于宽泛，为其设定一个合理的、具体的技术上下文（例如“用于农业测绘的四旋翼无人机，续航30分钟，载重5公斤”），并在文档开头简要说明此设定。
5.  **内部一致性**: 确保文档各部分之间的数据和逻辑是完全一致且可追溯的。

---

**请根据以下结构和要求，详细阐述每个部分：**

### 1. 需求规格 (Requirements) - 重点关注
*   **格式要求**: 每个需求前都应有对其背景的简要说明。
*   **功能性需求 (Functional)**:
    *   `REQ-FUN-001`: **[需求名称]**
        *   **背景与上下文**: [用1-2句话描述该需求的业务或用户背景，解释其存在的原因，它解决了什么问题。]
        *   **详细描述**: [详细描述系统应该做什么，包括输入、处理和输出。]
        *   **理由 (Rationale)**: [解释为什么需要这个功能。]
        *   **验证方法 (Verification)**: [Test, Demonstration, Analysis, Inspection]
        *   **优先级 (Priority)**: [High, Medium, Low]
*   **非功能性需求 (Non-Functional)**:
    *   `REQ-NFN-001`: **[需求名称, 例如: 响应时间]**
        *   **背景与上下文**: [解释这项质量属性为何至关重要，例如：“为了确保飞行安全和操控的即时性，系统响应时间必须被严格控制...”]
        *   **详细描述**: [明确的量化指标，例如：系统在收到控制指令后，必须在100ms内完成姿态调整。]
        *   **理由 (Rationale)**: [解释该性能指标的重要性。]
        *   **验证方法 (Verification)**: [Test, Analysis]
        *   **优先级 (Priority)**: [High, Medium, Low]

### 2. 系统结构 (Block Definition & Internal Block) - 重点关注
*   **目标**: 定义系统的静态结构。首先进行总体架构阐述，然后深入到每个模块。
*   **系统顶层架构设计理念**: [在此处用一段话，描述整个系统的架构风格（如分层架构、微服务架构等）、核心设计原则（如模块化、高内聚低耦合）以及主要功能块的划分依据。]
*   **块定义 (Block Definition)**: (至少定义5-8个核心功能块)
    *   `**块名称**: [组件名称, 例如: PowerControlModule]`
        *   **概述 (Overview)**: [用一段话，以叙述性的方式描述这个模块在整个系统中的角色、它的核心设计理念以及它如何与其他主要模块交互。]
        *   **核心职责 (Core Responsibilities)**: [使用列表清晰描述该模块的核心功能。]
        *   **关键属性 (Attributes)**: `- attributeName: Type [unit] {{range}}` (例如: `- batteryLevel: Real [%] {{0..100}}`)
        *   **核心操作 (Operations)**: `- operationName(param1: Type): ReturnType` (例如: `- enablePowerDistribution(voltage: Volts): Status`)
*   **内部结构与交互原理 (Internal Block Diagram - IBD)**:
    *   `**上下文**: [父级块名称]`
        *   **设计阐述**: [描述该模块内部设计的原理。为什么选择这些子组件？它们之间的连接（如数据总线、控制信号）是如何协同工作以实现整体功能的？]
        *   **组成部分 (Parts)**: 列出该父块由哪些子块实例构成。
        *   **连接关系 (Connectors)**: 描述各部分之间通过端口建立的连接。 `[part1.portA] <--> [part2.portB]`

### 3. 活动流程 (Activity) - 重点关注
*   **目标**: 描述关键的工作流程或数据流。
*   **格式要求**:
    *   `**活动名称**: [流程名称, 例如: 系统启动自检流程]`
    *   **流程概述**: [用一段描述性文字，介绍此活动的目标、范围以及它在系统整体运行中的关键作用。例如，“此流程描述了用户从按下开机按钮到系统进入待机状态的完整自检和初始化序列，确保所有硬件模块正常...”]
    *   **输入/输出 (Parameters)**: `IN: boot_command: Command`, `OUT: system_status: StatusReport`
    *   **步骤序列 (Actions)**: [详细描述步骤]

### 4. 状态机行为 (State Machine) - 重点关注
*   **目标**: 描述一个关键组件在其生命周期内的行为模式。
*   **格式要求**:
    *   `**上下文块**: [拥有此状态机的组件名称]`
    *   **行为模型概述**: [用一段话概括该组件的生命周期和行为模式。解释选择这些主要状态的原因，以及它们如何代表组件在现实世界中的运行模式，例如：“飞行控制器的行为模型定义了从‘地面准备’到‘飞行’再到‘任务完成’的完整生命周期...”]
    *   **状态定义**: (至少定义5-8个有意义的状态)
        *   `**状态名称**: [例如: Standby]`
            *   **描述**: [简要说明此状态的意义。]
            *   `entry / [进入时执行的动作]`
            *   `do / [在该状态下持续执行的活动]`
            *   `exit / [退出时执行的动作]`
    *   **转换 (Transitions)**: `[源状态] --[触发事件[守卫条件] / 效果动作]--> [目标状态]`

### 5. 用例场景 (Use Case)
*   **目标**: 从用户视角描述系统提供的服务。
*   **格式要求**:
    *   `**用例名称**: [用例的简洁动词短语]`
    *   **场景简述 (Narrative)**: [用故事性的语言简要描述这个用例的典型场景，帮助读者快速理解参与者和系统的交互故事。例如，“农民张三需要对他的麦田进行农药喷洒，他通过地面站规划好飞行路线并上传给无人机，然后启动一键起飞...”]
    *   **参与者 (Actors)**: `Primary: [主要使用者]`, `Secondary: [辅助系统/用户]`
    *   **基本流程 (Happy Path)**: [详细描述步骤]
    *   **备选/异常流程**: [详细描述步骤]

### 6. 参数关系 (Parametric)
*   **目标**: 定义关键性能参数间的数学或物理约束。
*   **格式要求**:
    *   `**约束块**: [约束关系的名称, 例如: VehicleDragEquation]`
    *   **物理意义与应用**: [解释该约束公式所代表的物理或数学原理，以及它在系统性能分析中的具体应用。例如，“此公式用于计算飞行器在不同速度和海拔下的空气阻力，是评估能耗和续航能力的关键...”]
    *   **参数 (Parameters)**: [列出参数]
    *   **约束公式 (Constraint)**: [列出公式]

### 7. 交互序列 (Sequence)
*   **目标**: 展示特定场景下组件间的消息时序。
*   **格式要求**:
    *   `**场景名称**: [描述一个具体的交互场景]`
    *   **交互背景**: [描述此交互序列发生的具体情境。是什么事件触发了这个序列？它的最终目标是什么？例如，“本序列描述了当传感器检测到温度超过阈值时，系统触发过热保护并通知用户的完整过程。”]
    *   **涉及的生命线 (Lifelines)**: `[BlockA]`, `[BlockB]`, `[Controller]`
    *   **消息序列 (Messages)**: [详细描述消息]

请确保你的扩展内容：
1. 使用专业的系统工程术语和概念
2. 提供具体的技术细节，避免泛泛而谈
3. 包含足够的数值指标和量化描述
4. 使用清晰的结构和层次，便于后续建模
5. 保持内部一致性，避免矛盾的描述
6. 特别关注SysML中最重要且常用的的图表类型（需求、块定义和内部块、活动、状态机）
7. 不要在文档末尾添加总结、结语或其他元内容，只提供纯粹的技术文档内容
8. 与数学公式有关的内容，不要使用LaTeX格式，而是直接提供数学公式文本形式

你的输出应是一份结构化、专业且全面的技术文档，使用Markdown格式，包含适当的标题、列表、表格和强调。
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
        
        initial_llm = CustomChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.base_url,
            temperature=0.4
        )
        
        initial_chain = initial_prompt | initial_llm 
        initial_content = ""
        for chunk in initial_chain.stream({"requirement": state.input_short_req}):
            reasoning = chunk.additional_kwargs.get("reasoning_content")
            if(reasoning):
                print(reasoning, end="", flush=True)
            else:
                initial_content += chunk.content
                print(chunk.content, end="", flush=True)

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
            
            enhance_llm = CustomChatOpenAI(
                model=settings.llm_model,
                api_key=settings.openai_api_key,
                base_url=settings.base_url,
                temperature=0.1
            )
            
            enhance_chain = enhance_prompt | enhance_llm
            enhanced_content = ""
            for chunk in enhance_chain.stream({"initial_content": initial_content}):
                reasoning = chunk.additional_kwargs.get("reasoning_content")
                if(reasoning):
                    print(reasoning, end="", flush=True)
                else:
                    enhanced_content += chunk.content
                    print(chunk.content, end="", flush=True)

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