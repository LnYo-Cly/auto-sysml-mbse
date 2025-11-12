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

def store_data_to_neo4j(data, db):
    try:
        logger.info("开始清空数据库...")
        db.clear_database()
        
        # 创建Block节点
        logger.info("开始创建Block节点...")
        for block in data['blocks']:
            query = """
            MERGE (b:Block {id: $id})
            SET b.name = $name
            """
            params = {'id': block['id'], 'name': block['name']}
            db.execute_query(query, params)
            logger.info(f"创建Block节点: {block['name']}")
        
        # 创建ConstraintBlock节点
        logger.info("开始创建ConstraintBlock节点...")
        for cb in data['constraintBlocks']:
            query = """
            MERGE (cb:ConstraintBlock {id: $id})
            SET cb.name = $name, cb.constraint = $constraint
            """
            params = {
                'id': cb['id'],
                'name': cb['name'],
                'constraint': cb['constraint']
            }
            db.execute_query(query, params)
            logger.info(f"创建ConstraintBlock节点: {cb['name']}")
        
        # 创建ValueProperty并关联到Block
        logger.info("创建ValueProperty节点并关联到Block...")
        for block in data['blocks']:
            block_id = block['id']
            for prop in block['properties']:
                query = """
                MERGE (vp:ValueProperty {id: $vp_id})
                SET vp.name = $name, vp.type = $type
                WITH vp
                MATCH (b:Block {id: $block_id})
                MERGE (b)-[:Owned]->(vp)
                """
                params = {
                    'vp_id': prop['id'],
                    'name': prop['name'],
                    'type': prop['type'],
                    'block_id': block_id
                }
                db.execute_query(query, params)
                logger.info(f"创建ValueProperty节点 {prop['id']} 并关联到Block {block_id}")
        
        # 创建ConstraintProperty并关联到Block和ConstraintBlock
        logger.info("创建ConstraintProperty节点并关联...")
        for block in data['blocks']:
            block_id = block['id']
            for cp in block['constraintProperties']:
                query = """
                MERGE (cp:ConstraintProperty {id: $cp_id})
                SET cp.name = $name, cp.from = $from_id
                WITH cp
                MATCH (b:Block {id: $block_id})
                MERGE (b)-[:Owned]->(cp)
                WITH cp
                MATCH (cb:ConstraintBlock {id: $from_id})
                MERGE (cp)-[:REFERENCES]->(cb)
                """
                params = {
                    'cp_id': cp['id'],
                    'name': cp['name'],
                    'from_id': cp['from'],
                    'block_id': block_id
                }
                db.execute_query(query, params)
                logger.info(f"创建ConstraintProperty节点 {cp['id']} 并关联到Block {block_id}和ConstraintBlock {cp['from']}")
        
        # 建立ConstraintBlock到ValueProperty的Port关系
        logger.info("建立Port关系...")
        for cb in data['constraintBlocks']:
            cb_id = cb['id']
            for port in cb['ports']:
                vp_id = port['from']
                query = """
                MATCH (vp:ValueProperty {id: $vp_id})
                MATCH (cb:ConstraintBlock {id: $cb_id})
                MERGE (cb)-[:Port]->(vp)
                """
                params = {'vp_id': vp_id, 'cb_id': cb_id}
                db.execute_query(query, params)
                logger.info(f"创建Port关系: ConstraintBlock {cb_id} -> ValueProperty {vp_id}")
        
        logger.info("所有数据已成功存储到Neo4j数据库")
    except Exception as e:
        logger.error(f"存储数据时发生错误: {str(e)}")
        raise

# 示例数据
data = {
    "blocks": [
        {
            "id": "block1",
            "name": "BatteryModule",
            "properties": [
                {"id": "prop1", "name": "V_batt", "type": "Real"},
                {"id": "prop2", "name": "EMF", "type": "Real"},
                {"id": "prop3", "name": "R_int", "type": "Real"},
                {"id": "prop4", "name": "I", "type": "Real"},
                {"id": "prop5", "name": "Losses", "type": "Real"}
            ],
            "constraintProperties": [
                {"id": "constraint1", "from": "cb1", "name": "BatteryEquation"}
            ]
        },
        {
            "id": "block2",
            "name": "MotorModule",
            "properties": [
                {"id": "prop6", "name": "T", "type": "Real"},
                {"id": "prop7", "name": "ω", "type": "Real"},
                {"id": "prop8", "name": "K_t", "type": "Real"},
                {"id": "prop9", "name": "B", "type": "Real"}
            ],
            "constraintProperties": [
                {"id": "constraint2", "from": "cb2", "name": "MotorDynamics"}
            ]
        }
    ],
    "constraintBlocks": [
        {
            "id": "cb1",
            "name": "BatteryModel",
            "ports": [
                {"id": "p1", "name": "V_batt", "type": "Real", "from": "prop1"},
                {"id": "p2", "name": "EMF", "type": "Real", "from": "prop2"},
                {"id": "p3", "name": "R_int", "type": "Real", "from": "prop3"},
                {"id": "p4", "name": "I", "type": "Real", "from": "prop4"}
            ],
            "constraint": "V_batt = EMF - R_int*I"
        },
        {
            "id": "cb2",
            "name": "MotorModel",
            "ports": [
                {"id": "p5", "name": "T", "type": "Real", "from": "prop6"},
                {"id": "p6", "name": "ω", "type": "Real", "from": "prop7"},
                {"id": "p7", "name": "K_t", "type": "Real", "from": "prop8"},
                {"id": "p8", "name": "B", "type": "Real", "from": "prop9"},
                {"id": "p9", "name": "I", "type": "Real", "from": "prop4"}
            ],
            "constraint": "T = K_t*I - B*ω"
        },
        {
            "id": "cb3",
            "name": "PowerBalance",
            "ports": [
                {"id": "p10", "name": "V_batt", "type": "Real", "from": "prop1"},
                {"id": "p11", "name": "I", "type": "Real", "from": "prop4"},
                {"id": "p12", "name": "T", "type": "Real", "from": "prop6"},
                {"id": "p13", "name": "ω", "type": "Real", "from": "prop7"},
                {"id": "p14", "name": "Losses", "type": "Real", "from": "prop5"}
            ],
            "constraint": "V_batt*I = T*ω + Losses"
        }
    ]
}

# Neo4j 连接配置
uri = "bolt://localhost:7687"
user = "neo4j"
password = "123456789"

try:
    db = Neo4jDatabase(uri, user, password)
    store_data_to_neo4j(data, db)
except Exception as e:
    logger.error(f"程序执行出错: {str(e)}")
finally:
    db.close()
    logger.info("数据库连接已关闭")