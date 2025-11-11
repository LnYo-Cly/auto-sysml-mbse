import os
from typing import Optional
from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    """项目配置"""
    
    # LLM配置
    llm_model: str = os.getenv("LLM_MODEL", "glm-4.6")
    embedding_model: str = os.getenv("EMBEDDING_MODEL", "embedding-3")
    embedding_dimensions: int = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
    glm_thinking: str = os.getenv("GLM_THINKING", "disabled")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    base_url: str = os.getenv("BASE_URL", "https://open.bigmodel.cn/api/paas/v4")
    max_tokens: int = int(os.getenv("MAX_TOKENS", "65536"))
    
    # 日志配置
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    
    # 工作流配置
    save_stages: bool = os.getenv("SAVE_STAGES", "true").lower() == "true"
    enable_quality_enhancement: bool = os.getenv("ENABLE_QUALITY_ENHANCEMENT", "true").lower() == "true"
    # 文档处理配置
    max_chunk_tokens: int = int(os.getenv("MAX_CHUNK_TOKENS", "2000"))
    chunk_overlap_tokens: int = int(os.getenv("CHUNK_OVERLAP_TOKENS", "200"))
    
    # 任务分类配置
    task_extraction_enhanced: bool = os.getenv("TASK_EXTRACTION_ENHANCED", "true").lower() == "true"
    task_extraction_similarity_threshold: float = float(os.getenv("TASK_EXTRACTION_SIMILARITY_THRESHOLD", "0.7"))
    task_extraction_min_content_length: int = int(os.getenv("TASK_EXTRACTION_MIN_CONTENT_LENGTH", "50"))
    
   
    # 路径配置
    requirement_expansion_examples_path: Optional[str] = os.getenv(
        "REQUIREMENT_EXPANSION_EXAMPLES_PATH", 
        "data/examples/high_quality_example.md"
    )
    
    class Config:
        env_file = ".env"
        case_sensitive = False

settings = Settings()