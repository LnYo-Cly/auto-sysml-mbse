from neo4j import GraphDatabase
import uuid
import json
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

class Neo4jDatabase:
    def __init__(self, uri, user, password):
        self.uri = uri
        self.user = user
        self.password = password
        try:
            self.driver = GraphDatabase.driver(self.uri, auth=(self.user, self.password))
            logger.info("成功连接到Neo4j数据库")
        except Exception as e:
            logger.error(f"连接数据库失败: {str(e)}")
            raise

    def close(self):
        self.driver.close()

    def execute_query(self, query, parameters=None):
        """执行查询并返回结果列表"""
        try:
            with self.driver.session() as session:
                # 直接获取所有结果并转换为列表
                result = session.run(query, parameters)
                records = [dict(record) for record in result]
                return records
        except Exception as e:
            logger.error(f"执行查询失败: {str(e)}")
            raise

    def clear_database(self):
        """清空数据库中的所有节点和关系"""
        try:
            query = "MATCH (n) DETACH DELETE n"
            self.execute_query(query)
            logger.info("数据库已清空")
        except Exception as e:
            logger.error(f"清空数据库失败: {str(e)}")
            raise

# 生成 UUID
def generate_uuid():
    return str(uuid.uuid4())

# 存储数据的函数
def store_data_to_neo4j(data, db):
    try:
        # 首先清空数据库
        logger.info("开始清空数据库...")
        db.clear_database()
        
        # 创建actor节点
        logger.info("开始创建actor节点...")
        for actor in data['actors']:
            query = """
            MERGE (a:Actor {id: $id})
            SET a.name = $name
            """
            db.execute_query(query, parameters={'id': actor['id'], 'name': actor['name']})
            logger.info(f"创建actor节点: {actor['name']}")

        # 创建用例节点
        logger.info("开始创建用例节点...")
        for use_case in data['useCases']:
            query = """
            MERGE (u:UseCase {id: $id})
            SET u.name = $name
            """
            db.execute_query(query, parameters={'id': use_case['id'], 'name': use_case['name']})
            logger.info(f"创建用例节点: {use_case['name']}")

        # 创建关系
        logger.info("开始创建关系...")
        for relationship in data['relationships']:
            if relationship['type'] == 'association':
                query = """
                MATCH (a:Actor {id: $source})
                MATCH (u:UseCase {id: $target})
                MERGE (a)-[r:关联]->(u)
                """
            elif relationship['type'] == 'include':
                query = """
                MATCH (u1:UseCase {id: $source})
                MATCH (u2:UseCase {id: $target})
                MERGE (u1)-[r:包含]->(u2)
                """
            else:  # extend
                query = """
                MATCH (u1:UseCase {id: $source})
                MATCH (u2:UseCase {id: $target})
                MERGE (u1)-[r:扩展]->(u2)
                """
            db.execute_query(query, parameters={
                'source': relationship['source'],
                'target': relationship['target']
            })
            logger.info(f"创建关系: {relationship['source']} -> {relationship['target']} ({relationship['type']})")

        logger.info("所有数据已成功存储到Neo4j数据库")
        


    except Exception as e:
        logger.error(f"存储数据时发生错误: {str(e)}")
        raise

# 示例数据
data = {
  "actors": [
    {"id": "actor1", "name": "农场管理员"},
    {"id": "actor2", "name": "技术支持团队"},
    {"id": "actor3", "name": "自动播种机"},
    {"id": "actor4", "name": "采摘机器人"},
    {"id": "actor5", "name": "无人机"}
  ],
  "useCases": [
    {"id": "useCase1", "name": "调节温室温度"},
    {"id": "useCase2", "name": "调节湿度"},
    {"id": "useCase3", "name": "调节光照"},
    {"id": "useCase4", "name": "监控土壤湿度"},
    {"id": "useCase5", "name": "自动灌溉"},
    {"id": "useCase6", "name": "自动施肥"},
    {"id": "useCase7", "name": "自动施药"},
    {"id": "useCase8", "name": "自动操作"},
    {"id": "useCase9", "name": "监测作物健康"},
    {"id": "useCase10", "name": "发出预警"},
    {"id": "useCase11", "name": "检查设备"},
    {"id": "useCase12", "name": "远程修复"},
    {"id": "useCase13", "name": "派人修理"},
    {"id": "useCase14", "name": "推送软件更新"}
  ],
  "relationships": [
    {"source": "actor1", "target": "useCase1", "type": "association"},
    {"source": "actor1", "target": "useCase2", "type": "association"},
    {"source": "actor1", "target": "useCase3", "type": "association"},
    {"source": "actor1", "target": "useCase4", "type": "association"},
    {"source": "actor1", "target": "useCase5", "type": "association"},
    {"source": "actor1", "target": "useCase6", "type": "association"},
    {"source": "actor1", "target": "useCase7", "type": "association"},
    {"source": "actor1", "target": "useCase9", "type": "association"},
    {"source": "actor1", "target": "useCase10", "type": "association"},
    {"source": "actor2", "target": "useCase11", "type": "association"},
    {"source": "actor2", "target": "useCase12", "type": "association"},
    {"source": "actor2", "target": "useCase13", "type": "association"},
    {"source": "actor2", "target": "useCase14", "type": "association"},
    {"source": "actor3", "target": "useCase8", "type": "association"},
    {"source": "actor4", "target": "useCase8", "type": "association"},
    {"source": "actor5", "target": "useCase9", "type": "association"},
    {"source": "useCase1", "target": "useCase4", "type": "include"},
    {"source": "useCase2", "target": "useCase4", "type": "include"},
    {"source": "useCase3", "target": "useCase4", "type": "include"},
    {"source": "useCase4", "target": "useCase5", "type": "extend"},
    {"source": "useCase4", "target": "useCase6", "type": "extend"},
    {"source": "useCase4", "target": "useCase7", "type": "extend"},
    {"source": "useCase8", "target": "useCase3", "type": "extend"},
    {"source": "useCase8", "target": "useCase4", "type": "extend"},
    {"source": "useCase9", "target": "useCase10", "type": "extend"},
    {"source": "useCase11", "target": "useCase12", "type": "extend"},
    {"source": "useCase11", "target": "useCase13", "type": "extend"}
  ]
}

# Neo4j 连接配置
uri = "bolt://localhost:7687"
user = "neo4j"
password = "123456789"

try:
    # 创建数据库连接实例
    db = Neo4jDatabase(uri, user, password)
    
    # 将数据存储到 Neo4j
    store_data_to_neo4j(data, db)
    
except Exception as e:
    logger.error(f"程序执行出错: {str(e)}")
finally:
    # 关闭数据库连接
    db.close()
    logger.info("数据库连接已关闭")
