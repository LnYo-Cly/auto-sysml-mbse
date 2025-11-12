import json
from neo4j import GraphDatabase
import traceback

# ==== Neo4j 连接配置 ====
URI = "bolt://localhost:7687" # 请根据您的Neo4j实例调整
USER = "neo4j"
PASSWORD = "123456789"  # 请替换为您的实际密码

class Neo4jRequirementUploader:
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
            return True
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
        if "elements" not in json_data:
            print("错误：JSON数据必须包含 'elements' 键。")
            return False
        elements_list = json_data["elements"]
        try:
            self.elements_by_id = {elem["id"]: elem for elem in elements_list}
        except KeyError as e:
            print(f"错误：JSON元素缺少 'id' 字段：{e}")
            return False
        except TypeError as e:
            print(f"错误：迭代JSON元素列表时出现问题：{e}")
            return False
        if "model" in json_data and isinstance(json_data["model"], list) and json_data["model"]:
            self.model_info = json_data["model"][0]
        else:
            print("警告：JSON数据中未找到顶层 'model' 信息。")
            self.model_info = {"id": "default-model-id", "name": "DefaultRequirementsModel"}
        return True

    # --- 添加缺失的包装方法 ---
    def _create_nodes_tx_wrapper(self, tx):
        """包装器方法，用于在 session.execute_write 中调用节点创建逻辑。"""
        self._create_nodes_in_batches_tx(tx)

    def _create_relationships_tx_wrapper(self, tx):
        """包装器方法，用于在 session.execute_write 中调用关系创建逻辑。"""
        self._create_relationships_in_batches_tx(tx)
    # --- 包装方法添加结束 ---

    def _create_nodes_in_batches_tx(self, tx):
        print("阶段 1 (TX)：开始创建/合并节点...")
        elements_to_create_nodes_for = []
        if self.model_info and "id" in self.model_info:
            model_props = {"elementId": self.model_info["id"], "type": "Model"}
            if "name" in self.model_info: model_props["name"] = self.model_info["name"]
            elements_to_create_nodes_for.append({"label": "Model", "props": model_props})

        node_types_map = {"Package": "Package", "Requirement": "Requirement", "Block": "Block", "TestCase": "TestCase"}
        for element_id, element_data in self.elements_by_id.items():
            elem_type = element_data.get("type")
            if elem_type in node_types_map:
                label = node_types_map[elem_type]
                props = {"elementId": element_id, "type": elem_type}
                if "name" in element_data: props["name"] = element_data["name"]
                if elem_type == "Requirement":
                    if "reqId" in element_data: props["userDefinedId"] = element_data["reqId"]
                    if "text" in element_data: props["text"] = element_data["text"]
                elements_to_create_nodes_for.append({"label": label, "props": props})

        grouped_batch = {}
        for item in elements_to_create_nodes_for:
            if item["label"] not in grouped_batch: grouped_batch[item["label"]] = []
            grouped_batch[item["label"]].append(item["props"])

        total_nodes_processed_tx = 0
        for label, props_list in grouped_batch.items():
            safe_label = "".join(c for c in label if c.isalnum() or c == '_')
            if not safe_label or not safe_label[0].isalpha(): continue
            query = f"UNWIND $props_list AS item_props MERGE (n:`{safe_label}` {{elementId: item_props.elementId}}) ON CREATE SET n = item_props ON MATCH SET n += item_props RETURN count(n) AS created_count"
            try:
                self._execute_write_tx(tx, query, parameters={"props_list": props_list})
                total_nodes_processed_tx += len(props_list)
                print(f"  - TX: 尝试处理了 {len(props_list)} 个 '{label}' 节点。")
            except Exception as e:
                print(f"错误 (TX)：在为标签 '{safe_label}' 创建/合并节点的操作中发生错误。")
                # 不在此处 raise，让 _execute_write_tx 处理异常的抛出

        print(f"阶段 1 (TX)：节点创建/合并完成。总共尝试处理了 {total_nodes_processed_tx} 个节点定义。")


    def _create_relationships_in_batches_tx(self, tx):
        print("阶段 2 (TX)：开始创建/合并关系...")
        # ... (与上一版本相同的关系创建逻辑) ...
        contains_relations_data = []
        model_root_id = self.model_info.get("id", "default-model-id")
        for element_id, element_data in self.elements_by_id.items():
            parent_id = element_data.get("parentId")
            actual_parent_id = parent_id if parent_id else model_root_id
            if actual_parent_id != element_id:
                parent_type_data = self.elements_by_id.get(actual_parent_id)
                parent_type = parent_type_data.get("type") if parent_type_data else ("Model" if actual_parent_id == model_root_id else None)
                elem_type = element_data.get("type")
                if parent_type in ["Model", "Package"] and elem_type not in ["DeriveReqt", "Satisfy", "Verify"]:
                    contains_relations_data.append({
                        "from_id": actual_parent_id,
                        "to_id": element_id
                    })
        specific_relations_data = []
        for element_id, element_data in self.elements_by_id.items():
            elem_type = element_data.get("type")
            rel_props = {"relationId": element_id, "type": elem_type}
            if "name" in element_data: rel_props["name"] = element_data["name"]
            rel_type_str, from_node_id, to_node_id = None, None, None
            if elem_type == "DeriveReqt":
                rel_type_str, from_node_id, to_node_id = "DERIVES_FROM", element_data.get("derivedRequirementId"), element_data.get("sourceRequirementId")
            elif elem_type == "Satisfy":
                rel_type_str, from_node_id, to_node_id = "SATISFIES", element_data.get("blockId"), element_data.get("requirementId")
            elif elem_type == "Verify":
                rel_type_str, from_node_id, to_node_id = "VERIFIES", element_data.get("testCaseId"), element_data.get("requirementId")
            if rel_type_str and from_node_id and to_node_id:
                specific_relations_data.append({"type": rel_type_str, "from_id": from_node_id, "to_id": to_node_id, "props": rel_props})

        if contains_relations_data:
            query_contains = "UNWIND $rel_list AS rel_data MATCH (from_node {elementId: rel_data.from_id}) MATCH (to_node {elementId: rel_data.to_id}) MERGE (from_node)-[r:CONTAINS]->(to_node) RETURN count(r)"
            try:
                self._execute_write_tx(tx, query_contains, parameters={"rel_list": contains_relations_data})
                print(f"  - TX: 尝试处理了 {len(contains_relations_data)} 个 :CONTAINS 关系。")
            except Exception as e:
                print(f"错误 (TX)：为类型 :CONTAINS 创建/合并关系时出错。")

        grouped_specific_rels = {}
        for rel_item in specific_relations_data:
            if rel_item["type"] not in grouped_specific_rels: grouped_specific_rels[rel_item["type"]] = []
            grouped_specific_rels[rel_item["type"]].append(rel_item)
        total_specific_rels_processed = 0
        for rel_type_str, rel_list in grouped_specific_rels.items():
            safe_rel_type = "".join(c for c in rel_type_str if c.isalnum() or c == '_').upper()
            if not safe_rel_type or not safe_rel_type[0].isalpha(): continue
            query_specific = f"UNWIND $rel_list AS rel_data MATCH (from_node {{elementId: rel_data.from_id}}) MATCH (to_node {{elementId: rel_data.to_id}}) MERGE (from_node)-[r:`{safe_rel_type}` {{relationId: rel_data.props.relationId}}]->(to_node) ON CREATE SET r = rel_data.props ON MATCH SET r += rel_data.props RETURN count(r)"
            try:
                self._execute_write_tx(tx, query_specific, parameters={"rel_list": rel_list})
                total_specific_rels_processed += len(rel_list)
                print(f"  - TX: 尝试处理了 {len(rel_list)} 个 :{safe_rel_type} 关系。")
            except Exception as e:
                print(f"错误 (TX)：为类型 :{safe_rel_type} 创建/合并关系时出错。")
        total_relations_attempted = len(contains_relations_data) + total_specific_rels_processed
        print(f"阶段 2 (TX)：关系创建/合并完成。总共尝试处理了 {total_relations_attempted} 个关系定义。")


    def upload_from_json_string(self, json_data_str):
        if not self._driver:
            print("错误：Neo4j 驱动未初始化。上传中止。")
            return False
        if not self._preprocess_data(json_data_str):
            print("错误：数据预处理失败。上传中止。")
            return False
        if not self.elements_by_id and not self.model_info.get("id"):
            print("错误：预处理后无元素且无模型信息。上传中止。")
            return False
        try:
            with self._driver.session(database="neo4j") as session:
                session.execute_write(self._create_nodes_tx_wrapper)      # 调用包装器
                session.execute_write(self._create_relationships_tx_wrapper) # 调用包装器
            print("数据上传过程成功完成。")
            return True
        except Exception as e:
            print(f"错误：在 Neo4j 会话或事务期间发生顶层错误：{e}")
            traceback.print_exc()
            return False

    def clear_database_content(self):
        if not self._driver:
            print("错误：Neo4j 驱动未初始化。")
            return
        confirm = input("警告：此操作将删除数据库中的所有数据！是否继续？ (yes/no): ")
        if confirm.lower() != 'yes':
            print("操作已取消。")
            return
        try:
            with self._driver.session(database="neo4j") as session:
                session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
            print("数据库内容已清空。")
        except Exception as e:
            print(f"清空数据库内容时出错: {e}")
            traceback.print_exc()

# --- 示例 JSON (使用您提供的最新版) ---
sample_req_json_for_neo4j_str = """
{
  "model": [
    {
      "id": "model-smartoffice-uuid",
      "name": "智慧办公套件系统模型"
    }
  ],
  "elements": [
    {
      "id": "pkg-doc-collab-uuid",
      "type": "Package",
      "name": "文档协同模块",
      "parentId": "model-smartoffice-uuid"
    },
    {
      "id": "req-doc-sec-verctrl-uuid",
      "type": "Requirement",
      "name": "保障文档安全与版本控制",
      "reqId": "REQ-DocSecVerCtrl",
      "text": "保障文档安全与版本控制",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "req-doc-realtimeedit-uuid",
      "type": "Requirement",
      "name": "文档多用户实时编辑",
      "reqId": "REQ-RealTimeEdit",
      "text": "系统必须支持文档多用户实时编辑",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "req-doc-fineperm-uuid",
      "type": "Requirement",
      "name": "细粒度的操作权限管理",
      "reqId": "REQ-FineGrainedPerm",
      "text": "需要实现细粒度的操作权限管理",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "blk-collab-edit-engine-uuid",
      "type": "Block",
      "name": "协同编辑引擎",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "blk-authz-service-uuid",
      "type": "Block",
      "name": "用户身份认证与授权服务",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "tc-concurrent-edit-pressure-uuid",
      "type": "TestCase",
      "name": "并发编辑压力测试",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "tc-multirole-perm-verify-uuid",
      "type": "TestCase",
      "name": "多角色权限组合验证",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "rel-derive-docsec-realtime-uuid",
      "type": "DeriveReqt",
      "sourceRequirementId": "req-doc-sec-verctrl-uuid",
      "derivedRequirementId": "req-doc-realtimeedit-uuid",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "rel-derive-docsec-fineperm-uuid",
      "type": "DeriveReqt",
      "sourceRequirementId": "req-doc-sec-verctrl-uuid",
      "derivedRequirementId": "req-doc-fineperm-uuid",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "rel-satisfy-collabedit-realtime-uuid",
      "type": "Satisfy",
      "blockId": "blk-collab-edit-engine-uuid",
      "requirementId": "req-doc-realtimeedit-uuid",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "rel-satisfy-authz-fineperm-uuid",
      "type": "Satisfy",
      "blockId": "blk-authz-service-uuid",
      "requirementId": "req-doc-fineperm-uuid",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "rel-verify-concurrentedit-realtime-uuid",
      "type": "Verify",
      "testCaseId": "tc-concurrent-edit-pressure-uuid",
      "requirementId": "req-doc-realtimeedit-uuid",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "rel-verify-multiroleperm-fineperm-uuid",
      "type": "Verify",
      "testCaseId": "tc-multirole-perm-verify-uuid",
      "requirementId": "req-doc-fineperm-uuid",
      "parentId": "pkg-doc-collab-uuid"
    },
    {
      "id": "pkg-task-schedule-uuid",
      "type": "Package",
      "name": "任务与日程管理",
      "parentId": "model-smartoffice-uuid"
    },
    {
      "id": "req-task-efficiency-uuid",
      "type": "Requirement",
      "name": "高效的任务分配与追踪",
      "reqId": "REQ-TaskEfficient",
      "text": "高效的任务分配与追踪",
      "parentId": "pkg-task-schedule-uuid"
    },
    {
      "id": "req-task-periodic-uuid",
      "type": "Requirement",
      "name": "支持创建周期性重复任务",
      "reqId": "REQ-PeriodicTask",
      "text": "支持创建周期性重复任务",
      "parentId": "pkg-task-schedule-uuid"
    },
    {
      "id": "blk-task-schedule-module-uuid",
      "type": "Block",
      "name": "任务调度与提醒模块",
      "parentId": "pkg-task-schedule-uuid"
    },
    {
      "id": "tc-periodic-task-gen-accuracy-uuid",
      "type": "TestCase",
      "name": "重复任务生成准确性测试",
      "parentId": "pkg-task-schedule-uuid"
    },
    {
      "id": "tc-task-assign-statusflow-uuid",
      "type": "TestCase",
      "name": "任务分配与状态流转测试",
      "parentId": "pkg-task-schedule-uuid"
    },
    {
      "id": "rel-derive-taskeff-periodic-uuid",
      "type": "DeriveReqt",
      "sourceRequirementId": "req-task-efficiency-uuid",
      "derivedRequirementId": "req-task-periodic-uuid",
      "parentId": "pkg-task-schedule-uuid"
    },
    {
      "id": "rel-satisfy-tasksched-periodic-uuid",
      "type": "Satisfy",
      "blockId": "blk-task-schedule-module-uuid",
      "requirementId": "req-task-periodic-uuid",
      "parentId": "pkg-task-schedule-uuid"
    },
    {
      "id": "rel-verify-periodictask-periodic-uuid",
      "type": "Verify",
      "testCaseId": "tc-periodic-task-gen-accuracy-uuid",
      "requirementId": "req-task-periodic-uuid",
      "parentId": "pkg-task-schedule-uuid"
    },
    {
      "id": "rel-verify-taskassign-efficiency-uuid",
      "type": "Verify",
      "testCaseId": "tc-task-assign-statusflow-uuid",
      "requirementId": "req-task-efficiency-uuid",
      "parentId": "pkg-task-schedule-uuid"
    }
  ]
}
"""

if __name__ == '__main__':
    uploader = None
    try:
        uploader = Neo4jRequirementUploader(URI, USER, "123456789") # 请修改为你的密码
        uploader.clear_database_content()

        print(f"\n--- 开始上传需求图数据到 Neo4j ({URI}) ---")
        success = uploader.upload_from_json_string(sample_req_json_for_neo4j_str)
        if success:
            print("--- 需求图数据上传成功 ---")
        else:
            print("--- 需求图数据上传失败 ---")

    except Exception as main_e:
        print(f"主程序发生错误: {main_e}")
        traceback.print_exc()
    finally:
        if uploader:
            uploader.close()