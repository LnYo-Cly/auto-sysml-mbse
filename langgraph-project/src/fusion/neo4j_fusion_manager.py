# step3_fusion/neo4j_fusion_manager.py

import sys
import os
from typing import Dict, Any, List

from connections.database_connectors import get_neo4j_driver
from fusion.relationship_rules import (SINGLE_REF_RULES, LIST_REF_RULES, 
                                COMPLEX_REF_RULES, CONNECTOR_END_REF_FIELDS,
                                NESTED_BEHAVIOR_RULES)
class Neo4jFusionManager:
    """
    负责将模型元素结构化地融合到Neo4j图数据库中。
    """
    def __init__(self):
        """初始化管理器并获取Neo4j驱动。"""
        self.driver = get_neo4j_driver()
        if not self.driver:
            raise ConnectionError("无法连接到Neo4j数据库，请检查配置和数据库状态。")
        print("Neo4jFusionManager 初始化成功。")

    def _get_target_key(self, source_key: str, key_remap: Dict[str, str]) -> str:
        """
        辅助函数：根据重映射表找到一个键最终应该指向的目标键。
        这可以处理多重重定向，例如 A->B, B->C，最终A应该指向C。
        """
        visited = set()
        current_key = source_key
        while current_key in key_remap:
            if current_key in visited:
                # 检测到循环引用，中断以避免无限循环
                print(f"  - 警告: 在重映射表中检测到循环引用: {current_key}")
                return source_key # 返回原始键
            visited.add(current_key)
            current_key = key_remap[current_key]
        return current_key

    def rebuild_relationships(self, all_elements_map: Dict, elements_with_keys: Dict, key_remap: Dict):
        """
        主方法，按顺序协调所有类型的关系重建。
        """
        print("\n开始重建节点之间的关系...")
        
        # 准备一个从原始ID到最终目标规范键的完整映射
        id_to_final_key_map = {}
        for original_id, canonical_key in elements_with_keys.items():
            id_to_final_key_map[original_id] = self._get_target_key(canonical_key, key_remap)

        # 按顺序调用每种关系类型的重建方法
        self._rebuild_single_ref_relationships(all_elements_map, id_to_final_key_map)
        self._rebuild_list_ref_relationships(all_elements_map, id_to_final_key_map)
        self._rebuild_connector_relationships(all_elements_map, id_to_final_key_map)
        self._rebuild_nested_behavior_relationships(all_elements_map, id_to_final_key_map) # <--- 新增调用
        
        print("✅ 所有关系重建完毕。")

    def _rebuild_single_ref_relationships(self, all_elements_map, id_to_final_key_map):
        """Rebuilds relationships based on SINGLE_REF_RULES."""
        print("  - 正在重建单一引用关系...")
        total_count = 0
        for field, rel_type, direction in SINGLE_REF_RULES:
            count = 0
            query = self._build_merge_query('source', rel_type, 'target', direction)
            
            for original_id, element_data in all_elements_map.items():
                if field in element_data and element_data[field]:
                    source_key = id_to_final_key_map.get(original_id)
                    target_key = id_to_final_key_map.get(element_data[field])
                    if source_key and target_key and source_key != target_key:
                        self._execute_write(query, {'source_key': source_key, 'target_key': target_key})
                        count += 1
            if count > 0:
                print(f"    创建了 {count} 条 '{rel_type}' 关系。")
                total_count += count

    def _rebuild_list_ref_relationships(self, all_elements_map, id_to_final_key_map):
        """Rebuilds relationships based on LIST_REF_RULES."""
        print("  - 正在重建列表引用关系...")
        total_count = 0
        for field, rel_type, direction in LIST_REF_RULES:
            count = 0
            query = self._build_merge_query('source', rel_type, 'target', direction)

            for original_id, element_data in all_elements_map.items():
                if field in element_data and isinstance(element_data[field], list):
                    source_key = id_to_final_key_map.get(original_id)
                    for target_id in element_data[field]:
                        target_key = id_to_final_key_map.get(target_id)
                        if source_key and target_key and source_key != target_key:
                            self._execute_write(query, {'source_key': source_key, 'target_key': target_key})
                            count += 1
            if count > 0:
                print(f"    创建了 {count} 条 '{rel_type}' 关系。")
                total_count += count
    
    def _rebuild_connector_relationships(self, all_elements_map, id_to_final_key_map):
        """Handles special logic for complex connectors (Binding, Assembly)."""
        print("  - 正在重建复杂的连接器关系...")
        count = 0
        
        # This query connects a connector to its endpoint references
        # and stores metadata (like 'end1', 'partRef') on the relationship
        query = """
        MATCH (connector {canonicalKey: $connector_key})
        MATCH (target {canonicalKey: $target_key})
        MERGE (connector)-[r:CONNECTS_TO]->(target)
        ON CREATE SET r = $props
        ON MATCH SET r += $props
        """

        for original_id, element_data in all_elements_map.items():
            if element_data.get('type') in ["AssemblyConnector", "BindingConnector"]:
                connector_key = id_to_final_key_map.get(original_id)
                if not connector_key: continue

                for end_name in COMPLEX_REF_RULES: # e.g., 'end1', 'end2'
                    if end_name in element_data:
                        end_data = element_data[end_name]
                        
                        # The properties to store on the relationship
                        rel_props = {'end': end_name}

                        # 动态遍历所有已知的引用字段类型
                        for ref_type in CONNECTOR_END_REF_FIELDS:
                            if ref_type in end_data:
                                ref_id = end_data[ref_type]
                                target_key = id_to_final_key_map.get(ref_id)
                                if target_key:
                                    # 存储原始引用类型和ID作为关系属性
                                    current_rel_props = rel_props.copy()
                                    current_rel_props['ref_type'] = ref_type
                                    current_rel_props['original_ref_id'] = ref_id

                                    self._execute_write(query, {
                                        'connector_key': connector_key,
                                        'target_key': target_key,
                                        'props': current_rel_props
                                    })
                                    count += 1
        if count > 0:
            print(f"    创建了 {count} 条 'CONNECTS_TO' 关系。")


    def _rebuild_nested_behavior_relationships(self, all_elements_map: Dict, id_to_final_key_map: Dict):
        """
        处理嵌套的行为调用关系 (entry, exit, doActivity, effect)。
        这是专门为状态机中的行为调用设计的。
        """
        print("  - 正在重建嵌套的行为调用关系...")
        total_count = 0
        
        for field, rel_type in NESTED_BEHAVIOR_RULES:
            count = 0
            # 行为调用关系总是 'out' 方向
            query = self._build_merge_query('source', rel_type, 'target', 'out')

            for original_id, element_data in all_elements_map.items():
                # 检查元素是否包含此字段，并且字段值是一个字典
                if field in element_data and isinstance(element_data[field], dict):
                    behavior_obj = element_data[field]
                    
                    # 检查字典内部是否含有 'calledBehaviorId'
                    if 'calledBehaviorId' in behavior_obj:
                        source_key = id_to_final_key_map.get(original_id)
                        target_id = behavior_obj['calledBehaviorId']
                        target_key = id_to_final_key_map.get(target_id)
                        
                        if source_key and target_key:
                            self._execute_write(query, {'source_key': source_key, 'target_key': target_key})
                            count += 1
            if count > 0:
                print(f"    创建了 {count} 条 '{rel_type}' 关系。")
                total_count += count


    def _build_merge_query(self, source_alias, rel_type, target_alias, direction):
        """Helper to build Cypher MERGE queries for relationships."""
        if direction == 'out':
            return f"""
            MATCH ({source_alias} {{canonicalKey: $source_key}})
            MATCH ({target_alias} {{canonicalKey: $target_key}})
            MERGE ({source_alias})-[:{rel_type}]->({target_alias})
            """
        else: # 'in'
            return f"""
            MATCH ({source_alias} {{canonicalKey: $source_key}})
            MATCH ({target_alias} {{canonicalKey: $target_key}})
            MERGE ({source_alias})<-[:{rel_type}]-({target_alias})
            """
            
    def _execute_write(self, query: str, parameters: Dict[str, Any] = None):
        """执行一个写事务的辅助函数。"""
        if not self.driver:
            return
        
        with self.driver.session() as session:
            session.write_transaction(lambda tx: tx.run(query, parameters))

    def setup_constraints(self, all_elements: list): # <-- 接受所有元素作为参数
        """
        根据加载的所有元素，动态地为所有出现的节点类型设置唯一性约束。
        """
        print("正在为节点标签设置唯一性约束...")
        
        # 1. 动态地从数据中提取所有唯一的元素类型
        known_types = set(elem['type'] for elem in all_elements if 'type' in elem)
        print(f"  - 在数据中发现了 {len(known_types)} 种唯一的元素类型。")
        
        # 2. 为每种类型创建约束
        for elem_type in known_types:
            # 确保类型名符合Cypher标签的规范（例如，不含空格或特殊字符）
            # 在我们的例子中，类型名都是合规的，但这是一个好的实践
            if elem_type.isalnum():
                query = f"CREATE CONSTRAINT IF NOT EXISTS FOR (n:`{elem_type}`) REQUIRE n.canonicalKey IS UNIQUE"
                self._execute_write(query)
            else:
                print(f"  - 警告: 类型名 '{elem_type}' 不合规，跳过为其创建约束。")
            
        print("✅ 唯一性约束设置完毕。")

    def fuse_element(self, element: Dict[str, Any], canonical_key: str):
        """
        将单个元素融合到Neo4j中。

        Args:
            element (Dict[str, Any]): 要融合的元素对象。
            canonical_key (str): 该元素的规范键。
        """
        if not element or not canonical_key:
            return

        elem_type = element.get('type', 'UnknownType')
        
        # 准备属性字典，我们将把元素的所有非结构性信息作为属性存储
        # 我们需要处理复杂数据类型（如列表、字典），将其转换为Neo4j兼容的格式
        properties_to_set = {
            'original_id': element['id'],
            'canonicalKey': canonical_key,
            'name': element.get('name')
        }
        
        # 遍历元素的所有key，将可以被JSON序列化的值作为属性
        for key, value in element.items():
            if key not in ['id', 'type', 'name']: # 已经被特殊处理的键
                if isinstance(value, (str, int, float, bool)):
                    properties_to_set[key] = value
                # 将列表和字典转换为JSON字符串存储，这是一个简单而强大的方法
                elif isinstance(value, (list, dict)):
                    import json
                    properties_to_set[key] = json.dumps(value, ensure_ascii=False)

        # 构建 MERGE 查询
        # 1. MERGE 基于元素的类型和规范键来匹配或创建节点
        # 2. ON CREATE 设置所有初始属性
        # 3. ON MATCH 更新属性（对于结构化融合，覆盖通常是可接受的策略）
        query = f"""
        MERGE (n:{elem_type} {{canonicalKey: $canonicalKey}})
        ON CREATE SET n = $props
        ON MATCH SET n += $props
        """
        
        try:
            self._execute_write(query, {'canonicalKey': canonical_key, 'props': properties_to_set})
        except Exception as e:
            print(f"❌ 融合元素失败: {canonical_key}")
            print(f"   错误: {e}")
            print(f"   元素数据: {element}")
    
    def fuse_elements_batch(self, elements_with_keys: Dict[str, str], all_elements_map: Dict[str, Dict]):
        """
        批量融合所有元素。
        
        Args:
            elements_with_keys (Dict[str, str]): 原始ID到规范键的映射.
            all_elements_map (Dict[str, Dict]): 原始ID到完整元素对象的映射.
        """
        print(f"\n开始批量融合 {len(elements_with_keys)} 个元素到 Neo4j...")
        
        count = 0
        total = len(elements_with_keys)
        for original_id, canonical_key in elements_with_keys.items():
            element_data = all_elements_map.get(original_id)
            if element_data:
                self.fuse_element(element_data, canonical_key)
                count += 1
                # 简单的进度条
                print(f"\r  进度: {count}/{total} ({count/total:.1%})", end="")

        print("\n✅ 所有元素节点融合完毕。")

    def close(self):
        """关闭与Neo4j的连接。"""
        if self.driver:
            self.driver.close()


    def unify_models(self, master_model_original_id: str, master_model_name: str):
        """
        统一模型流程（不使用 APOC）
        步骤:
        1) 创建或确认 master（基于 original_id）
        2) 收集所有需要挂载的 Package
        3) 将这些 Package 挂载到 master
        4) 删除所有旧 Model（保留 master）
        5) 校验
        """
        print("  - 正在执行模型统一操作（无 APOC 版本）...")

        params = {
            "master_original_id": master_model_original_id,
            "master_name": master_model_name
        }

        try:
            with self.driver.session() as session:

                # Step 1: 创建/确认 master
                q_create_master = """
                MERGE (master:Model {original_id: $master_original_id})
                ON CREATE SET master.name = $master_name
                """
                session.write_transaction(lambda tx: tx.run(q_create_master, params))
                print("    [1/6] ✅ 主模型节点已确认存在（基于原业务 ID）")

                # Step 2: 收集旧模型关联的 package
                q_old_pkgs = """
                MATCH (p:Package)-[:IS_CHILD_OF]->(oldM:Model)
                WHERE oldM.original_id <> $master_original_id
                RETURN DISTINCT p.original_id AS pid
                """
                old_pkg_ids = [r["pid"] for r in session.run(q_old_pkgs, params).data()]

                # 收集顶层孤立 package（未挂到任何 Model 或 Package）
                q_top_pkgs = """
                MATCH (p:Package)
                WHERE NOT (p)-[:IS_CHILD_OF]->(:Package)
                AND NOT (p)-[:IS_CHILD_OF]->(:Model)
                RETURN DISTINCT p.original_id AS pid
                """
                top_pkg_ids = [r["pid"] for r in session.run(q_top_pkgs).data()]

                pkg_ids = list(set(old_pkg_ids + top_pkg_ids))
                print(f"    [2/6] ✅ 识别需挂载的顶层 Package 数量: {len(pkg_ids)}")

                # Step 3: 执行挂载
                if pkg_ids:
                    q_attach = """
                    UNWIND $pkg_ids AS pid
                    MATCH (p:Package {original_id: pid})
                    MATCH (master:Model {original_id: $master_original_id})
                    MERGE (p)-[:IS_CHILD_OF]->(master)
                    """
                    session.write_transaction(lambda tx: tx.run(q_attach, {"pkg_ids": pkg_ids, **params}))
                    print(f"    [3/6] ✅ 已挂载 {len(pkg_ids)} 个 Package 到主模型")
                else:
                    print("    [3/6] ⚠ 无需挂载")

                # Step 4: 删除旧 Model
                q_del_old_models = """
                MATCH (m:Model)
                WHERE m.original_id <> $master_original_id
                DETACH DELETE m
                """
                session.write_transaction(lambda tx: tx.run(q_del_old_models, params))
                print("    [4/6] ✅ 旧 Model 节点已完成清理")

                # Step 5: 校验（打印信息）
                q_count_models = "MATCH (m:Model) RETURN count(m) AS cnt"
                total_models = session.run(q_count_models).single().value()
                print(f"    [5/6] ✅ 当前 Model 节点剩余数量: {total_models}")

                print("  ✅ 模型统一完成，无错误\n")

        except Exception as exc:
            print(f"  ❌ 模型统一失败: {exc}")
            raise
