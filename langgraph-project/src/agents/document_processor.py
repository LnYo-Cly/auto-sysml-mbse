"""
文档处理Agent
负责读取文档并将其分割为多个chunk
"""
import logging
import os
from typing import List
import docx
import tiktoken

from graph.workflow_state import WorkflowState, ProcessStatus
from config.settings import settings

logger = logging.getLogger(__name__)


def count_tokens(text: str) -> int:
    """
    计算文本的token数量
    
    参数:
        text: 要计算的文本
        
    返回:
        token数量
    """
    try:
        encoding = tiktoken.encoding_for_model("gpt-4")
        return len(encoding.encode(text))
    except Exception as e:
        logger.warning(f"使用gpt-4编码失败，使用cl100k_base: {str(e)}")
        encoding = tiktoken.get_encoding("cl100k_base")
        return len(encoding.encode(text))


def read_word_doc(doc_path: str) -> str:
    """
    读取Word文档
    
    参数:
        doc_path: 文档路径
        
    返回:
        文档内容
    """
    try:
        document = docx.Document(doc_path)
        full_text = []
        for para in document.paragraphs:
            if para.style and para.style.name.startswith('Heading'):
                level = int(para.style.name.split(' ')[1])
                full_text.append("\n" + "#" * level + " " + para.text.strip())
            else:
                full_text.append(para.text.strip())
        return "\n\n".join(full_text)
    except Exception as e:
        logger.error(f"读取Word文档失败: {str(e)}", exc_info=True)
        raise ValueError(f"读取Word文档失败: {str(e)}")


def read_text_file(file_path: str) -> str:
    """
    读取文本文件（支持 .txt, .md 等）
    
    参数:
        file_path: 文件路径
        
    返回:
        文件内容
    """
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        logger.error(f"读取文本文件失败: {str(e)}", exc_info=True)
        raise ValueError(f"读取文本文件失败: {str(e)}")


def read_document(doc_path: str) -> str:
    """
    读取文档（自动识别文件类型）
    
    参数:
        doc_path: 文档路径
        
    返回:
        文档内容
    """
    _, ext = os.path.splitext(doc_path)
    ext = ext.lower()
    
    if ext in ['.docx', '.doc']:
        return read_word_doc(doc_path)
    elif ext in ['.txt', '.md', '.markdown']:
        return read_text_file(doc_path)
    else:
        # 尝试作为文本文件读取
        logger.warning(f"未知文件类型 {ext}，尝试作为文本文件读取")
        return read_text_file(doc_path)


def split_text_into_chunks(text: str, max_tokens: int = 2000, overlap_tokens: int = 200) -> List[str]:
    """
    将文本分割成多个chunk，按token数量分割
    
    参数:
        text: 要分割的文本
        max_tokens: 每个chunk的最大token数
        overlap_tokens: chunk之间的重叠token数
        
    返回:
        分割后的chunk列表
    """
    encoding = tiktoken.get_encoding("cl100k_base")
    tokens = encoding.encode(text)
    
    chunks = []
    start = 0
    
    while start < len(tokens):
        # 计算当前chunk的结束位置
        end = start + max_tokens
        
        # 获取当前chunk的tokens
        chunk_tokens = tokens[start:end]
        
        # 解码回文本
        chunk_text = encoding.decode(chunk_tokens)
        chunks.append(chunk_text)
        
        # 移动到下一个chunk，考虑重叠
        start = end - overlap_tokens
        
        # 如果剩余tokens不足overlap，直接跳到末尾
        if start + max_tokens >= len(tokens):
            if start < len(tokens):
                remaining_tokens = tokens[start:]
                remaining_text = encoding.decode(remaining_tokens)
                if remaining_text.strip():  # 只添加非空内容
                    chunks.append(remaining_text)
            break
    
    logger.info(f"文本分割完成: 总tokens={len(tokens)}, 分割为{len(chunks)}个chunks")
    return chunks


def process_document(state: WorkflowState) -> WorkflowState:
    """
    处理文档，提取文本内容并分割为chunks
    
    参数:
        state: 当前工作流状态
        
    返回:
        更新后的工作流状态
    """
    # 检查是否有文档路径
    if not state.input_doc_path:
        # 如果没有文档路径，检查是否有扩展内容需要分块
        if state.expanded_content:
            logger.info("✅ 使用扩展内容进行分块")
            text_content = state.expanded_content
        else:
            logger.warning("⚠️ 既没有提供文档路径，也没有扩展内容")
            # 不设置为失败，让工作流继续
            return state
    else:
        # 有文档路径，读取文档
        if not os.path.exists(state.input_doc_path):
            state.error_message = f"文档路径不存在: {state.input_doc_path}"
            state.status = ProcessStatus.FAILED
            return state
        
        try:
            logger.info(f"📖 开始读取文档: {state.input_doc_path}")
            text_content = read_document(state.input_doc_path)
            
            # 如果已有扩展内容，合并
            if state.expanded_content:
                logger.info("📝 合并扩展内容和文档内容")
                text_content = state.expanded_content + "\n\n" + text_content
            else:
                state.expanded_content = text_content
                
            logger.info(f"✅ 文档读取成功，文本长度: {len(text_content)} 字符")
        except Exception as e:
            logger.error(f"❌ 文档处理失败: {str(e)}", exc_info=True)
            state.error_message = f"文档处理失败: {str(e)}"
            state.status = ProcessStatus.FAILED
            return state
    
    try:
        # 分割文本为chunks
        logger.info(f"📄 开始分割文本，最大token数: {state.max_chunk_tokens}")
        chunks = split_text_into_chunks(
            text_content, 
            max_tokens=state.max_chunk_tokens,
            overlap_tokens=200  # 可以配置
        )
        
        # 计算每个chunk的token数
        chunk_tokens = [count_tokens(chunk) for chunk in chunks]
        
        # 保存到状态
        state.text_chunks = chunks
        state.chunk_token_counts = chunk_tokens
        
        # 打印分块信息
        print("\n" + "="*80)
        print(f"📄 文档分块完成")
        print("="*80)
        print(f"总字符数: {len(text_content)}")
        print(f"总token数: {sum(chunk_tokens)}")
        print(f"分块数量: {len(chunks)}")
        print(f"平均每块token数: {sum(chunk_tokens) // len(chunks) if chunks else 0}")
        print("\n各分块token数:")
        for i, tokens in enumerate(chunk_tokens, 1):
            print(f"  Chunk {i}: {tokens} tokens")
        print("="*80 + "\n")
        
        logger.info(f"✅ 文档分块完成: {len(chunks)} 个chunks")
        
        return state
        
    except Exception as e:
        logger.error(f"❌ 文本分块失败: {str(e)}", exc_info=True)
        state.error_message = f"文本分块失败: {str(e)}"
        state.status = ProcessStatus.FAILED
        return state