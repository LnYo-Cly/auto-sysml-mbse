import json
from neo4j import GraphDatabase
import traceback

# ==== Neo4j 连接配置 ====
URI = "bolt://localhost:7687"
USER = "neo4j"
PASSWORD = "123456789"  # 请替换为您的实际密码

class Neo4jSequenceDiagramUploader:
    def __init__(self, uri, user, password):
        self._driver = None
        self.elements_by_id = {}
        self.model_info = {}

        try:
            self._driver = GraphDatabase.driver(uri, auth=(user, password),
                                               connection_timeout=30, max_connection_lifetime=3600)
            self._driver.verify_connectivity()
            print("成功连接到 Neo4j 并验证连接。")
        except Exception as e:
            print(f"错误：无法连接到 Neo4j 或验证失败 - {e}")
            self._driver = None
            raise

    def close(self):
        if self._driver:
            self._driver.close()
            print("Neo4j 连接已关闭。")

    def _execute_write_tx(self, tx, query, parameters=None):
        try:
            tx.run(query, parameters if parameters else {})
        except Exception as e:
            print(f"错误：执行 Cypher 查询失败\n查询: {query}\n参数: {parameters}\n错误: {e}")
            traceback.print_exc()
            raise

    def _preprocess_data(self, json_data_str):
        self.elements_by_id.clear()
        self.model_info.clear()
        try:
            json_data = json.loads(json_data_str)
        except json.JSONDecodeError as e:
            print(f"错误：JSON数据解析失败 - {e}")
            return False

        if "model" not in json_data or not isinstance(json_data["model"], list) or not json_data["model"]:
            print("错误：JSON数据必须包含有效的 'model' 键和信息。")
            return False
        
        self.model_info = json_data["model"][0]
        model_id_seq = self.model_info.get("id", "default-model-id")
        self.elements_by_id[model_id_seq] = self.model_info # 将模型信息也加入elements_by_id

        if "elements" not in json_data:
            print("错误：JSON数据必须包含 'elements' 键。")
            return False
        
        elements_list = json_data["elements"]
        try:
            for elem in elements_list:
                if "id" not in elem:
                    print(f"警告: 元素 {elem.get('name', 'Unnamed')} 缺少 'id'，已跳过。")
                    continue
                self.elements_by_id[elem["id"]] = elem
        except TypeError as e:
            print(f"错误：迭代JSON元素列表时出现问题：{e}")
            return False
        return True

    def _create_nodes_tx(self, tx):
        print("阶段 1 (TX)：开始创建/合并序列图节点...")
        nodes_to_create_batch = []

        for element_id, element_data in self.elements_by_id.items():
            elem_type = element_data.get("type")
            if not elem_type:
                print(f"警告: 元素 {element_id} 缺少 'type' 字段。跳过节点创建。")
                continue

            label = elem_type 
            props = {"elementId": element_id, "type": elem_type} 

            # 通用属性
            if "name" in element_data: props["name"] = element_data["name"]
            if "visibility" in element_data: props["visibility"] = element_data["visibility"] # 新增

            # 特定类型属性
            if elem_type == "Class" or elem_type == "Block":
                if "classifierBehaviorId" in element_data: props["classifierBehaviorId_ref"] = element_data["classifierBehaviorId"]
            elif elem_type == "Property":
                if "typeId" in element_data: props["typeId_ref"] = element_data["typeId"]
                if "typeHref" in element_data: props["typeHref"] = element_data["typeHref"]
                if "aggregation" in element_data: props["aggregation"] = element_data["aggregation"]
                if "associationId" in element_data: props["associationId_ref"] = element_data["associationId"]
            elif elem_type == "Parameter":
                if "direction" in element_data: props["direction"] = element_data["direction"]
                if "typeId" in element_data: props["typeId_ref"] = element_data["typeId"]
                if "typeHref" in element_data: props["typeHref"] = element_data["typeHref"]
            elif elem_type == "Lifeline":
                if "representsId" in element_data: props["representsId_ref"] = element_data["representsId"]
            elif elem_type == "Message":
                if "messageSort" in element_data: props["messageSort"] = element_data["messageSort"]
                if "signatureId" in element_data: props["signatureId_ref"] = element_data["signatureId"]
                # arguments 存储为字符串，或者更复杂地，参数作为独立节点
                if "arguments" in element_data: props["arguments_json"] = json.dumps(element_data["arguments"])
            elif elem_type == "MessageOccurrenceSpecification":
                if "coveredId" in element_data: props["coveredId_ref"] = element_data["coveredId"]
                if "messageId" in element_data: props["messageId_ref"] = element_data["messageId"]
            elif elem_type == "DestructionOccurrenceSpecification":
                 if "coveredId" in element_data: props["coveredId_ref"] = element_data["coveredId"]
            elif elem_type == "CombinedFragment":
                if "interactionOperator" in element_data: props["interactionOperator"] = element_data["interactionOperator"]
            elif elem_type == "InteractionConstraint":
                if "specification" in element_data and isinstance(element_data["specification"], dict):
                    props["spec_body"] = element_data["specification"].get("body")
                    props["spec_language"] = element_data["specification"].get("language")
                    if "id" in element_data["specification"]: props["spec_id"] = element_data["specification"]["id"]


            nodes_to_create_batch.append({"label": label, "props": props})
        
        # 分批执行MERGE
        grouped_batch = {}
        for item in nodes_to_create_batch:
            label = item["label"]
            if label not in grouped_batch: grouped_batch[label] = []
            grouped_batch[label].append(item["props"])

        total_nodes_processed_tx = 0
        for label, props_list in grouped_batch.items():
            safe_label = "".join(c for c in label if c.isalnum() or c == '_')
            if not safe_label or not safe_label[0].isalpha():
                print(f"警告: 无效的标签 '{label}' (处理后为 '{safe_label}')。跳过。")
                continue
            
            query = f"""
            UNWIND $props_list AS item_props
            MERGE (n:`{safe_label}` {{elementId: item_props.elementId}})
            ON CREATE SET n = item_props
            ON MATCH SET n += item_props
            """
            try:
                self._execute_write_tx(tx, query, parameters={"props_list": props_list})
                total_nodes_processed_tx += len(props_list)
                print(f"  - TX: 尝试处理了 {len(props_list)} 个 '{safe_label}' 节点。")
            except Exception: pass 

        print(f"阶段 1 (TX)：序列图节点创建/合并完成。总共尝试处理了 {total_nodes_processed_tx} 个节点定义。")

    def _create_relationships_tx(self, tx):
        print("阶段 2 (TX)：开始创建/合并序列图关系...")
        relations_batch = []
        model_id = self.model_info.get("id")

        for element_id, element_data in self.elements_by_id.items():
            if element_id == model_id: continue 

            elem_type = element_data.get("type")
            parent_id = element_data.get("parentId")
            
            # 1. 结构和拥有关系 (ParentId based)
            if parent_id:
                parent_node_data = self.elements_by_id.get(parent_id)
                if not parent_node_data:
                    print(f"警告: 元素 {element_id} 的父ID {parent_id} 未找到。跳过其父子关系。")
                    continue
                
                parent_type = parent_node_data.get("type")
                rel_type = "CONTAINS" # Default containment

                if parent_type == "Model" and elem_type == "Package": rel_type = "OWNS_PACKAGE"
                elif parent_type == "Package" and elem_type in ["Actor", "Class", "Block", "Association"]: rel_type = "OWNS_ELEMENT"
                elif parent_type in ["Class", "Block"]:
                    if elem_type == "Property": rel_type = "OWNS_ATTRIBUTE"
                    elif elem_type == "Operation": rel_type = "OWNS_OPERATION"
                    elif elem_type == "Interaction" and parent_node_data.get("classifierBehaviorId") == element_id:
                        rel_type = "HAS_CLASSIFIER_BEHAVIOR"
                    elif elem_type == "Interaction": # ownedBehavior not classifier
                        rel_type = "OWNS_BEHAVIOR"
                elif parent_type == "Operation" and elem_type == "Parameter": rel_type = "OWNS_PARAMETER"
                elif parent_type == "Interaction":
                    if elem_type == "Property": rel_type = "OWNS_ATTRIBUTE" # Interaction-owned property
                    elif elem_type == "Lifeline": rel_type = "OWNS_LIFELINE"
                    elif elem_type == "Message": rel_type = "OWNS_MESSAGE"
                    elif elem_type in ["MessageOccurrenceSpecification", "DestructionOccurrenceSpecification", "CombinedFragment"]:
                        rel_type = "OWNS_FRAGMENT"
                elif parent_type == "CombinedFragment" and elem_type == "InteractionOperand": rel_type = "OWNS_OPERAND"
                elif parent_type == "InteractionOperand":
                    if elem_type == "InteractionConstraint": rel_type = "HAS_GUARD_CONSTRAINT"
                    elif elem_type in ["MessageOccurrenceSpecification", "CombinedFragment"]: rel_type = "OWNS_FRAGMENT" # Fragment inside operand
                
                relations_batch.append({"from_id": parent_id, "to_id": element_id, "rel_type": rel_type, "props": {}})

            # 2. 特定引用关系
            if elem_type == "Property":
                if element_data.get("typeId"):
                    relations_batch.append({"from_id": element_id, "to_id": element_data["typeId"], "rel_type": "HAS_TYPE", "props": {}})
                if element_data.get("associationId"):
                    relations_batch.append({"from_id": element_id, "to_id": element_data["associationId"], "rel_type": "REFERENCES_ASSOCIATION_END", "props": {}})
            elif elem_type == "Association":
                for end_id in element_data.get("memberEndIds", []):
                    relations_batch.append({"from_id": element_id, "to_id": end_id, "rel_type": "HAS_MEMBER_END", "props": {}})
                for end_id in element_data.get("navigableOwnedEndIds", []):
                     relations_batch.append({"from_id": element_id, "to_id": end_id, "rel_type": "HAS_NAVIGABLE_OWNED_END", "props": {}})
            elif elem_type == "Lifeline" and element_data.get("representsId"):
                relations_batch.append({"from_id": element_id, "to_id": element_data["representsId"], "rel_type": "REPRESENTS_ELEMENT", "props": {}}) # Element could be Property or Class/Actor directly
            elif elem_type == "Message":
                if element_data.get("sendEventId"):
                    relations_batch.append({"from_id": element_id, "to_id": element_data["sendEventId"], "rel_type": "HAS_SEND_EVENT", "props": {}})
                if element_data.get("receiveEventId"):
                    relations_batch.append({"from_id": element_id, "to_id": element_data["receiveEventId"], "rel_type": "HAS_RECEIVE_EVENT", "props": {}})
                if element_data.get("signatureId"):
                    relations_batch.append({"from_id": element_id, "to_id": element_data["signatureId"], "rel_type": "INVOKES_OPERATION", "props": {}})
            elif elem_type == "MessageOccurrenceSpecification":
                if element_data.get("coveredId"):
                    relations_batch.append({"from_id": element_id, "to_id": element_data["coveredId"], "rel_type": "COVERS_LIFELINE", "props": {}})
                if element_data.get("messageId"):
                     relations_batch.append({"from_id": element_id, "to_id": element_data["messageId"], "rel_type": "BELONGS_TO_MESSAGE", "props": {}})
            elif elem_type == "DestructionOccurrenceSpecification" and element_data.get("coveredId"):
                 relations_batch.append({"from_id": element_id, "to_id": element_data["coveredId"], "rel_type": "COVERS_LIFELINE", "props": {}})
            elif elem_type == "CombinedFragment":
                for ll_id in element_data.get("coveredLifelineIds", []):
                    relations_batch.append({"from_id": element_id, "to_id": ll_id, "rel_type": "COVERS_LIFELINE", "props": {}})
            elif elem_type == "Parameter" and element_data.get("typeId"):
                 relations_batch.append({"from_id": element_id, "to_id": element_data["typeId"], "rel_type": "HAS_TYPE", "props": {}})


        # 执行关系创建
        total_rels_processed_tx = 0
        grouped_rels = {}
        for rel_item in relations_batch:
            rel_type = rel_item["rel_type"]
            if rel_type not in grouped_rels: grouped_rels[rel_type] = []
            grouped_rels[rel_type].append(rel_item)

        for rel_type_str, rel_list_for_type in grouped_rels.items():
            safe_rel_type = "".join(c for c in rel_type_str if c.isalnum() or c == '_').upper()
            if not safe_rel_type or not safe_rel_type[0].isalpha(): continue
            
            query = f"""
            UNWIND $rel_list AS rel_data
            MATCH (from_node {{elementId: rel_data.from_id}})
            MATCH (to_node {{elementId: rel_data.to_id}})
            MERGE (from_node)-[r:`{safe_rel_type}`]->(to_node)
            ON CREATE SET r = rel_data.props
            ON MATCH SET r += rel_data.props 
            """
            try:
                self._execute_write_tx(tx, query, parameters={"rel_list": rel_list_for_type})
                total_rels_processed_tx += len(rel_list_for_type)
                print(f"  - TX: 尝试处理了 {len(rel_list_for_type)} 个 :{safe_rel_type} 关系。")
            except Exception: pass

        print(f"阶段 2 (TX)：序列图关系创建/合并完成。总共尝试处理了 {total_rels_processed_tx} 个关系定义。")

    def upload_from_json_string(self, json_data_str):
        if not self._driver: print("错误：Neo4j 驱动未初始化。"); return False
        if not self._preprocess_data(json_data_str): print("错误：数据预处理失败。"); return False
        if not self.elements_by_id : print("错误：预处理后无元素。"); return False
        
        try:
            with self._driver.session(database="neo4j") as session:
                session.execute_write(self._create_nodes_tx)
                session.execute_write(self._create_relationships_tx)
            print("序列图数据上传过程成功完成。")
            return True
        except Exception as e:
            print(f"错误：在 Neo4j 会话或事务期间发生顶层错误：{e}")
            traceback.print_exc()
            return False

    def clear_database_content(self):
        # (与之前的 clear_database_content 实现相同)
        if not self._driver: print("错误：Neo4j 驱动未初始化。"); return
        confirm = input("警告：此操作将删除数据库中的所有数据！是否继续？ (yes/no): ")
        if confirm.lower() != 'yes': print("操作已取消。"); return
        try:
            with self._driver.session(database="neo4j") as session:
                session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
            print("数据库内容已清空。")
        except Exception as e: print(f"清空数据库内容时出错: {e}"); traceback.print_exc()

# --- 主程序 ---
if __name__ == '__main__':
    # 使用您提供的ATM序列图JSON
    actual_atm_interaction_json_str = """
{
  "model": [
    {
      "id": "model-atm-sys-uuid",
      "type": "Model",
      "name": "ATM系统模型"
    }
  ],
  "elements": [
    {
      "id": "pkg-banksvc-uuid",
      "type": "Package",
      "name": "银行服务",
      "parentId": "model-atm-sys-uuid"
    },
    {
      "id": "actor-customer-uuid",
      "type": "Actor",
      "name": "客户",
      "parentId": "pkg-banksvc-uuid"
    },
    {
      "id": "cls-atm-uuid",
      "type": "Class",
      "name": "ATM",
      "parentId": "pkg-banksvc-uuid",
      "classifierBehaviorId": "interaction-withdraw-uuid",
      "ownedOperationIds": ["op-execwithdraw-uuid"],
      "ownedAttributeIds": ["prop-atm-instance-uuid", "prop-db-connector-uuid"]
    },
    {
      "id": "cls-db-uuid",
      "type": "Class",
      "name": "后端数据库",
      "parentId": "pkg-banksvc-uuid",
      "ownedOperationIds": ["op-querybal-uuid"],
      "ownedAttributeIds": []
    },
    {
      "id": "prop-interaction-customer-uuid",
      "type": "Property",
      "name": "p_customer",
      "parentId": "interaction-withdraw-uuid",
      "typeId": "actor-customer-uuid",
      "aggregation": "none",
      "visibility": "public"
    },
    {
      "id": "prop-atm-instance-uuid",
      "type": "Property",
      "name": "atm_instance",
      "parentId": "cls-atm-uuid",
      "typeId": "cls-atm-uuid",
      "aggregation": "none",
      "visibility": "public"
    },
    {
      "id": "prop-db-connector-uuid",
      "type": "Property",
      "name": "db_connector",
      "parentId": "cls-atm-uuid",
      "typeId": "cls-db-uuid",
      "aggregation": "none",
      "visibility": "public"
    },
    {
      "id": "op-execwithdraw-uuid",
      "type": "Operation",
      "name": "执行取款",
      "parentId": "cls-atm-uuid",
      "parameterIds": [],
      "visibility": "public"
    },
    {
      "id": "op-querybal-uuid",
      "type": "Operation",
      "name": "查询余额",
      "parentId": "cls-db-uuid",
      "parameterIds": ["param-accountid-uuid"],
      "visibility": "public"
    },
    {
      "id": "param-accountid-uuid",
      "type": "Parameter",
      "name": "账户ID",
      "parentId": "op-querybal-uuid",
      "direction": "in",
      "typeId": null, 
      "visibility": "public"
    },
    {
      "id": "interaction-withdraw-uuid",
      "type": "Interaction",
      "name": "客户取钱",
      "parentId": "cls-atm-uuid",
      "lifelineIds": [
        "ll-customer-uuid",
        "ll-atm-uuid",
        "ll-db-uuid"
      ],
      "messageIds": [
        "msg-reqwithdraw-uuid",
        "msg-verifybal-uuid",
        "msg-balinfo-uuid",
        "msg-dispense-uuid",
        "msg-insufficient-uuid"
      ],
      "fragmentIds": [
        "fragment-send-reqwithdraw-uuid",
        "fragment-recv-reqwithdraw-uuid",
        "fragment-send-verifybal-uuid",
        "fragment-recv-verifybal-uuid",
        "fragment-send-balinfo-uuid",
        "fragment-recv-balinfo-uuid",
        "cf-balancecheck-alt-uuid",
        "fragment-destroy-db-uuid"
      ],
      "ownedAttributeIds": [
        "prop-interaction-customer-uuid"
      ]
    },
    {
      "id": "ll-customer-uuid",
      "type": "Lifeline",
      "name": "L1",
      "parentId": "interaction-withdraw-uuid",
      "representsId": "prop-interaction-customer-uuid",
      "visibility": "public"
    },
    {
      "id": "ll-atm-uuid",
      "type": "Lifeline",
      "name": "L2",
      "parentId": "interaction-withdraw-uuid",
      "representsId": "prop-atm-instance-uuid",
      "visibility": "public"
    },
    {
      "id": "ll-db-uuid",
      "type": "Lifeline",
      "name": "L3",
      "parentId": "interaction-withdraw-uuid",
      "representsId": "prop-db-connector-uuid",
      "visibility": "public"
    },
    {
      "id": "msg-reqwithdraw-uuid",
      "type": "Message",
      "name": "取款请求",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-reqwithdraw-uuid",
      "receiveEventId": "fragment-recv-reqwithdraw-uuid",
      "messageSort": "synchCall",
      "signatureId": "op-execwithdraw-uuid",
      "arguments": [],
      "visibility": "public"
    },
    {
      "id": "msg-verifybal-uuid",
      "type": "Message",
      "name": "验证余额",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-verifybal-uuid",
      "receiveEventId": "fragment-recv-verifybal-uuid",
      "messageSort": "synchCall",
      "signatureId": "op-querybal-uuid",
      "arguments": [
        {
          "id": "arg-acctid-for-verifybal-uuid",
          "body": "账户ID",
          "language": "natural"
        }
      ],
      "visibility": "public"
    },
    {
      "id": "msg-balinfo-uuid",
      "type": "Message",
      "name": "余额信息",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-balinfo-uuid",
      "receiveEventId": "fragment-recv-balinfo-uuid",
      "messageSort": "reply",
      "visibility": "public"
    },
    {
      "id": "msg-dispense-uuid",
      "type": "Message",
      "name": "出钞",
      "parentId": "interaction-withdraw-uuid", 
      "sendEventId": "fragment-send-dispense-uuid",
      "receiveEventId": "fragment-recv-dispense-uuid",
      "messageSort": "reply",
      "visibility": "public"
    },
    {
      "id": "msg-insufficient-uuid",
      "type": "Message",
      "name": "余额不足",
      "parentId": "interaction-withdraw-uuid", 
      "sendEventId": "fragment-send-insufficient-uuid",
      "receiveEventId": "fragment-recv-insufficient-uuid",
      "messageSort": "reply",
      "visibility": "public"
    },
    {
      "id": "fragment-send-reqwithdraw-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-customer-uuid",
      "messageId": "msg-reqwithdraw-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-reqwithdraw-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-reqwithdraw-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-send-verifybal-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-verifybal-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-verifybal-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-db-uuid",
      "messageId": "msg-verifybal-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-send-balinfo-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-db-uuid",
      "messageId": "msg-balinfo-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-balinfo-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-balinfo-uuid",
      "visibility": "public"
    },
    {
      "id": "cf-balancecheck-alt-uuid",
      "type": "CombinedFragment",
      "name": "AltFragment",
      "parentId": "interaction-withdraw-uuid",
      "interactionOperator": "alt",
      "coveredLifelineIds": [
        "ll-atm-uuid",
        "ll-customer-uuid"
      ],
      "operandIds": [
        "operand-sufficient-uuid",
        "operand-insufficient-uuid"
      ],
      "visibility": "public"
    },
    {
      "id": "operand-sufficient-uuid",
      "type": "InteractionOperand",
      "parentId": "cf-balancecheck-alt-uuid",
      "guardId": "guard-sufficient-uuid",
      "fragmentIds": [
        "fragment-send-dispense-uuid",
        "fragment-recv-dispense-uuid",
        "msg-dispense-uuid" 
      ],
      "visibility": "public"
    },
    {
      "id": "operand-insufficient-uuid",
      "type": "InteractionOperand",
      "parentId": "cf-balancecheck-alt-uuid",
      "guardId": null,
      "fragmentIds": [
        "fragment-send-insufficient-uuid",
        "fragment-recv-insufficient-uuid",
        "msg-insufficient-uuid" 
      ],
      "visibility": "public"
    },
    {
      "id": "guard-sufficient-uuid",
      "type": "InteractionConstraint",
      "parentId": "operand-sufficient-uuid",
      "specification": {
        "id": "spec-guard-sufficient-uuid",
        "body": "余额充足",
        "language": "natural"
      },
      "visibility": "public"
    },
    {
      "id": "fragment-send-dispense-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-sufficient-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-dispense-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-dispense-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-sufficient-uuid",
      "coveredId": "ll-customer-uuid",
      "messageId": "msg-dispense-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-send-insufficient-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-insufficient-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-insufficient-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-insufficient-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-insufficient-uuid",
      "coveredId": "ll-customer-uuid",
      "messageId": "msg-insufficient-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-destroy-db-uuid",
      "type": "DestructionOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-db-uuid",
      "visibility": "public"
    }
  ]
}
"""
    uploader = None
    try:
        uploader = Neo4jSequenceDiagramUploader(URI, USER, PASSWORD)
        uploader.clear_database_content() # 取消注释以清空数据库

        print(f"\n--- 开始上传ATM序列图数据到 Neo4j ({URI}) ---")
        success = uploader.upload_from_json_string(actual_atm_interaction_json_str)
        if success:
            print("--- ATM序列图数据上传成功 ---")
        else:
            print("--- ATM序列图数据上传失败 ---")

    except Exception as main_e:
        print(f"主程序发生错误: {main_e}")
        traceback.print_exc()
    finally:
        if uploader:
            uploader.close()