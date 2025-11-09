# step2_connections/config.py

# --- Neo4j Database Configuration ---
NEO4J_URI = "bolt://localhost:7687"
NEO4J_USER = "neo4j"
NEO4J_PASSWORD = "123456789"  # <-- 请将这里替换为您的密码 "123456"

# --- PostgreSQL (pgvector) Configuration ---
PG_DB_NAME = "test"        # <-- 请替换为您的数据库名, e.g., "mbse_db"
PG_USER = "postgres"
PG_PASSWORD = "123456"  # <-- 请将这里替换为您的密码 "123456"
PG_HOST = "localhost"
PG_PORT = "5432"

# --- Vector Database Table Configuration ---
PG_VECTOR_TABLE_NAME = "model_element_embeddings"
# Qwen3-Embedding-0.6B 模型的向量维度是 1024
VECTOR_DIMENSION = 1024

# --- Ollama AI Models Configuration ---
OLLAMA_HOST = "http://localhost:11434"
# 用于生成文本向量的模型
EMBEDDING_MODEL = "dengcao/Qwen3-Embedding-0.6B:Q8_0"
# 用于重排序的模型（此步骤暂不使用，但预先配置好）
RERANKER_MODEL = "dengcao/Qwen3-Reranker-0.6B:Q8_0"