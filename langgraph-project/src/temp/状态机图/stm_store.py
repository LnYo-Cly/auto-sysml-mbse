import json
from neo4j import GraphDatabase
import traceback

# ==== Neo4j 连接配置 ====
URI = "bolt://localhost:7687" # 请根据您的Neo4j实例调整
USER = "neo4j"
PASSWORD = "123456789"  # 请替换为您的实际密码

class Neo4jStateMachineUploader:
    def __init__(self, uri, user, password):
        self._driver = None
        self.elements_by_id = {} # 存储从JSON加载的所有元素
        self.model_info = {}     # 存储顶层模型信息

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
            result = tx.run(query, parameters if parameters else {})
            return result # 返回结果，以便可以检查摘要或记录
        except Exception as e:
            print(f"错误：执行 Cypher 查询失败\n查询: {query}\n参数: {parameters}\n错误: {e}")
            traceback.print_exc()
            raise # 重新抛出异常，以便事务可以回滚

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
            # 将所有元素（包括模型信息，如果它有ID并被其他元素引用）放入elements_by_id
            for elem in elements_list:
                self.elements_by_id[elem["id"]] = elem
        except KeyError as e:
            print(f"错误：JSON元素缺少 'id' 字段：{e}")
            return False
        except TypeError as e:
            print(f"错误：迭代JSON元素列表时出现问题：{e}")
            return False

        if "model" in json_data and isinstance(json_data["model"], list) and json_data["model"]:
            self.model_info = json_data["model"][0]
            # 如果模型信息有ID，也加入elements_by_id，以防有元素直接父级是模型ID
            if "id" in self.model_info:
                self.elements_by_id[self.model_info["id"]] = self.model_info
        else:
            print("警告：JSON数据中未找到顶层 'model' 信息。将使用默认模型。")
            self.model_info = {"id": "default-model-id", "name": "DefaultStateMachineModel", "type": "Model"}
            self.elements_by_id[self.model_info["id"]] = self.model_info
        return True

    def _create_nodes_tx(self, tx):
        print("阶段 1 (TX)：开始创建/合并节点...")
        nodes_to_create_batch = []

        # 1. 创建模型节点
        if self.model_info and "id" in self.model_info:
            model_props = {"elementId": self.model_info["id"], "type": self.model_info.get("type", "Model")}
            if "name" in self.model_info: model_props["name"] = self.model_info["name"]
            nodes_to_create_batch.append({"label": self.model_info.get("type", "Model"), "props": model_props})

        # 2. 创建其他所有元素节点
        for element_id, element_data in self.elements_by_id.items():
            if element_id == self.model_info.get("id"): # 模型节点已处理
                continue

            elem_type = element_data.get("type")
            if not elem_type:
                print(f"警告: 元素 {element_id} 缺少 'type' 字段。跳过节点创建。")
                continue

            label = elem_type # 直接使用JSON中的type作为标签
            props = {"elementId": element_id, "type": elem_type} # 基础属性

            # 添加通用属性
            if "name" in element_data: props["name"] = element_data["name"]

            # 特定类型属性
            if elem_type == "Pseudostate":
                if "kind" in element_data: props["kind"] = element_data["kind"]
            elif elem_type == "State":
                if "isComposite" in element_data: props["isComposite"] = element_data["isComposite"]
            elif elem_type == "Transition": # 转换的属性（guard, effect等）可以在创建关系时处理或直接放在节点上
                if "guard" in element_data and isinstance(element_data["guard"], dict):
                    props["guard_expression"] = element_data["guard"].get("expression")
                    props["guard_language"] = element_data["guard"].get("language")
            elif elem_type == "SignalEvent":
                if "signalId" in element_data: props["referencesSignalId"] = element_data["signalId"] # 稍后用于关系

            # 为了entry/exit/do/effect的wrapper activities，它们在JSON中没有独立条目，但ID被引用
            # 我们需要确保这些包装器活动节点也被创建（如果它们被视为独立节点的话）
            # 在这个模型中，我们将entry/exit/do/effect包装器活动建模为独立的:Activity节点
            # 需要从State和Transition的定义中提取这些wrapperActivityId
            if elem_type == "State":
                for behavior_key in ["entry", "exit", "doActivity"]:
                    if behavior_key in element_data and isinstance(element_data[behavior_key], dict):
                        wrapper_id = element_data[behavior_key].get("wrapperActivityId")
                        called_id = element_data[behavior_key].get("calledBehaviorId")
                        if wrapper_id:
                            # 检查是否已在主循环中作为:Activity处理（如果它们在JSON elements中有独立条目）
                            if wrapper_id not in self.elements_by_id:
                                wrapper_props = {"elementId": wrapper_id, "type": "Activity", "isWrapper": True}
                                # 尝试获取调用行为的名称作为包装器活动的名称
                                if called_id and called_id in self.elements_by_id:
                                    called_data = self.elements_by_id[called_id]
                                    if "name" in called_data:
                                        wrapper_props["name"] = f"{behavior_key}_{called_data['name']}"
                                else:
                                     wrapper_props["name"] = f"{behavior_key}_Wrapper_{element_id}" # Fallback name
                                nodes_to_create_batch.append({"label": "Activity", "props": wrapper_props})
            elif elem_type == "Transition":
                if "effect" in element_data and isinstance(element_data["effect"], dict):
                    wrapper_id = element_data["effect"].get("wrapperActivityId")
                    called_id = element_data["effect"].get("calledBehaviorId")
                    if wrapper_id and wrapper_id not in self.elements_by_id:
                        wrapper_props = {"elementId": wrapper_id, "type": "Activity", "isWrapper": True}
                        if called_id and called_id in self.elements_by_id:
                             called_data = self.elements_by_id[called_id]
                             if "name" in called_data:
                                wrapper_props["name"] = f"effect_{called_data['name']}"
                        else:
                            wrapper_props["name"] = f"effect_Wrapper_{element_id}"
                        nodes_to_create_batch.append({"label": "Activity", "props": wrapper_props})
            
            # 确保不重复添加已作为包装器处理的节点
            is_already_wrapper = any(n["props"]["elementId"] == element_id for n in nodes_to_create_batch if n["label"]=="Activity" and n["props"].get("isWrapper"))
            if not is_already_wrapper:
                nodes_to_create_batch.append({"label": label, "props": props})
        
        # 去重，以elementId为准
        final_nodes_to_create_map = {item["props"]["elementId"]: item for item in nodes_to_create_batch}
        final_nodes_to_create_list = list(final_nodes_to_create_map.values())

        # 分批执行MERGE
        grouped_batch = {}
        for item in final_nodes_to_create_list:
            label = item["label"]
            if label not in grouped_batch:
                grouped_batch[label] = []
            grouped_batch[label].append(item["props"])

        total_nodes_processed_tx = 0
        for label, props_list in grouped_batch.items():
            # Neo4j标签不能包含特殊字符，通常是大驼峰或下划线
            safe_label = "".join(c for c in label if c.isalnum() or c == '_')
            if not safe_label or not safe_label[0].isalpha():
                print(f"警告: 无效的标签 '{label}' (处理后为 '{safe_label}')。跳过批处理。")
                continue
            
            query = f"""
            UNWIND $props_list AS item_props
            MERGE (n:`{safe_label}` {{elementId: item_props.elementId}})
            ON CREATE SET n = item_props
            ON MATCH SET n += item_props 
            """
            # ON MATCH SET n += apoc.map.clean(item_props, [], ['']) // 如果想避免覆盖为null
            try:
                self._execute_write_tx(tx, query, parameters={"props_list": props_list})
                total_nodes_processed_tx += len(props_list)
                print(f"  - TX: 尝试处理了 {len(props_list)} 个 '{safe_label}' 节点。")
            except Exception: # 错误已在 _execute_write_tx 中打印
                pass # 继续处理其他标签

        print(f"阶段 1 (TX)：节点创建/合并完成。总共尝试处理了 {total_nodes_processed_tx} 个节点定义。")


    def _create_relationships_tx(self, tx):
        print("阶段 2 (TX)：开始创建/合并关系...")
        # 关系数据收集列表
        relations_batch = [] # 格式: {"from_id": "...", "to_id": "...", "rel_type": "...", "props": {...}}

        model_root_id = self.model_info.get("id", "default-model-id")

        for element_id, element_data in self.elements_by_id.items():
            if element_id == model_root_id: continue # 模型本身没有父级

            elem_type = element_data.get("type")
            parent_id = element_data.get("parentId")
            
            # 1. 结构关系 (CONTAINS, HAS_REGION, HAS_SUBVERTEX, HAS_CONNECTION_POINT)
            if parent_id:
                parent_node_data = self.elements_by_id.get(parent_id)
                if not parent_node_data:
                    print(f"警告: 元素 {element_id} 的父ID {parent_id} 未找到。跳过其父子关系创建。")
                    continue
                
                parent_type = parent_node_data.get("type")
                rel_type = None

                if parent_type in ["Model", "Package"]:
                    rel_type = "CONTAINS"
                elif parent_type == "Block" and elem_type == "StateMachine":
                    # 检查是否是 classifierBehavior
                    if parent_node_data.get("classifierBehaviorId") == element_id:
                        rel_type = "HAS_CLASSIFIER_BEHAVIOR"
                    else: # 否则是普通的 ownedBehavior 或 containment
                        rel_type = "CONTAINS" # 或者 :OWNS_BEHAVIOR
                elif parent_type == "StateMachine" and elem_type == "Region":
                    rel_type = "HAS_REGION"
                elif parent_type == "State" and elem_type == "Region": # 复合状态的Region
                    rel_type = "HAS_REGION"
                elif parent_type == "State" and elem_type == "Pseudostate" and \
                     element_data.get("kind") in ["entryPoint", "exitPoint"]:
                    rel_type = "HAS_CONNECTION_POINT"
                elif parent_type == "Region":
                    if elem_type in ["State", "Pseudostate"]:
                        rel_type = "HAS_SUBVERTEX"
                    elif elem_type == "Transition":
                        rel_type = "HAS_TRANSITION"
                
                if rel_type:
                    relations_batch.append({
                        "from_id": parent_id, "to_id": element_id,
                        "rel_type": rel_type, "props": {}
                    })

            # 2.特定于状态机的关系
            if elem_type == "Transition":
                if "sourceId" in element_data and "targetId" in element_data:
                    relations_batch.append({
                        "from_id": element_id, "to_id": element_data["sourceId"],
                        "rel_type": "HAS_SOURCE", "props": {}
                    })
                    relations_batch.append({
                        "from_id": element_id, "to_id": element_data["targetId"],
                        "rel_type": "HAS_TARGET", "props": {}
                    })
                if "triggerIds" in element_data:
                    for trigger_event_id in element_data["triggerIds"]:
                        relations_batch.append({
                            "from_id": element_id, "to_id": trigger_event_id,
                            "rel_type": "HAS_TRIGGER", "props": {}
                        })
                # Effect 关系 (Transition -> Wrapper Activity)
                if "effect" in element_data and isinstance(element_data["effect"], dict):
                    wrapper_id = element_data["effect"].get("wrapperActivityId")
                    if wrapper_id:
                         relations_batch.append({
                            "from_id": element_id, "to_id": wrapper_id,
                            "rel_type": "HAS_EFFECT", "props": {}
                        })


            elif elem_type == "State":
                # Entry/Exit/Do Behavior 关系 (State -> Wrapper Activity)
                for behavior_key, rel_type_str in [("entry", "HAS_ENTRY_BEHAVIOR"),
                                                   ("exit", "HAS_EXIT_BEHAVIOR"),
                                                   ("doActivity", "HAS_DO_BEHAVIOR")]:
                    if behavior_key in element_data and isinstance(element_data[behavior_key], dict):
                        wrapper_id = element_data[behavior_key].get("wrapperActivityId")
                        if wrapper_id:
                            relations_batch.append({
                                "from_id": element_id, "to_id": wrapper_id,
                                "rel_type": rel_type_str, "props": {}
                            })
            
            # 3. Wrapper Activity -> Called Activity 关系
            # This needs to be handled by iterating through states and transitions again, or checking if current element is a wrapper
            # The wrapper nodes were added in _create_nodes_tx, let's identify them from the main loop
            # Or, we can iterate through states/transitions again specifically for these
            
            # 4. SignalEvent -> Signal
            if elem_type == "SignalEvent" and "signalId" in element_data:
                relations_batch.append({
                    "from_id": element_id, "to_id": element_data["signalId"],
                    "rel_type": "REFERENCES_SIGNAL", "props": {}
                })

        # Add CALLS_BEHAVIOR relations
        for e_id, e_data in self.elements_by_id.items():
            e_type = e_data.get("type")
            if e_type == "State":
                for behavior_key in ["entry", "exit", "doActivity"]:
                    if behavior_key in e_data and isinstance(e_data[behavior_key], dict):
                        wrapper_id = e_data[behavior_key].get("wrapperActivityId")
                        called_id = e_data[behavior_key].get("calledBehaviorId")
                        if wrapper_id and called_id:
                            relations_batch.append({
                                "from_id": wrapper_id, "to_id": called_id,
                                "rel_type": "CALLS_BEHAVIOR", "props": {}
                            })
            elif e_type == "Transition":
                 if "effect" in e_data and isinstance(e_data["effect"], dict):
                    wrapper_id = e_data["effect"].get("wrapperActivityId")
                    called_id = e_data["effect"].get("calledBehaviorId")
                    if wrapper_id and called_id:
                        relations_batch.append({
                            "from_id": wrapper_id, "to_id": called_id,
                            "rel_type": "CALLS_BEHAVIOR", "props": {}
                        })


        # 执行关系创建
        total_rels_processed_tx = 0
        # 分组执行以提高效率（按关系类型）
        grouped_rels = {}
        for rel_item in relations_batch:
            rel_type = rel_item["rel_type"]
            if rel_type not in grouped_rels:
                grouped_rels[rel_type] = []
            grouped_rels[rel_type].append(rel_item)

        for rel_type_str, rel_list_for_type in grouped_rels.items():
            safe_rel_type = "".join(c for c in rel_type_str if c.isalnum() or c == '_').upper()
            if not safe_rel_type or not safe_rel_type[0].isalpha():
                print(f"警告: 无效的关系类型 '{rel_type_str}' (处理后为 '{safe_rel_type}')。跳过批处理。")
                continue
            
            # 为关系也添加一个唯一的relationId，如果它们本身在JSON中没有ID的话
            # 但这里我们的关系是从结构和引用中派生的，所以用源和目标ID来MERGE
            query = f"""
            UNWIND $rel_list AS rel_data
            MATCH (from_node {{elementId: rel_data.from_id}})
            MATCH (to_node {{elementId: rel_data.to_id}})
            MERGE (from_node)-[r:`{safe_rel_type}`]->(to_node)
            ON CREATE SET r = rel_data.props
            ON MATCH SET r += rel_data.props 
            """ # props目前为空，但可以扩展
            try:
                self._execute_write_tx(tx, query, parameters={"rel_list": rel_list_for_type})
                total_rels_processed_tx += len(rel_list_for_type)
                print(f"  - TX: 尝试处理了 {len(rel_list_for_type)} 个 :{safe_rel_type} 关系。")
            except Exception:
                pass

        print(f"阶段 2 (TX)：关系创建/合并完成。总共尝试处理了 {total_rels_processed_tx} 个关系定义。")


    def upload_from_json_string(self, json_data_str):
        if not self._driver:
            print("错误：Neo4j 驱动未初始化。上传中止。")
            return False
        if not self._preprocess_data(json_data_str):
            print("错误：数据预处理失败。上传中止。")
            return False
        if not self.elements_by_id and not self.model_info.get("id"): # 检查是否有数据
            print("错误：预处理后无元素且无模型信息。上传中止。")
            return False
        
        try:
            with self._driver.session(database="neo4j") as session: # 确保指定数据库
                session.execute_write(self._create_nodes_tx)
                session.execute_write(self._create_relationships_tx)
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

# --- 主程序 ---
if __name__ == '__main__':
    # 使用您提供的状态机JSON（已修复连接点引用的版本）
    state_machine_json_str = """
{
  "model": [
    {
      "id": "model-door-access-sm-uuid",
      "name": "门禁系统模型",
      "type": "Model"
    }
  ],
  "elements": [
    {
      "id": "pkg-door-controller-uuid",
      "type": "Package",
      "name": "门控制器模块",
      "parentId": "model-door-access-sm-uuid"
    },
    {
      "id": "pkg-door-behaviors-uuid",
      "type": "Package",
      "name": "门禁行为库",
      "parentId": "model-door-access-sm-uuid"
    },
    {
      "id": "blk-door-controller-uuid",
      "type": "Block",
      "name": "门控制器",
      "parentId": "pkg-door-controller-uuid",
      "classifierBehaviorId": "sm-door-access-uuid"
    },
    {
      "id": "sm-door-access-uuid",
      "type": "StateMachine",
      "name": "门禁状态机",
      "parentId": "blk-door-controller-uuid"
    },
    {
      "id": "region-door-main-uuid",
      "type": "Region",
      "name": "主区域",
      "parentId": "sm-door-access-uuid"
    },
    {
      "id": "ps-main-initial-uuid",
      "type": "Pseudostate",
      "kind": "initial",
      "parentId": "region-door-main-uuid"
    },
    {
      "id": "state-locked-uuid",
      "type": "State",
      "name": "锁定",
      "parentId": "region-door-main-uuid",
      "isComposite": true,
      "connectionPoints": [
        "ps-locked-entry-uuid",
        "ps-locked-exit-uuid"
      ],
      "regions": [
        "region-locked-sub-uuid"
      ]
    },
    {
      "id": "ps-locked-entry-uuid",
      "type": "Pseudostate",
      "kind": "entryPoint",
      "name": "ep_lock",
      "parentId": "state-locked-uuid"
    },
    {
      "id": "ps-locked-exit-uuid",
      "type": "Pseudostate",
      "kind": "exitPoint",
      "name": "xp_lock",
      "parentId": "state-locked-uuid"
    },
    {
      "id": "region-locked-sub-uuid",
      "type": "Region",
      "name": "内部安全检查",
      "parentId": "state-locked-uuid"
    },
    {
      "id": "ps-locked-sub-initial-uuid",
      "type": "Pseudostate",
      "kind": "initial",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "state-selfcheck-uuid",
      "type": "State",
      "name": "自检",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "ps-locked-sub-final-uuid",
      "type": "Pseudostate",
      "kind": "final",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "state-unlocking-uuid",
      "type": "State",
      "name": "开锁中",
      "parentId": "region-door-main-uuid",
      "entry": {
        "wrapperActivityId": "wrapper-entry-unlocking-uuid",
        "calledBehaviorId": "act-unlock-bolt-uuid"
      },
      "doActivity": {
        "wrapperActivityId": "wrapper-do-unlocking-uuid",
        "calledBehaviorId": "act-keep-door-open-uuid"
      },
      "exit": {
        "wrapperActivityId": "wrapper-exit-unlocking-uuid",
        "calledBehaviorId": "act-check-door-closed-uuid"
      }
    },
    {
      "id": "state-alarm-uuid",
      "type": "State",
      "name": "报警",
      "parentId": "region-door-main-uuid"
    },
    {
      "id": "trans-initial-to-locked-uuid",
      "type": "Transition",
      "sourceId": "ps-main-initial-uuid",
      "targetId": "ps-locked-entry-uuid", 
      "parentId": "region-door-main-uuid"
    },
    {
      "id": "trans-locked-to-unlocking-uuid",
      "type": "Transition",
      "sourceId": "ps-locked-exit-uuid", 
      "targetId": "state-unlocking-uuid",
      "parentId": "region-door-main-uuid",
      "triggerIds": [
        "event-valid-unlock-signal-uuid"
      ],
      "guard": {
        "expression": "安全系统已解除 == true",
        "language": "English"
      },
      "effect": {
        "wrapperActivityId": "wrapper-effect-t2-uuid",
        "calledBehaviorId": "act-log-unlock-attempt-uuid"
      }
    },
    {
      "id": "trans-unlocking-to-locked-uuid",
      "type": "Transition",
      "sourceId": "state-unlocking-uuid",
      "targetId": "state-locked-uuid", 
      "parentId": "region-door-main-uuid",
      "triggerIds": [
        "event-timeout-uuid"
      ],
      "effect": {
        "wrapperActivityId": "wrapper-effect-t3-uuid",
        "calledBehaviorId": "act-auto-lock-uuid"
      }
    },
    {
      "id": "trans-locked-to-alarm-uuid",
      "type": "Transition",
      "sourceId": "state-locked-uuid", 
      "targetId": "state-alarm-uuid",
      "parentId": "region-door-main-uuid",
      "triggerIds": [
        "event-forced-open-uuid"
      ],
      "effect": {
        "wrapperActivityId": "wrapper-effect-t4-uuid",
        "calledBehaviorId": "act-sound-alarm-uuid"
      }
    },
    {
      "id": "trans-entrypoint-to-subinitial-uuid",
      "type": "Transition",
      "sourceId": "ps-locked-entry-uuid",
      "targetId": "ps-locked-sub-initial-uuid",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "trans-subinitial-to-selfcheck-uuid",
      "type": "Transition",
      "sourceId": "ps-locked-sub-initial-uuid",
      "targetId": "state-selfcheck-uuid",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "trans-selfcheck-to-subfinal-uuid",
      "type": "Transition",
      "sourceId": "state-selfcheck-uuid",
      "targetId": "ps-locked-sub-final-uuid",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "sig-valid-unlock-uuid",
      "type": "Signal",
      "name": "有效开锁信号",
      "parentId": "pkg-door-controller-uuid"
    },
    {
      "id": "event-valid-unlock-signal-uuid",
      "type": "SignalEvent",
      "name": "有效开锁信号事件",
      "signalId": "sig-valid-unlock-uuid",
      "parentId": "pkg-door-controller-uuid"
    },
    {
      "id": "event-timeout-uuid",
      "type": "Event", 
      "name": "超时事件",
      "parentId": "pkg-door-controller-uuid"
    },
    {
      "id": "event-forced-open-uuid",
      "type": "Event",
      "name": "强制开门事件",
      "parentId": "pkg-door-controller-uuid"
    },
    {
      "id": "act-log-unlock-attempt-uuid",
      "type": "Activity",
      "name": "记录开锁尝试",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-unlock-bolt-uuid",
      "type": "Activity",
      "name": "解锁门闩",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-keep-door-open-uuid",
      "type": "Activity",
      "name": "保持门锁打开",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-check-door-closed-uuid",
      "type": "Activity",
      "name": "检查门是否已关闭",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-auto-lock-uuid",
      "type": "Activity",
      "name": "自动上锁",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-sound-alarm-uuid",
      "type": "Activity",
      "name": "鸣响警报",
      "parentId": "pkg-door-behaviors-uuid"
    }
  ]
}
"""
    uploader = None
    try:
        uploader = Neo4jStateMachineUploader(URI, USER, PASSWORD) # 请修改为你的密码
        # uploader.clear_database_content() # 取消注释以清空数据库

        print(f"\n--- 开始上传状态机数据到 Neo4j ({URI}) ---")
        success = uploader.upload_from_json_string(state_machine_json_str)
        if success:
            print("--- 状态机数据上传成功 ---")
        else:
            print("--- 状态机数据上传失败 ---")

    except Exception as main_e:
        print(f"主程序发生错误: {main_e}")
        traceback.print_exc()
    finally:
        if uploader:
            uploader.close()