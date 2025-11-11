import logging
from langgraph.graph import StateGraph, END, START
from graph.workflow_state import WorkflowState, ProcessStatus
from agents.requirement_expander import expand_requirement
from agents.document_processor import process_document
from agents.task_classifier import classify_and_assign_tasks
from agents.fusion_agent import fusion_agent
from agents.xml_generator_agent import xml_generator_agent


logger = logging.getLogger(__name__)


def should_process_document(state: WorkflowState) -> str:
    """决定是否需要处理文档"""
    if state.input_doc_path or state.expanded_content:
        return "document_processing"
    return END


def should_classify_tasks(state: WorkflowState) -> str:
    """决定是否需要进行任务分类"""
    if state.text_chunks or state.expanded_content:
        return "task_classification"
    return END

def should_generate_xml(state: WorkflowState) -> str:
    """决定是否需要生成XML"""
    if state.fusion_status == "completed":
        logger.info("✅ 融合完成，进入XML生成阶段")
        return "xml_generation"
    
    logger.info("⚠️ 融合未完成，跳过XML生成阶段")
    return END

def should_run_fusion(state: WorkflowState) -> str:
    """决定是否需要运行融合流程"""
    # 检查是否有已完成的任务
    if state.assigned_tasks:
        completed_tasks = [
            t for t in state.assigned_tasks 
            if t.status == ProcessStatus.COMPLETED
        ]
        if completed_tasks:
            logger.info(f"✅ 发现 {len(completed_tasks)} 个已完成任务，进入融合阶段")
            return "fusion"
    
    logger.info("⚠️ 没有已完成的任务，跳过融合阶段")
    return END

def create_workflow() -> StateGraph:
    """创建LangGraph工作流"""
    
    # 创建状态图
    workflow = StateGraph(WorkflowState)
    
    # 添加节点
    workflow.add_node("requirement_expansion", expand_requirement)
    workflow.add_node("document_processing", process_document)
    workflow.add_node("task_classification", classify_and_assign_tasks)
    workflow.add_node("fusion", fusion_agent)
    workflow.add_node("xml_generation", xml_generator_agent)
    
    # 设置入口点
    workflow.set_entry_point("requirement_expansion")
    
    # 需求扩展 → 文档处理
    workflow.add_conditional_edges(
        "requirement_expansion",
        should_process_document,
        {
            "document_processing": "document_processing",
            END: END
        }
    )
    
    # 文档处理 → 任务分类
    workflow.add_conditional_edges(
        "document_processing",
        should_classify_tasks,
        {
            "task_classification": "task_classification",
            END: END
        }
    )

    # 多sysml任务处理 → 融合
    workflow.add_conditional_edges(
        "task_classification",
        should_run_fusion,
        {
            "fusion": "fusion",
            END: END
        }
    )

    # 融合 → XML生成
    workflow.add_conditional_edges(
        "fusion",
        should_generate_xml,
        {
            "xml_generation": "xml_generation",
            END: END
        }
    )
    
    # 任务分类 → 结束
    workflow.add_edge("xml_generation", END)
    
    # 编译工作流
    return workflow.compile()