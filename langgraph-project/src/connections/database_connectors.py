# step2_connections/database_connectors.py
import os
os.environ["PYTHONUTF8"] = "1"
os.environ["PGCLIENTENCODING"] = "UTF8"
import neo4j
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from connections import config

# 使用缓存来存储数据库驱动和连接，避免重复创建
_neo4j_driver = None
_pg_connection = None

def get_neo4j_driver():
    """
    获取并返回一个Neo4j驱动实例 (Singleton Pattern)。
    """
    global _neo4j_driver
    if _neo4j_driver is None:
        try:
            _neo4j_driver = neo4j.GraphDatabase.driver(
                config.NEO4J_URI,
                auth=(config.NEO4J_USER, config.NEO4J_PASSWORD)
            )
            _neo4j_driver.verify_connectivity()
            print("✅ Neo4j 连接成功。")
        except Exception as e:
            print(f"❌ Neo4j 连接失败: {e}")
            return None
    return _neo4j_driver

def get_pg_connection():
    """
    获取并返回一个PostgreSQL连接实例 (Singleton Pattern)。
    """
    global _pg_connection
    if _pg_connection is None or _pg_connection.closed:
        try:
            # --- 核心修改：使用 DSN 字符串进行连接 ---
            # 这种方法对于处理复杂的编码环境通常更稳定
            dsn = (
                f"dbname='{config.PG_DB_NAME}' "
                f"user='{config.PG_USER}' "
                f"password='{config.PG_PASSWORD}' "
                f"host='{config.PG_HOST}' "
                f"port='{config.PG_PORT}'"
            )
            _pg_connection = psycopg2.connect(dsn)
            # 连接成功后，再显式设置客户端编码，双重保险
            _pg_connection.set_client_encoding('UTF8')
            
            print("✅ PostgreSQL 连接成功。")
        except psycopg2.OperationalError as e:
             # 如果数据库不存在，尝试创建它
            if f'database "{config.PG_DB_NAME}" does not exist' in str(e):
                print(f"数据库 '{config.PG_DB_NAME}' 不存在，正在尝试创建...")
                try:
                    # 创建数据库时也使用 DSN
                    conn_postgres_dsn = (
                        f"dbname='postgres' "
                        f"user='{config.PG_USER}' "
                        f"password='{config.PG_PASSWORD}' "
                        f"host='{config.PG_HOST}' "
                        f"port='{config.PG_PORT}'"
                    )
                    conn_postgres = psycopg2.connect(conn_postgres_dsn)
                    conn_postgres.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
                    cursor = conn_postgres.cursor()
                    cursor.execute(f"CREATE DATABASE {config.PG_DB_NAME}")
                    cursor.close()
                    conn_postgres.close()
                    print(f"✅ 数据库 '{config.PG_DB_NAME}' 创建成功。请重新运行脚本以连接。")
                    return None
                except Exception as create_e:
                    print(f"❌ 数据库创建失败: {create_e}")
                    return None
            else:
                # 捕获并尝试解码可能的错误信息
                try:
                    error_message = str(e)
                except UnicodeDecodeError:
                    error_message = repr(e) # 如果str()转换失败，用repr()显示原始信息
                print(f"❌ PostgreSQL 连接失败: {error_message}")
                return None
    return _pg_connection
def setup_pgvector_table(conn):
    """
    在PostgreSQL中启用vector扩展并创建用于存储向量的表。
    """
    if not conn:
        return False
    try:
        with conn.cursor() as cursor:
            print("  - 正在启用 pgvector 扩展...")
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            
            print(f"  - 正在创建表 '{config.PG_VECTOR_TABLE_NAME}'...")
            # 创建一个表来存储元素的规范键和对应的向量
            create_table_query = f"""
            CREATE TABLE IF NOT EXISTS {config.PG_VECTOR_TABLE_NAME} (
                canonical_key VARCHAR(1024) PRIMARY KEY,
                element_name TEXT,
                element_type VARCHAR(255),
                element_description TEXT,
                embedding vector({config.VECTOR_DIMENSION})
            );
            """
            cursor.execute(create_table_query)
        conn.commit()
        print(f"✅ pgvector 表 '{config.PG_VECTOR_TABLE_NAME}' 设置完毕。")
        return True
    except Exception as e:
        print(f"❌ pgvector 表设置失败: {e}")
        conn.rollback()
        return False

def close_connections():
    """关闭所有活动的数据库连接。"""
    global _neo4j_driver, _pg_connection
    if _neo4j_driver:
        _neo4j_driver.close()
        _neo4j_driver = None
        print("Neo4j 连接已关闭。")
    if _pg_connection and not _pg_connection.closed:
        _pg_connection.close()
        _pg_connection = None
        print("PostgreSQL 连接已关闭。")
