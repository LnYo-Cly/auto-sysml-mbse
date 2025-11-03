from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from enum import Enum

class ProcessStatus(str, Enum):
    """处理状态枚举"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    NOT_STARTED = "not_started"

class SysMLTask(BaseModel):
    """SysML任务"""
    id: str = Field(description="任务ID")
    type: str = Field(description="SysML图表类型")
    content: str = Field(description="任务内容")
    status: ProcessStatus = Field(default=ProcessStatus.NOT_STARTED, description="任务状态")
    result: Optional[Any] = Field(default=None, description="任务结果")
    error: Optional[str] = Field(default=None, description="错误信息")

class WorkflowState(BaseModel):
    """工作流状态"""
    
    # 输入 - 两种输入方式：文本或文档
    input_short_req: str = Field(default="", description="用户输入的简短需求")
    input_doc_path: str = Field(default="", description="输入文档路径")
    
    # 需求扩展阶段
    initial_expanded_content: Optional[str] = Field(default=None, description="初始扩展内容")
    expanded_content: Optional[str] = Field(default=None, description="最终扩展内容")
    
    # 文档分块阶段
    text_chunks: List[str] = Field(default_factory=list, description="文档分块列表")
    chunk_token_counts: List[int] = Field(default_factory=list, description="每个分块的token数量")
    
    # 任务分类阶段
    assigned_tasks: List[SysMLTask] = Field(default_factory=list, description="分配的任务列表")
    tasks_assigned: bool = Field(default=False, description="是否已完成任务分配")
    
    # 输出相关
    output_dir: Optional[str] = Field(default=None, description="输出目录")
    detailed_task_results_file: Optional[str] = Field(default=None, description="详细任务结果文件路径")
    
    # 配置
    save_stages: bool = Field(default=True, description="是否保存中间阶段文档")
    enable_quality_enhancement: bool = Field(default=True, description="是否启用质量提升")
    max_chunk_tokens: int = Field(default=2000, description="每个分块的最大token数")
    
    # 错误处理
    error_message: Optional[str] = Field(default=None, description="错误信息")
    
    # 状态
    status: ProcessStatus = Field(default=ProcessStatus.PENDING, description="处理状态")
    
    class Config:
        arbitrary_types_allowed = True