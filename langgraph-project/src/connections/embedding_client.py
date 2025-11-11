# step2_connections/embedding_client.py

import ollama
from typing import List, Optional
from connections import config

from langchain_openai import OpenAIEmbeddings
from config.settings import settings

class OllamaEmbeddingClient:
    """
    一个用于与本地Ollama服务交互的客户端，
    专门用于生成文本嵌入（向量）。
    """
    def __init__(self):
        self.model = settings.ollama_embedding_model
        try:
            self.client = ollama.Client(host=settings.ollama_host)
            # 尝试与服务器通信以验证连接
            self.client.list()
            print(f"✅ Ollama 客户端初始化成功，已连接到 {settings.ollama_host}。")
        except Exception as e:
            print(f"❌ Ollama 客户端初始化失败: {e}")
            print("  请确保您已通过 'ollama serve' 命令启动了Ollama服务。")
            self.client = None

    def get_embedding(self, text: str) -> Optional[List[float]]:
        """
        为给定的文本生成嵌入向量。

        Args:
            text: 需要被向量化的字符串。

        Returns:
            一个浮点数列表表示的向量，如果失败则返回 None。
        """
        if not self.client:
            print("Ollama 客户端未初始化，无法生成嵌入。")
            return None
        
        try:
            response = self.client.embeddings(
                model=self.model,
                prompt=text
            )
            return response.get('embedding')
        except Exception as e:
            print(f"❌ 调用Ollama嵌入模型 '{self.model}' 失败: {e}")
            print("  请确保您已通过 'ollama pull <model_name>' 下载了该模型。")
            return None
        
# ✅ 新增：GLM Embedding 客户端
class GLMEmbeddingClient:
    """
    使用 LangChain 调用智谱 GLM Embedding 模型
    接口与 OllamaEmbeddingClient 保持一致
    """
    def __init__(self):
        """
        初始化 GLM Embedding 客户端
        从 config.settings 读取配置
        """
        try:
            # 从 settings 读取配置
            self.model = getattr(settings, 'embedding_model', 'embedding-3')
            
            self.client = OpenAIEmbeddings(
                model=self.model,
                openai_api_base=settings.base_url,
                openai_api_key=settings.openai_api_key,
                dimensions=settings.embedding_dimensions
            )
            
            # 测试连接（生成一个简单的嵌入）
            test_embedding = self.client.embed_query("test")
            if test_embedding:
                print(f"✅ GLM Embedding 客户端初始化成功，模型: {self.model}")
                print(f"   向量维度: {len(test_embedding)}")
            else:
                raise Exception("测试嵌入生成失败")
                
        except Exception as e:
            print(f"❌ GLM Embedding 客户端初始化失败: {e}")
            print("  请检查 API Key 和网络连接。")
            self.client = None

    def get_embedding(self, text: str) -> Optional[List[float]]:
        """
        为给定的文本生成嵌入向量。

        Args:
            text: 需要被向量化的字符串。

        Returns:
            一个浮点数列表表示的向量，如果失败则返回 None。
        """
        if not self.client:
            print("GLM Embedding 客户端未初始化，无法生成嵌入。")
            return None
        
        if not text or not text.strip():
            print("⚠️ 输入文本为空，跳过嵌入生成。")
            return None
        
        try:
            # 使用 LangChain 的 embed_query 方法
            embedding = self.client.embed_query(text)
            return embedding
        except Exception as e:
            print(f"❌ 调用 GLM Embedding 模型 '{self.model}' 失败: {e}")
            print(f"  输入文本长度: {len(text)} 字符")
            return None
    
    def get_embeddings(self, texts: List[str]) -> Optional[List[List[float]]]:
        """
        批量生成嵌入向量（可选方法，提升性能）

        Args:
            texts: 需要被向量化的字符串列表。

        Returns:
            嵌入向量列表，如果失败则返回 None。
        """
        if not self.client:
            print("GLM Embedding 客户端未初始化，无法生成嵌入。")
            return None
        
        try:
            # 使用 LangChain 的批量嵌入方法
            embeddings = self.client.embed_documents(texts)
            return embeddings
        except Exception as e:
            print(f"❌ 批量调用 GLM Embedding 模型失败: {e}")
            return None