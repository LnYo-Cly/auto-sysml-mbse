"""
任务分类Agent
对文档chunks进行分类，提取SysML任务
"""
import logging
import uuid
import json
import os
from typing import List
from collections import defaultdict
from datetime import datetime

from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import JsonOutputParser
from pydantic import BaseModel, Field
from json_repair import repair_json

from graph.workflow_state import WorkflowState, SysMLTask, ProcessStatus
from config.settings import settings

logger = logging.getLogger(__name__)

# 导入各个sysml-agent
try:
    from agents.diagram_agents.req_agent import requirement_agent
except ImportError as e:
    logger.warning(f"无法导入需求图Agent: {e}")
    requirement_agent = None
    
try:
    from agents.diagram_agents.act_agent import activity_agent
except ImportError as e:
    logger.warning(f"无法导入活动图Agent: {e}")
    activity_agent = None
    
try:
    from agents.diagram_agents.bdd_ibd_agent import bdd_ibd_agent
except ImportError as e:
    logger.warning(f"无法导入BDD/IBD图Agent: {e}")
    bdd_ibd_agent = None
    
try:
    from agents.diagram_agents.par_agent import parameter_agent
except ImportError as e:
    logger.warning(f"无法导入参数图Agent: {e}")
    parameter_agent = None
    
try:
    from agents.diagram_agents.uc_agent import usecase_agent
except ImportError as e:
    logger.warning(f"无法导入用例图Agent: {e}")
    usecase_agent = None
    
try:
    from agents.diagram_agents.stm_agent import state_machine_agent
except ImportError as e:
    logger.warning(f"无法导入状态机图Agent: {e}")
    state_machine_agent = None
    
try:
    from agents.diagram_agents.sd_agent import sequence_agent
except ImportError as e:
    logger.warning(f"无法导入序列图Agent: {e}")
    sequence_agent = None


class SysMLTaskExtraction(BaseModel):
    """SysML任务提取结果项"""
    type: str = Field(description="SysML图表类型")
    content: str = Field(description="提取的相关内容")


class SysMLTaskExtractionResult(BaseModel):
    """SysML任务提取结果集合"""
    tasks: List[SysMLTaskExtraction] = Field(description="提取的SysML任务列表")


# 系统提示模板
SYSTEM_PROMPT_EXTRACT_AND_CLASSIFY = """
你是一个系统设计助手，专注于MBSE（Model-Based Systems Engineering）。你的任务是分析提供的文本内容，精确识别其中包含的SysML模型相关信息，并将它们分类以便分配给专门的SysML建模Agent。

请将识别出的信息归类为以下类型之一，并提取出对应的具体文本内容：

## 重点关注的四种主要SysML图表类型：

1. **Requirement (需求):** - 最重要
   - 描述系统应实现的功能或非功能约束
   - 包括需求描述、优先级、验证方法
   - 包括需求之间的依赖、派生、验证等关系

2. **Block Definition and Internal Block (块定义和内部块):** - 最重要
   - 描述系统中的结构组件（块）、其属性、端口、操作、以及与其他块的关系
   - 描述块的内部结构，包括部件、连接器和端口

3. **Activity (活动):** - 最重要
   - 描述系统执行的步骤、动作、控制流、并发或选择路径
   - 包括活动步骤、决策点、分支条件、并发活动

4. **State Machine (状态机):** - 最重要
   - 描述系统或组件的行为状态及转换、状态名称、事件等
   - 包括状态名称、转换条件、触发事件

## 其他SysML图表类型：

5. **Use Case (用例):** 
   - 描述用户与系统之间的交互、系统提供的功能、参与者和场景

6. **Parameter (参数):** 
   - 描述系统参数和约束关系
   - 包括数学/物理公式、约束条件

7. **Sequence (序列):** 
   - 描述组件之间的交互序列
   - 包括消息、调用、响应、返回值

你的输出必须是一个JSON格式的任务列表，格式如下：
{{
  "tasks": [
    {{
      "type": "图表类型",
      "content": "提取的具体内容"
    }}
  ]
}}
"""

USER_PROMPT_EXTRACT_AND_CLASSIFY = """
请从以下文本中提取适合创建各种SysML图表的内容，并按照图表类型进行分类：

{text}

请确保：
1. 全面分析文本，不遗漏任何有价值的信息
2. 准确分类每个内容片段到对应的SysML图表类型
3. 提取的内容足够详细，同时尽量简洁
4. 输出格式符合要求，每个任务有明确的type和content字段
"""


def get_output_dir() -> str:
    """
    获取输出目录路径，如果不存在则创建
    
    返回:
        输出目录的绝对路径
    """
    # 获取项目根目录（src的父目录）
    current_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(os.path.dirname(current_dir))
    
    # 输出目录路径
    output_dir = os.path.join(project_root, "data", "output")
    
    # 如果目录不存在，创建它
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)
        logger.info(f"创建输出目录: {output_dir}")
    
    return output_dir


def save_merged_tasks(tasks: List[SysMLTaskExtraction], output_dir: str = None) -> str:
    """
    保存合并后的任务到JSON文件
    
    参数:
        tasks: 任务列表
        output_dir: 输出目录（可选）
        
    返回:
        保存的文件路径
    """
    try:
        # 获取输出目录
        if output_dir is None:
            output_dir = get_output_dir()
        
        # 生成文件名（带时间戳）
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"merged_tasks_{timestamp}.json"
        filepath = os.path.join(output_dir, filename)
        
        # 准备要保存的数据
        tasks_data = {
            "timestamp": timestamp,
            "total_tasks": len(tasks),
            "tasks": []
        }
        
        # 转换任务数据
        for i, task in enumerate(tasks, 1):
            task_data = {
                "index": i,
                "type": task.type,
                "content": task.content,
                "content_length": len(task.content)
            }
            tasks_data["tasks"].append(task_data)
        
        # 保存到文件
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(tasks_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✅ 合并后的任务已保存到: {filepath}")
        
        # 打印统计信息
        print(f"\n{'='*80}")
        print(f"📁 合并任务已保存")
        print(f"{'='*80}")
        print(f"文件路径: {filepath}")
        print(f"任务总数: {len(tasks)}")
        print("\n任务类型统计:")
        task_types = {}
        for task in tasks:
            task_types[task.type] = task_types.get(task.type, 0) + 1
        
        for task_type, count in sorted(task_types.items()):
            print(f"  📋 {task_type}: {count} 个")
        print(f"{'='*80}\n")
        
        return filepath
        
    except Exception as e:
        logger.error(f"❌ 保存合并任务失败: {str(e)}", exc_info=True)
        return ""


def classify_chunk(chunk: str, chunk_index: int, llm, output_parser) -> List[SysMLTaskExtraction]:
    """
    对单个chunk进行分类
    
    参数:
        chunk: 文本块
        chunk_index: 块索引
        llm: 语言模型
        output_parser: 输出解析器
        
    返回:
        任务列表
    """
    try:
        logger.info(f"🔍 分类第 {chunk_index + 1} 个chunk")
        
        # 使用ChatPromptTemplate而不是PromptTemplate
        prompt = ChatPromptTemplate.from_messages([
            ("system", SYSTEM_PROMPT_EXTRACT_AND_CLASSIFY),
            ("human", USER_PROMPT_EXTRACT_AND_CLASSIFY)
        ])
        
        # 创建链
        chain = prompt | llm | output_parser
        
        # 流式调用
        print(f"\n{'='*80}")
        print(f"🔍 正在分析 Chunk {chunk_index + 1}...")
        print(f"{'='*80}\n")
        
        # 流式输出 - 注意：JsonOutputParser的stream返回的是字典对象
        final_result = None
        for partial_result in chain.stream({"text": chunk}):
            # partial_result 是一个字典对象，不是字符串
            # 打印当前的部分结果（美化输出）
            if isinstance(partial_result, dict):
                # 只在有tasks字段时才打印
                if 'tasks' in partial_result and partial_result['tasks']:
                    print(f"\r正在生成... 已识别 {len(partial_result['tasks'])} 个任务", end="", flush=True)
                final_result = partial_result
        
        print()  # 换行
        
        print(f"\n{'='*80}")
        print(f"✅ Chunk {chunk_index + 1} 分析完成")
        print(f"{'='*80}\n")
        
        # 最后一个结果就是完整的结果
        result = final_result
        
        # 使用json_repair修复可能的JSON问题（如果需要）
        if result:
            try:
                result = json.loads(repair_json(json.dumps(result)))
            except Exception as repair_error:
                logger.warning(f"JSON修复失败，使用原始结果: {repair_error}")
        
        # 转换为SysMLTaskExtraction对象
        tasks = []
        if result and 'tasks' in result:
            for task_dict in result['tasks']:
                tasks.append(SysMLTaskExtraction(
                    type=task_dict.get('type', ''),
                    content=task_dict.get('content', '')
                ))
        
        logger.info(f"✅ Chunk {chunk_index + 1} 提取到 {len(tasks)} 个任务")
        for i, task in enumerate(tasks, 1):
            logger.info(f"   {i}. {task.type}: {task.content[:50]}...")
        
        return tasks
        
    except Exception as e:
        logger.error(f"❌ Chunk {chunk_index + 1} 分类失败: {str(e)}", exc_info=True)
        return []


def merge_tasks_by_type(tasks: List[SysMLTaskExtraction]) -> List[SysMLTaskExtraction]:
    """
    按类型合并任务
    
    参数:
        tasks: 任务列表
        
    返回:
        合并后的任务列表
    """
    logger.info(f"🔄 开始合并任务，原始任务数: {len(tasks)}")
    
    task_groups = defaultdict(list)
    for task in tasks:
        if task.type and task.content:  # 确保type和content都不为空
            task_groups[task.type].append(task.content)
    
    merged_tasks = []
    for task_type, contents in task_groups.items():
        merged_content = "\n\n---\n\n".join(contents)
        merged_tasks.append(SysMLTaskExtraction(
            type=task_type,
            content=merged_content.strip()
        ))
        logger.info(f"   📦 {task_type}: 合并了 {len(contents)} 个内容片段")
    
    logger.info(f"✅ 合并完成，最终任务数: {len(merged_tasks)}")
    return merged_tasks


def classify_and_assign_tasks(state: WorkflowState) -> WorkflowState:
    """
    对chunks进行分类并分配任务
    
    参数:
        state: 当前工作流状态
        
    返回:
        更新后的工作流状态
    """
    # 检查输入
    if not state.text_chunks:
        logger.warning("⚠️ 没有文本块可供分类，尝试使用expanded_content")
        if state.expanded_content:
            state.text_chunks = [state.expanded_content]
        else:
            state.error_message = "没有可分类的文本内容"
            state.status = ProcessStatus.FAILED
            return state
    
    try:
        logger.info(f"📋 开始对 {len(state.text_chunks)} 个chunks进行任务分类")
        
        # 创建LLM和解析器
        llm = ChatOpenAI(
            model=settings.llm_model,
            api_key=settings.openai_api_key,
            base_url=settings.base_url,
            temperature=0.0,
            streaming=True
        )
        
        output_parser = JsonOutputParser(pydantic_object=SysMLTaskExtractionResult)
        
        # 对每个chunk进行分类
        all_tasks = []
        for i, chunk in enumerate(state.text_chunks):
            logger.info(f"\n{'='*80}")
            logger.info(f"📄 处理 Chunk {i+1}/{len(state.text_chunks)}")
            logger.info(f"📏 Chunk长度: {len(chunk)} 字符")
            logger.info(f"{'='*80}")
            
            tasks = classify_chunk(chunk, i, llm, output_parser)
            all_tasks.extend(tasks)
        
        logger.info(f"📊 总共提取了 {len(all_tasks)} 个原始任务")
        
        # 按类型合并任务
        merged_tasks = merge_tasks_by_type(all_tasks)
        logger.info(f"🔄 合并后有 {len(merged_tasks)} 个任务")
        
        # 保存合并后的任务到JSON文件
        if merged_tasks:
            output_dir = state.output_dir if state.output_dir else None
            saved_file = save_merged_tasks(merged_tasks, output_dir)
            if saved_file and not state.output_dir:
                state.output_dir = os.path.dirname(saved_file)
        
        # 转换为SysMLTask对象
        sysml_tasks = []
        for task in merged_tasks:
            task_id = f"TASK-{uuid.uuid4().hex[:8]}"
            sysml_task = SysMLTask(
                id=task_id,
                type=task.type,
                content=task.content,
                status=ProcessStatus.NOT_STARTED
            )
            sysml_tasks.append(sysml_task)
            
            logger.info(f"📝 创建任务 {task_id}")
            logger.info(f"   类型: {task.type}")
            logger.info(f"   内容长度: {len(task.content)} 字符")
            logger.info(f"   内容预览: {task.content[:100]}...")
        
        # 更新状态
        state.assigned_tasks = sysml_tasks
        state.tasks_assigned = True
        
        # 执行任务（调用各个sysml-agent）
        if sysml_tasks:
            state = execute_sysml_tasks(state)
        else:
            logger.warning("⚠️ 没有提取到任何任务")
        
        logger.info(f"✅ 任务分类和分配完成，共 {len(sysml_tasks)} 个任务")
        return state
        
    except Exception as e:
        logger.error(f"❌ 任务分类失败: {str(e)}", exc_info=True)
        state.error_message = f"任务分类失败: {str(e)}"
        state.status = ProcessStatus.FAILED
        return state


def execute_sysml_tasks(state: WorkflowState) -> WorkflowState:
    """
    执行SysML任务（调用各个agent）
    
    参数:
        state: 当前工作流状态
        
    返回:
        更新后的工作流状态
    """
    logger.info(f"🚀 开始执行 {len(state.assigned_tasks)} 个SysML任务")

    for task in state.assigned_tasks:
        try:
            logger.info(f"\n{'='*80}")
            logger.info(f"⚙️ 执行任务 {task.id}")
            logger.info(f"   类型: {task.type}")
            logger.info(f"{'='*80}\n")
            
            task.status = ProcessStatus.PROCESSING
            
            # 根据任务类型调用对应的agent
            if task.type == "Requirement" and requirement_agent:
                state = requirement_agent(state, task.id, task.content)
                
            elif task.type == "Activity" and activity_agent:
                state = activity_agent(state, task.id, task.content)
                
            elif task.type == "Block Definition and Internal Block" and bdd_ibd_agent:
                state = bdd_ibd_agent(state, task.id, task.content)
                
            elif task.type == "State Machine" and state_machine_agent:
                state = state_machine_agent(state, task.id, task.content)
                
            elif task.type == "Use Case" and usecase_agent:
                state = usecase_agent(state, task.id, task.content)
                
            elif task.type == "Parameter" and parameter_agent:
                state = parameter_agent(state, task.id, task.content)
                
            elif task.type == "Sequence" and sequence_agent:
                state = sequence_agent(state, task.id, task.content)
                
            else:
                logger.warning(f"⚠️ 不支持的任务类型或agent不可用: {task.type}")
                task.status = ProcessStatus.FAILED
                task.error = f"不支持的任务类型或agent不可用: {task.type}"
                continue
            
            # 更新任务状态
            for state_task in state.assigned_tasks:
                if state_task.id == task.id:
                    if state_task.status != ProcessStatus.FAILED:
                        state_task.status = ProcessStatus.COMPLETED
                    logger.info(f"✅ 任务 {task.id} 执行完成")
                    break
                    
        except Exception as e:
            logger.error(f"❌ 任务 {task.id} 执行失败: {str(e)}", exc_info=True)
            task.status = ProcessStatus.FAILED
            task.error = str(e)
    
    logger.info(f"\n{'='*80}")
    logger.info(f"🎉 所有任务执行完成")
    logger.info(f"{'='*80}\n")
    
    return state