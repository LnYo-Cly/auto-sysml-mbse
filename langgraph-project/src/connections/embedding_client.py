# step2_connections/embedding_client.py

import ollama
from typing import List, Optional
from connections import config

class OllamaEmbeddingClient:
    """
    一个用于与本地Ollama服务交互的客户端，
    专门用于生成文本嵌入（向量）。
    """
    def __init__(self):
        self.model = config.EMBEDDING_MODEL
        try:
            self.client = ollama.Client(host=config.OLLAMA_HOST)
            # 尝试与服务器通信以验证连接
            self.client.list()
            print(f"✅ Ollama 客户端初始化成功，已连接到 {config.OLLAMA_HOST}。")
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