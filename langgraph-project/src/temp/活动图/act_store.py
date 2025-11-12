import json
from neo4j import GraphDatabase
import traceback
import copy

# ==== Neo4j 连接配置 ====
URI = "bolt://localhost:7687"
USER = "neo4j"
PASSWORD = "123456789" # 请替换为您的密码

# --- 辅助函数 ---
def generate_unique_id(base_id, suffix):
    """辅助函数，用于生成可能唯一的ID。"""
    clean_base = str(base_id).replace("-","")
    clean_suffix = str(suffix).replace("-","")
    return f"{clean_base}_{clean_suffix}"

class Neo4jUmlUploader:
    """
    用于将UML活动图JSON数据上传到Neo4j的类。
    v17: 修正泳道关系 (:IN_PARTITION, :REPRESENTS) 的存储逻辑。
    """
    def __init__(self, uri, user, password):
        """初始化驱动程序和实例变量。"""
        self._driver = None
        self.elements_by_id = {}
        self.children_by_parent = {}
        self.pins_by_parent_action = {}
        self.activity_elements = {}
        self.activity_partitions_data = [] # 用于存储原始分区数据
        self.blocks_data = []            # 用于存储原始块数据
        self.model_id = "model-root-uuid"
        self.model_name = "DefaultModelName"
        self.root_element_ids = []

        try:
            self._driver = GraphDatabase.driver(uri, auth=(user, password),
                                               connection_timeout=15, max_connection_lifetime=3600)
            self._driver.verify_connectivity()
            print("成功连接到 Neo4j 并验证连接。")
        except Exception as e:
            print(f"错误：无法连接到 Neo4j 或验证失败 - {e}"); self._driver = None

    def close(self):
        """关闭数据库连接。"""
        if self._driver: self._driver.close(); print("Neo4j 连接已关闭。")

    def _execute_write_tx(self, tx, query, parameters=None):
        """在事务中执行写入查询的辅助函数。"""
        try:
            # result = tx.run(query, parameters if parameters else {}) # 如果需要处理结果摘要
            tx.run(query, parameters if parameters else {}) # 对于 MERGE/CREATE，通常不需要处理结果
            return True
        except Exception as e:
            print(f"错误：执行 Cypher 查询失败\n查询: {query}\n参数: {parameters}\n错误: {e}")
            traceback.print_exc(); raise e

    def _preprocess_data(self, json_data):
        """预处理JSON数据，填充实例变量。"""
        # Reset instance variables
        self.elements_by_id.clear(); self.children_by_parent.clear(); self.pins_by_parent_action.clear()
        self.activity_elements.clear(); self.activity_partitions_data.clear(); self.blocks_data.clear()
        self.root_element_ids.clear(); self.model_id = "model-root-uuid"; self.model_name = "DefaultModelName"

        if "elements" not in json_data: print("错误：JSON 缺少 'elements' 键。"); return False
        elements_list = json_data["elements"]
        try: self.elements_by_id = {elem["id"]: elem for elem in elements_list}
        except (KeyError, TypeError) as e: print(f"JSON 元素错误: {e}"); return False

        if "model" in json_data and json_data["model"]:
            try: self.model_name = json_data["model"][0].get("name", self.model_name)
            except (IndexError, AttributeError): pass

        self.pins_by_parent_action = {}
        for elem_id, elem_data in self.elements_by_id.items():
            parent_id = elem_data.get("parentId"); elem_type = elem_data.get("type", "Unknown")
            current_parent_id = parent_id if parent_id is not None else self.model_id
            if current_parent_id not in self.children_by_parent: self.children_by_parent[current_parent_id] = []
            self.children_by_parent[current_parent_id].append(elem_id)
            if parent_id is None: self.root_element_ids.append(elem_id)

            activity_parent_id = parent_id
            if activity_parent_id and activity_parent_id in self.elements_by_id and self.elements_by_id[activity_parent_id]["type"] == "Activity":
                 if activity_parent_id not in self.activity_elements: self.activity_elements[activity_parent_id] = {"nodes": [], "edges": [], "groups": []}
                 node_types = ["InitialNode", "ActivityFinalNode", "FlowFinalNode", "DecisionNode", "MergeNode", "ForkNode", "JoinNode", "CallBehaviorAction", "ActivityParameterNode", "CentralBufferNode"]
                 edge_types = ["ControlFlow", "ObjectFlow"]
                 if elem_type in node_types: self.activity_elements[activity_parent_id]["nodes"].append(elem_id)
                 elif elem_type in edge_types: self.activity_elements[activity_parent_id]["edges"].append(elem_id)
                 elif elem_type == "ActivityPartition": self.activity_elements[activity_parent_id]["groups"].append(elem_id)
            elif elem_type in ["InputPin", "OutputPin"]:
                 if parent_id:
                     if parent_id not in self.pins_by_parent_action: self.pins_by_parent_action[parent_id] = []
                     self.pins_by_parent_action[parent_id].append(elem_id)
            # *** 确保存储分区和块数据 ***
            if elem_type == "ActivityPartition": self.activity_partitions_data.append(elem_data)
            elif elem_type == "Block": self.blocks_data.append(elem_data)
        return True

    def _create_nodes_in_batches(self, session):
        """第一阶段：只创建代表实际UML元素的节点（排除Flows）。"""
        print("阶段 1：开始创建/合并节点 (不包括 Flow)...")
        skipped_count = 0
        elements_by_type = {}
        node_types_to_create = [
            "Package", "Block", "Activity", "InitialNode", "ActivityFinalNode", "FlowFinalNode",
            "DecisionNode", "MergeNode", "ForkNode", "JoinNode", "CallBehaviorAction",
            "ActivityParameterNode", "CentralBufferNode", "InputPin", "OutputPin", "ActivityPartition"
            # 不再需要为 LiteralUnlimitedNatural, OpaqueExpression 创建节点，它们作为关系属性存储
        ]

        for element_id, element in self.elements_by_id.items():
            elem_type = element.get("type")
            if elem_type not in node_types_to_create:
                if elem_type not in ["ControlFlow", "ObjectFlow"]: # 忽略 Flow，报告其他未知类型
                     print(f"警告：在节点创建阶段跳过未知/非节点类型 '{elem_type}' (ID: {element_id})")
                skipped_count += 1; continue

            label = elem_type.replace(" ", "_").replace("-","_")
            if label not in elements_by_type: elements_by_type[label] = []
            elements_by_type[label].append(element)

        total_nodes_processed = 0
        for label, items in elements_by_type.items():
            batch_data = []
            for item in items:
                props = {"elementId": item["id"], "type": item["type"]}
                if "name" in item: props["name"] = item["name"]
                if "behavior" in item: props["behavior"] = item["behavior"]
                if "representsId" in item: props["representsId"] = item["representsId"] # 确保存储 representsId
                if "typeId" in item: props["typeId"] = item["typeId"] # 确保存储 typeId
                batch_data.append(props)

            safe_label = label.replace('`','').replace(':','')
            if not safe_label or not (safe_label[0].isalpha() and all(c.isalnum() or c == '_' for c in safe_label)):
                print(f"警告：跳过无效的标签名 '{label}'"); skipped_count += len(items); continue

            query = f"""
            UNWIND $batch as item_props
            MERGE (n:`{safe_label}` {{elementId: item_props.elementId}})
            ON CREATE SET n = item_props
            ON MATCH SET n += item_props
            RETURN count(n)
            """
            try:
                result = session.execute_write(self._execute_write_tx, query, parameters={"batch": batch_data})
                if result: total_nodes_processed += len(batch_data); print(f"  - 处理了 {len(batch_data)} 个节点，标签为 :{safe_label}")
            except Exception as e: print(f"错误：为标签 :{safe_label} 创建节点时出错：{e}")

        print(f"阶段 1：节点创建/合并完成。处理了 {total_nodes_processed} 个节点，跳过了 {skipped_count} 个元素。")

    def _create_relationships_in_batches(self, session):
        """第二阶段：创建关系，包括 Flow 关系。"""
        print("阶段 2：开始创建/合并关系...")
        rel_count = 0
        rel_batches = { "CONTAINS": [], "CONTROL_FLOW": [], "OBJECT_FLOW": [], "HAS_PIN": [],
                        "HAS_TYPE": [], "REPRESENTS": [], "CALLS": [], "IN_PARTITION": [] }

        for elem_id, element in self.elements_by_id.items():
            elem_type = element.get("type"); parent_id = element.get("parentId")

            # :CONTAINS (只为非Flow元素创建)
            if parent_id and elem_type not in ["ControlFlow", "ObjectFlow"]:
                rel_batches["CONTAINS"].append({"parentId": parent_id, "childId": elem_id})

            # Flows 作为关系
            elif elem_type in ["ControlFlow", "ObjectFlow"]:
                source_id = element.get("sourceId"); target_id = element.get("targetId")
                if source_id and target_id:
                    rel_props = {"elementId": elem_id}
                    if "name" in element: rel_props["name"] = element["name"]
                    if elem_type == "ControlFlow" and "guard" in element: rel_props["guard"] = element["guard"]
                    # weight 不再是节点，直接作为关系属性 (如果需要的话)
                    # if elem_type in ["ControlFlow", "ObjectFlow"]: rel_props["weight"] = "1" # 根据需要添加 weight
                    rel_key = elem_type.upper().replace("FLOW", "_FLOW")
                    if rel_key in rel_batches:
                        rel_batches[rel_key].append({"sourceId": source_id, "targetId": target_id, "props": rel_props})

            # :HAS_TYPE
            elif "typeId" in element and elem_type in ["InputPin", "OutputPin", "ActivityParameterNode", "CentralBufferNode"]:
                type_id = element["typeId"]
                if type_id: rel_batches["HAS_TYPE"].append({"elemId": elem_id, "typeId": type_id})
            # :REPRESENTS - 在下面单独处理
            # :CALLS
            elif elem_type == "CallBehaviorAction" and "behavior" in element:
                behavior_id = element["behavior"]
                if behavior_id: rel_batches["CALLS"].append({"actionId": elem_id, "behaviorId": behavior_id})
            # :IN_PARTITION - 在下面单独处理

        # --- 单独处理 HAS_PIN ---
        for action_id, pin_ids in self.pins_by_parent_action.items():
            for pin_id in pin_ids: rel_batches["HAS_PIN"].append({"actionId": action_id, "pinId": pin_id})

        # --- 单独处理 REPRESENTS 和 IN_PARTITION (迭代存储的分区数据) ---
        for part_data in self.activity_partitions_data: # 使用存储的原始分区数据
            partition_id = part_data["id"]
            # 处理 :REPRESENTS
            rep_id = part_data.get("representsId")
            if rep_id:
                rel_batches["REPRESENTS"].append({"partId": partition_id, "repId": rep_id})
            # 处理 :IN_PARTITION
            for node_id in part_data.get("nodeIds", []):
                 node_data = self.elements_by_id.get(node_id) # 检查节点是否存在且非 Fork/Join
                 if node_data and node_data.get("type") not in ['ForkNode', 'JoinNode']:
                      rel_batches["IN_PARTITION"].append({"nodeId": node_id, "partitionId": partition_id})

        # --- 在单个事务中执行所有关系创建 ---
        def tx_create_all_rels(tx):
            nonlocal rel_count; tx_rel_count = 0
            # Cypher 查询语句
            queries = {
                "CONTAINS": "UNWIND $batch as d MATCH (p {elementId: d.parentId}) MATCH (c {elementId: d.childId}) MERGE (p)-[r:CONTAINS]->(c) RETURN count(r)",
                "CONTROL_FLOW": "UNWIND $batch as d MATCH (s {elementId: d.sourceId}) MATCH (t {elementId: d.targetId}) MERGE (s)-[r:CONTROL_FLOW {elementId: d.props.elementId}]->(t) SET r += d.props RETURN count(r)",
                "OBJECT_FLOW": "UNWIND $batch as d MATCH (s {elementId: d.sourceId}) MATCH (t {elementId: d.targetId}) MERGE (s)-[r:OBJECT_FLOW {elementId: d.props.elementId}]->(t) SET r += d.props RETURN count(r)",
                "HAS_PIN": "UNWIND $batch as d MATCH (a {elementId: d.actionId}) WHERE labels(a)[0] IN ['CallBehaviorAction', 'Action'] MATCH (p {elementId: d.pinId}) WHERE labels(p)[0] IN ['InputPin', 'OutputPin'] MERGE (a)-[r:HAS_PIN]->(p) RETURN count(r)", # 明确 Action 标签
                "HAS_TYPE": "UNWIND $batch as d MATCH (e {elementId: d.elemId}) WHERE labels(e)[0] IN ['InputPin', 'OutputPin', 'ActivityParameterNode', 'CentralBufferNode'] MATCH (b:Block {elementId: d.typeId}) MERGE (e)-[r:HAS_TYPE]->(b) RETURN count(r)",
                "REPRESENTS": "UNWIND $batch as d MATCH (p:ActivityPartition {elementId: d.partId}) MATCH (b:Block {elementId: d.repId}) MERGE (p)-[r:REPRESENTS]->(b) RETURN count(r)",
                "CALLS": "UNWIND $batch as d MATCH (a:CallBehaviorAction {elementId: d.actionId}) MATCH (c:Activity {elementId: d.behaviorId}) MERGE (a)-[r:CALLS]->(c) RETURN count(r)",
                "IN_PARTITION": "UNWIND $batch as d MATCH (n {elementId: d.nodeId}) WHERE NOT labels(n)[0] IN ['ForkNode', 'JoinNode'] MATCH (p:ActivityPartition {elementId: d.partitionId}) MERGE (n)-[r:IN_PARTITION]->(p) RETURN count(r)"
            }
            for rel_type, batch in rel_batches.items():
                if batch:
                    try:
                        self._execute_write_tx(tx, queries[rel_type], parameters={"batch": batch})
                        tx_rel_count += len(batch)
                        print(f"    - 成功创建/合并 {len(batch)} 个 :{rel_type} 关系") # 添加日志
                    except KeyError: print(f"警告：未找到关系类型 '{rel_type}' 的查询。"); continue
                    except Exception as exec_e: print(f"错误：创建类型为 '{rel_type}' 的关系时出错。"); raise exec_e
            rel_count = tx_rel_count

        try:
            session.execute_write(tx_create_all_rels)
            print(f"阶段 2：关系创建/合并完成。尝试处理了 {rel_count} 个关系。")
        except Exception as e: print(f"错误：在关系创建事务期间发生顶层错误：{e}")

    def upload_from_json_data(self, json_data):
        """将解析后的 JSON 数据上传到 Neo4j。"""
        if not self._driver: print("错误：Neo4j 驱动未初始化。"); return
        success = self._preprocess_data(json_data)
        if not success: print("错误：数据预处理失败。"); return
        if not self.elements_by_id: print("错误：预处理后无元素。"); return

        try:
            with self._driver.session(database="neo4j") as session:
                self._create_nodes_in_batches(session)
                self._create_relationships_in_batches(session)
            print("数据上传过程完成。")
        except Exception as e: print(f"错误：在 Neo4j 会话期间发生错误：{e}"); traceback.print_exc()

    def clear_database(self):
        """ 清空数据库，谨慎使用！"""
        if not self._driver: print("错误：Neo4j 驱动未初始化。"); return
        print("警告：即将清空整个 Neo4j 数据库！")
        try:
            with self._driver.session(database="neo4j") as session:
                session.execute_write(lambda tx: tx.run("MATCH (n) DETACH DELETE n"))
            print("数据库已清空。")
        except Exception as e: print(f"清空数据库时出错: {e}")


# --- 主执行块 ---
if __name__ == "__main__":
    json_string = """
{
  "model": [
    {
      "id": "model-docreview-partitions-uuid",
      "name": "DocumentReviewApprovalModel_WithPartitions"
    }
  ],
  "elements": [
    {
      "id": "pkg-docreview-uuid",
      "type": "Package",
      "name": "DocumentReview"
    },
    {
      "id": "blk-doc-submission-uuid",
      "type": "Block",
      "name": "DocumentSubmission",
      "parentId": "pkg-docreview-uuid"
    },
    {
      "id": "blk-prepared-doc-uuid",
      "type": "Block",
      "name": "PreparedDocument",
      "parentId": "pkg-docreview-uuid"
    },
    {
      "id": "blk-review-status-uuid",
      "type": "Block",
      "name": "ReviewStatus",
      "parentId": "pkg-docreview-uuid"
    },
    {
      "id": "blk-final-decision-uuid",
      "type": "Block",
      "name": "FinalDecision",
      "parentId": "pkg-docreview-uuid"
    },
    {
      "id": "blk-notification-context-uuid",
      "type": "Block",
      "name": "NotificationContext",
      "parentId": "pkg-docreview-uuid"
    },
    {
      "id": "blk-docproc-svc-uuid",
      "type": "Block",
      "name": "DocumentProcessingService",
      "parentId": "pkg-docreview-uuid"
    },
    {
      "id": "blk-review-a-svc-uuid",
      "type": "Block",
      "name": "ReviewDeptA",
      "parentId": "pkg-docreview-uuid"
    },
    {
      "id": "blk-review-b-svc-uuid",
      "type": "Block",
      "name": "ReviewDeptB",
      "parentId": "pkg-docreview-uuid"
    },
    {
      "id": "blk-notify-svc-uuid",
      "type": "Block",
      "name": "NotificationService",
      "parentId": "pkg-docreview-uuid"
    },
    {
      "id": "act-main-review-uuid",
      "type": "Activity",
      "name": "主文档审查活动",
      "parentId": "pkg-docreview-uuid",
      "nodes": [
        "cbuf-dr-final-decision",
        "cbuf-dr-prepared-doc",
        "node-dr-approve",
        "node-dr-consolidate",
        "node-dr-decision",
        "node-dr-final",
        "node-dr-fork",
        "node-dr-join",
        "node-dr-merge",
        "node-dr-prepare",
        "node-dr-reject",
        "node-dr-review-a",
        "node-dr-review-b",
        "node-dr-send-notify",
        "node-dr-start"
      ],
      "edges": [
        "edge-dr-cf1",
        "edge-dr-cf10-decision-approve",
        "edge-dr-cf11-decision-reject",
        "edge-dr-cf12-approve-merge",
        "edge-dr-cf13-reject-merge",
        "edge-dr-cf14-merge-notify",
        "edge-dr-cf15-notify-final",
        "edge-dr-cf2",
        "edge-dr-cf4-fork-a",
        "edge-dr-cf5-fork-b",
        "edge-dr-cf6-reviewa-join",
        "edge-dr-cf7-reviewb-join",
        "edge-dr-cf8-join-consol",
        "edge-dr-cf9-consol-decision",
        "edge-dr-of1-prepare-in",
        "edge-dr-of10-buf-reject",
        "edge-dr-of11-buf-decision-reject",
        "edge-dr-of12-approve-notify",
        "edge-dr-of13-reject-notify",
        "edge-dr-of2-prepare-buf",
        "edge-dr-of3-buf-reviewa",
        "edge-dr-of4-buf-reviewb",
        "edge-dr-of5-reviewa-consol",
        "edge-dr-of6-reviewb-consol",
        "edge-dr-of7-consol-buf",
        "edge-dr-of8-buf-approve",
        "edge-dr-of9-buf-decision-approve"
      ],
      "groups": [
        "grp-docproc-uuid",
        "grp-notify-uuid",
        "grp-review-a-uuid",
        "grp-review-b-uuid"
      ]
    },
    {
      "id": "act-review-dept-a",
      "type": "Activity",
      "name": "部门A审查文档",
      "parentId": "pkg-docreview-uuid",
      "nodes": [],
      "edges": [],
      "groups": []
    },
    {
      "id": "act-review-dept-b",
      "type": "Activity",
      "name": "部门B审查文档",
      "parentId": "pkg-docreview-uuid",
      "nodes": [],
      "edges": [],
      "groups": []
    },
    {
      "id": "grp-docproc-uuid",
      "type": "ActivityPartition",
      "name": "文档处理服务",
      "representsId": "blk-docproc-svc-uuid",
      "parentId": "act-main-review-uuid",
      "nodeIds": [
        "node-dr-approve",
        "node-dr-consolidate",
        "node-dr-decision",
        "node-dr-merge",
        "node-dr-prepare",
        "node-dr-reject"
      ]
    },
    {
      "id": "grp-review-a-uuid",
      "type": "ActivityPartition",
      "name": "部门A",
      "representsId": "blk-review-a-svc-uuid",
      "parentId": "act-main-review-uuid",
      "nodeIds": [
        "node-dr-review-a"
      ]
    },
    {
      "id": "grp-review-b-uuid",
      "type": "ActivityPartition",
      "name": "部门B",
      "representsId": "blk-review-b-svc-uuid",
      "parentId": "act-main-review-uuid",
      "nodeIds": [
        "node-dr-review-b"
      ]
    },
    {
      "id": "grp-notify-uuid",
      "type": "ActivityPartition",
      "name": "通知服务",
      "representsId": "blk-notify-svc-uuid",
      "parentId": "act-main-review-uuid",
      "nodeIds": [
        "node-dr-send-notify"
      ]
    },
    {
      "id": "node-dr-start",
      "type": "InitialNode",
      "name": "接收文档提交",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-prepare",
      "type": "CallBehaviorAction",
      "name": "准备文档",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-review-a",
      "type": "CallBehaviorAction",
      "name": "部门A审阅",
      "behavior": "act-review-dept-a",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-review-b",
      "type": "CallBehaviorAction",
      "name": "部门B审阅",
      "behavior": "act-review-dept-b",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-consolidate",
      "type": "CallBehaviorAction",
      "name": "汇总审阅结果",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-approve",
      "type": "CallBehaviorAction",
      "name": "标记文档已批准",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-reject",
      "type": "CallBehaviorAction",
      "name": "标记文档已拒绝",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-send-notify",
      "type": "CallBehaviorAction",
      "name": "发送通知",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "pin-dr-prepare-in",
      "type": "InputPin",
      "name": "in_提交文档",
      "typeId": "blk-doc-submission-uuid",
      "parentId": "node-dr-prepare"
    },
    {
      "id": "pin-dr-review-a-in",
      "type": "InputPin",
      "name": "in_文档",
      "typeId": "blk-prepared-doc-uuid",
      "parentId": "node-dr-review-a"
    },
    {
      "id": "pin-dr-review-b-in",
      "type": "InputPin",
      "name": "in_文档",
      "typeId": "blk-prepared-doc-uuid",
      "parentId": "node-dr-review-b"
    },
    {
      "id": "pin-dr-consol-a-in",
      "type": "InputPin",
      "name": "in_状态A",
      "typeId": "blk-review-status-uuid",
      "parentId": "node-dr-consolidate"
    },
    {
      "id": "pin-dr-consol-b-in",
      "type": "InputPin",
      "name": "in_状态B",
      "typeId": "blk-review-status-uuid",
      "parentId": "node-dr-consolidate"
    },
    {
      "id": "pin-dr-approve-in-doc",
      "type": "InputPin",
      "name": "in_待批准文档",
      "typeId": "blk-prepared-doc-uuid",
      "parentId": "node-dr-approve"
    },
    {
      "id": "pin-dr-approve-in-decision",
      "type": "InputPin",
      "name": "in_批准决策",
      "typeId": "blk-final-decision-uuid",
      "parentId": "node-dr-approve"
    },
    {
      "id": "pin-dr-reject-in-doc",
      "type": "InputPin",
      "name": "in_待拒绝文档",
      "typeId": "blk-prepared-doc-uuid",
      "parentId": "node-dr-reject"
    },
    {
      "id": "pin-dr-reject-in-decision",
      "type": "InputPin",
      "name": "in_拒绝决策",
      "typeId": "blk-final-decision-uuid",
      "parentId": "node-dr-reject"
    },
    {
      "id": "pin-dr-notify-in",
      "type": "InputPin",
      "name": "in_通知上下文",
      "typeId": "blk-notification-context-uuid",
      "parentId": "node-dr-send-notify"
    },
    {
      "id": "pin-dr-prepare-out",
      "type": "OutputPin",
      "name": "out_待审阅文档",
      "typeId": "blk-prepared-doc-uuid",
      "parentId": "node-dr-prepare"
    },
    {
      "id": "pin-dr-review-a-out",
      "type": "OutputPin",
      "name": "out_状态A",
      "typeId": "blk-review-status-uuid",
      "parentId": "node-dr-review-a"
    },
    {
      "id": "pin-dr-review-b-out",
      "type": "OutputPin",
      "name": "out_状态B",
      "typeId": "blk-review-status-uuid",
      "parentId": "node-dr-review-b"
    },
    {
      "id": "pin-dr-consol-out",
      "type": "OutputPin",
      "name": "out_最终决策",
      "typeId": "blk-final-decision-uuid",
      "parentId": "node-dr-consolidate"
    },
    {
      "id": "pin-dr-approve-out-ctx",
      "type": "OutputPin",
      "name": "out_批准通知上下文",
      "typeId": "blk-notification-context-uuid",
      "parentId": "node-dr-approve"
    },
    {
      "id": "pin-dr-reject-out-ctx",
      "type": "OutputPin",
      "name": "out_拒绝通知上下文",
      "typeId": "blk-notification-context-uuid",
      "parentId": "node-dr-reject"
    },
    {
      "id": "cbuf-dr-prepared-doc",
      "type": "CentralBufferNode",
      "name": "待审阅文档缓存",
      "typeId": "blk-prepared-doc-uuid",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "cbuf-dr-final-decision",
      "type": "CentralBufferNode",
      "name": "审阅决策缓存",
      "typeId": "blk-final-decision-uuid",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-fork",
      "type": "ForkNode",
      "name": "分发审阅",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-join",
      "type": "JoinNode",
      "name": "等待审阅完成",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-decision",
      "type": "DecisionNode",
      "name": "审阅是否通过?",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-merge",
      "type": "MergeNode",
      "name": "汇合处理路径",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "node-dr-final",
      "type": "ActivityFinalNode",
      "name": "审查结束",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf1",
      "type": "ControlFlow",
      "sourceId": "node-dr-start",
      "targetId": "node-dr-prepare",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of1-prepare-in",
      "type": "ObjectFlow",
      "sourceId": "node-dr-start",
      "targetId": "pin-dr-prepare-in",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf2",
      "type": "ControlFlow",
      "sourceId": "node-dr-prepare",
      "targetId": "node-dr-fork",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf6-reviewa-join",
      "type": "ControlFlow",
      "sourceId": "node-dr-review-a",
      "targetId": "node-dr-join",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf7-reviewb-join",
      "type": "ControlFlow",
      "sourceId": "node-dr-review-b",
      "targetId": "node-dr-join",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf9-consol-decision",
      "type": "ControlFlow",
      "sourceId": "node-dr-consolidate",
      "targetId": "node-dr-decision",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf12-approve-merge",
      "type": "ControlFlow",
      "sourceId": "node-dr-approve",
      "targetId": "node-dr-merge",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf13-reject-merge",
      "type": "ControlFlow",
      "sourceId": "node-dr-reject",
      "targetId": "node-dr-merge",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf15-notify-final",
      "type": "ControlFlow",
      "sourceId": "node-dr-send-notify",
      "targetId": "node-dr-final",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of2-prepare-buf",
      "type": "ObjectFlow",
      "sourceId": "pin-dr-prepare-out",
      "targetId": "cbuf-dr-prepared-doc",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of5-reviewa-consol",
      "type": "ObjectFlow",
      "sourceId": "pin-dr-review-a-out",
      "targetId": "pin-dr-consol-a-in",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of6-reviewb-consol",
      "type": "ObjectFlow",
      "sourceId": "pin-dr-review-b-out",
      "targetId": "pin-dr-consol-b-in",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of7-consol-buf",
      "type": "ObjectFlow",
      "sourceId": "pin-dr-consol-out",
      "targetId": "cbuf-dr-final-decision",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of12-approve-notify",
      "type": "ObjectFlow",
      "sourceId": "pin-dr-approve-out-ctx",
      "targetId": "pin-dr-notify-in",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of13-reject-notify",
      "type": "ObjectFlow",
      "sourceId": "pin-dr-reject-out-ctx",
      "targetId": "pin-dr-notify-in",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of8-buf-approve",
      "type": "ObjectFlow",
      "sourceId": "cbuf-dr-prepared-doc",
      "targetId": "pin-dr-approve-in-doc",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of4-buf-reviewb",
      "type": "ObjectFlow",
      "sourceId": "cbuf-dr-prepared-doc",
      "targetId": "pin-dr-review-b-in",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of3-buf-reviewa",
      "type": "ObjectFlow",
      "sourceId": "cbuf-dr-prepared-doc",
      "targetId": "pin-dr-review-a-in",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of10-buf-reject",
      "type": "ObjectFlow",
      "sourceId": "cbuf-dr-prepared-doc",
      "targetId": "pin-dr-reject-in-doc",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of9-buf-decision-approve",
      "type": "ObjectFlow",
      "sourceId": "cbuf-dr-final-decision",
      "targetId": "pin-dr-approve-in-decision",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-of11-buf-decision-reject",
      "type": "ObjectFlow",
      "sourceId": "cbuf-dr-final-decision",
      "targetId": "pin-dr-reject-in-decision",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf5-fork-b",
      "type": "ControlFlow",
      "sourceId": "node-dr-fork",
      "targetId": "node-dr-review-b",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf4-fork-a",
      "type": "ControlFlow",
      "sourceId": "node-dr-fork",
      "targetId": "node-dr-review-a",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf8-join-consol",
      "type": "ControlFlow",
      "sourceId": "node-dr-join",
      "targetId": "node-dr-consolidate",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf10-decision-approve",
      "type": "ControlFlow",
      "sourceId": "node-dr-decision",
      "targetId": "node-dr-approve",
      "guard": "[approved]",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf11-decision-reject",
      "type": "ControlFlow",
      "sourceId": "node-dr-decision",
      "targetId": "node-dr-reject",
      "guard": "[rejected]",
      "parentId": "act-main-review-uuid"
    },
    {
      "id": "edge-dr-cf14-merge-notify",
      "type": "ControlFlow",
      "sourceId": "node-dr-merge",
      "targetId": "node-dr-send-notify",
      "parentId": "act-main-review-uuid"
    }
  ]
}
"""

    # 尝试加载 JSON
    try: data = json.loads(json_string)
    except json.JSONDecodeError as e: print(f"JSON 解析错误: {e}"); data = None

    # 如果 JSON 加载成功，则执行上传
    if data:
        uploader = Neo4jUmlUploader(URI, USER, PASSWORD)
        if uploader._driver:
            uploader.clear_database() # 可选：清空数据库
            uploader.upload_from_json_data(data)
            uploader.close()
        else: print("由于 Neo4j 连接失败，上传未执行。")
    else: print("由于 JSON 解析错误，上传未执行。")

