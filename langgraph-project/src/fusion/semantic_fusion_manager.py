# step4_semantic_fusion/semantic_fusion_manager.py

import sys
import os
import logging
import concurrent.futures
from typing import Dict, Any, Optional, Tuple, List

from flask import json

logger = logging.getLogger(__name__)

# 设置项目根目录以便导入模块
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from connections import config
from connections.database_connectors import get_pg_connection, setup_pgvector_table
from connections.embedding_client import OllamaEmbeddingClient, GLMEmbeddingClient
from fusion.llm_arbiter import LLMArbiter # <--- 导入新的仲裁者
from config.settings import settings


# 定义相似度结果的数据结构
SemanticSearchResult = Tuple[bool, Optional[str], Optional[float]]

class SemanticFusionManager:
    """
    负责通过向量相似度来识别潜在的重复实体。
    """
    # 余弦相似度阈值，只有高于此值的才被认为是强相似
    SIMILARITY_THRESHOLD = 0.98

    def __init__(self):
        """初始化管理器，连接pgvector并准备Ollama客户端。"""
        self.pg_conn = get_pg_connection()
        # self.embed_client = GLMEmbeddingClient()
        if settings.embedding_service == "ollama":
            self.embed_client = OllamaEmbeddingClient()
        elif settings.embedding_service == "glm":
            self.embed_client = GLMEmbeddingClient()

        self.llm_arbiter = LLMArbiter() # <--- 在这里初始化仲裁者
        if not self.pg_conn or not self.embed_client.client:
            raise ConnectionError("无法初始化SemanticFusionManager，请检查数据库或Ollama连接。")
        # ✅ 初始化时检查并创建表
        self._ensure_table_exists()
        print("SemanticFusionManager 初始化成功。")

    def _ensure_table_exists(self):
        """
        检查 pgvector 表是否存在，如果不存在则创建
        """
        try:
            with self.pg_conn.cursor() as cursor:
                # 检查表是否存在
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_name = %s
                    );
                """, (config.PG_VECTOR_TABLE_NAME,))
                
                table_exists = cursor.fetchone()[0]
                
                if not table_exists:
                    print(f"⚠️ 表 '{config.PG_VECTOR_TABLE_NAME}' 不存在，正在创建...")
                    if setup_pgvector_table(self.pg_conn):
                        print(f"✅ 表 '{config.PG_VECTOR_TABLE_NAME}' 创建成功")
                    else:
                        raise Exception(f"无法创建表 '{config.PG_VECTOR_TABLE_NAME}'")
                else:
                    print(f"✅ 表 '{config.PG_VECTOR_TABLE_NAME}' 已存在")
                    
        except Exception as e:
            print(f"❌ 检查表失败: {e}")
            raise

    def find_similar_element(self, element: Dict[str, Any], canonical_key: str) -> SemanticSearchResult:
        """
        在向量数据库中查找与给定元素语义上最相似的实体。

        Args:
            element (Dict[str, Any]): 待检查的元素对象。

        Returns:
            一个元组 (is_similar, similar_key, similarity_score)，
            如果找到强相似项，is_similar为True。
        """
        element_name = element.get('name')
        element_type = element.get('type')
        
        print("\n开始语义相似性检查")

        # 如果元素没有名称或类型，则无法进行有意义的语义比较
        if not element_name or not element_type:
            return (False, None, None)

        # 1. 为当前元素的名称生成嵌入向量
        # 如果有 description，则包含在向量生成中以提升语义准确度
        element_desc = element.get('description', '')
        if element_desc:
            text_to_embed = f"A {element_type} named {element_name}: {element_desc}"
        else:
            text_to_embed = f"A {element_type} named {element_name}"
        
        print(f"  text_to_embed: {text_to_embed}")
        embedding = self.embed_client.get_embedding(text_to_embed)
        if not embedding:
            print(f"  - 警告: 无法为 '{element_name}' 生成向量，跳过语义检查。")
            return (False, None, None)

        # 2. 在pgvector中执行相似度搜索
        # '<=>' 是pgvector提供的余弦距离运算符 (0=完全相同, 1=不相关)
        # 相似度 = 1 - 距离
        # 重要: 排除当前元素本身，避免找到自己
        # 同时获取 description 用于 LLM 仲裁
        query = f"""
        SELECT canonical_key, element_description, 1 - (embedding <=> %s) AS similarity
        FROM {config.PG_VECTOR_TABLE_NAME}
        WHERE element_type = %s AND canonical_key != %s
        ORDER BY similarity DESC
        LIMIT 1;
        """
        
        with self.pg_conn.cursor() as cursor:
            # psycopg2需要将list转换为字符串
            cursor.execute(query, (str(embedding), element_type, canonical_key))
            result = cursor.fetchone()
            
        if result:
            similar_key, similar_desc, similarity_score = result
            if similarity_score >= self.SIMILARITY_THRESHOLD:
                # 找到了一个强相似的实体，现在触发LLM仲裁
                print(f"\n  - 向量召回: 找到潜在相似项 '{canonical_key}' -> '{similar_key}' (相似度: {similarity_score:.2f})")
                
                # 传递当前元素和相似元素的 description 给 LLM 仲裁
                is_truly_same = self.llm_arbiter.are_they_the_same_entity(
                    canonical_key,
                    element_desc,
                    similar_key,
                    similar_desc or ''  # 如果 similar_desc 为 None，使用空字符串
                )
                
                if is_truly_same:
                    # 只有在LLM确认后，才判定为重复
                    return (True, similar_key, similarity_score)

        # 未找到强相似实体
    
        return (False, None, None)

    def store_element_embedding(self, element: Dict[str, Any], canonical_key: str):
        """
        将一个新确认的元素的向量存入pgvector。

        Args:
            element (Dict[str, Any]): 要存储的元素对象。
            canonical_key (str): 该元素的规范键。
        """

        
        element_name = element.get('name')
        element_type = element.get('type')
        element_desc = element.get('description', '')  # 获取 description，如果没有则为空字符串


        # 没有 name 就用 canonical_key 的最后一部分
        if not element_name:
            element_name = canonical_key.split('::')[-1]
        if not element_type:
            print("  ⚠️ 元素缺少 type，跳过存储")
            return # 没有足够信息，不存储

        # 生成 embedding 时包含 description（与 find_similar_element 保持一致）
        if isinstance(element_desc, dict):
            element_desc = json.dumps(element_desc, ensure_ascii=False)
            text_to_embed = f"A {element_type} named {element_name}: {element_desc}"
        
        elif element_desc:
            text_to_embed = f"A {element_type} named {element_name}: {element_desc}"
        else:
            text_to_embed = f"A {element_type} named {element_name}"
        
        print(f"  text_to_embed: {text_to_embed}")

        embedding = self.embed_client.get_embedding(text_to_embed)
        if not embedding:
            return

        # ✅ 转换为 JSON 字符串用于查询
        if isinstance(embedding, (list, dict)):
            embedding_json = json.dumps(embedding)
        else:
            embedding_json = str(embedding)

        # 使用 INSERT ... ON CONFLICT (UPSERT) 语句，确保主键唯一
        query = f"""
        INSERT INTO {config.PG_VECTOR_TABLE_NAME} (canonical_key, element_name, element_type, element_description, embedding)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (canonical_key) DO UPDATE SET
            element_name = EXCLUDED.element_name,
            element_description = EXCLUDED.element_description,
            embedding = EXCLUDED.embedding;
        """
        # ========== 添加调试打印 ==========
        print(f"\n  [DEBUG] 准备执行 SQL")
        print(f"    表名: {config.PG_VECTOR_TABLE_NAME}")
        
        params = (canonical_key, element_name, element_type, element_desc, embedding_json)
        print(f"  [DEBUG] SQL 参数:")
        for i, param in enumerate(params):
            print(f"    参数 {i}: 类型={type(param)}, 值={str(param)[:100]}...")
        # ==================================
        
        with self.pg_conn.cursor() as cursor:
            cursor.execute(query, (canonical_key, element_name, element_type, element_desc, str(embedding_json)))
        self.pg_conn.commit()
    
    def get_embeddings_parallel(self, items: List[Tuple[str, str]]) -> List[Optional[List[float]]]:
        """
        并行生成向量
        
        Args:
            items: List of (text_to_embed, identifier_for_log)
            
        Returns:
            List[Optional[List[float]]]: 对应每个文本的嵌入向量
        """
        if not items:
            return []
        
        embeddings = [None] * len(items)
        
        def _worker(index, text, identifier):
            try:
                embedding = self.embed_client.get_embedding(text)
                if embedding:
                    logger.debug(f"✅ [{index}] 向量生成成功: {identifier}")
                return index, embedding
            except Exception as e:
                logger.error(f"❌ [{index}] 向量生成失败 ({identifier}): {e}")
                return index, None
        
        max_workers = min(len(items), 10)  # 最多10个并发
        logger.info(f"🚀 开始并行生成 {len(items)} 个向量 (并发数: {max_workers})")
        
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(_worker, i, item[0], item[1]) for i, item in enumerate(items)]
            
            for future in concurrent.futures.as_completed(futures):
                idx, emb = future.result()
                embeddings[idx] = emb
        
        success_count = sum(1 for e in embeddings if e is not None)
        logger.info(f"✅ 并行向量生成完成: {success_count}/{len(items)} 成功")
        
        return embeddings
    
    def search_candidate_only(self, embedding: List[float], element_type: str, canonical_key: str) -> Optional[Dict[str, Any]]:
        """
        仅搜索相似候选，不进行 LLM 仲裁
        
        Args:
            embedding: 嵌入向量
            element_type: 元素类型
            canonical_key: 规范键
            
        Returns:
            如果找到相似候选，返回 {'key': str, 'description': str, 'similarity': float}
        """
        query = f"""
        SELECT canonical_key, element_description, 1 - (embedding <=> %s) AS similarity
        FROM {config.PG_VECTOR_TABLE_NAME}
        WHERE element_type = %s AND canonical_key != %s
        ORDER BY similarity DESC
        LIMIT 1;
        """
        
        with self.pg_conn.cursor() as cursor:
            cursor.execute(query, (str(embedding), element_type, canonical_key))
            result = cursor.fetchone()
        
        if result:
            similar_key, similar_desc, similarity_score = result
            if similarity_score >= self.SIMILARITY_THRESHOLD:
                return {
                    'key': similar_key,
                    'description': similar_desc or '',
                    'similarity': similarity_score
                }
        
        return None
    
    def store_embedding_direct(self, canonical_key: str, element: Dict[str, Any], embedding: List[float]):
        """
        直接存储嵌入向量（不再重新生成）
        
        Args:
            canonical_key: 规范键
            element: 元素数据
            embedding: 已生成的嵌入向量
        """
        element_name = element.get('name', canonical_key.split('::')[-1])
        element_type = element.get('type')
        element_desc = element.get('description', '')
        
        if not element_type:
            logger.warning(f"⚠️ 元素缺少 type，跳过存储: {canonical_key}")
            return
        
        # 处理 description
        if isinstance(element_desc, dict):
            element_desc = json.dumps(element_desc, ensure_ascii=False)
        
        # 转换向量为 JSON 字符串
        if isinstance(embedding, (list, dict)):
            embedding_json = json.dumps(embedding)
        else:
            embedding_json = str(embedding)
        
        query = f"""
        INSERT INTO {config.PG_VECTOR_TABLE_NAME} (canonical_key, element_name, element_type, element_description, embedding)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (canonical_key) DO UPDATE SET
            element_name = EXCLUDED.element_name,
            element_description = EXCLUDED.element_description,
            embedding = EXCLUDED.embedding;
        """
        
        try:
            with self.pg_conn.cursor() as cursor:
                cursor.execute(query, (canonical_key, element_name, element_type, element_desc, str(embedding_json)))
            self.pg_conn.commit()
            logger.debug(f"✅ 向量存储成功: {canonical_key}")
        except Exception as e:
            logger.error(f"❌ 向量存储失败 ({canonical_key}): {e}")
