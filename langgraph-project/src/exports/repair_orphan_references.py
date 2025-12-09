# repair_orphan_references.py
"""
该模块用于修复 SysML JSON 模型中的孤立引用问题。
使用多轮次渐进式修复策略：

修复策略（按优先级）：
1. 规则修复：相似ID匹配、名称关键词匹配、创建缺失父级
2. 大模型修复：对规则无法修复的，使用LLM进行语义推断
3. 多轮迭代：重复上述过程直到收敛或达到最大轮次
4. 保底删除：对最终无法修复的节点，执行级联删除
"""

import json
import os
import sys
import re
from datetime import datetime
from typing import Dict, List, Set, Any, Optional, Tuple
from copy import deepcopy
from collections import defaultdict

# 确保可以导入项目模块
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from config.settings import settings


def get_llm_client():
    """获取项目配置的 LLM 客户端"""
    try:
        from langchain_openai import ChatOpenAI
        
        llm = ChatOpenAI(
            model=settings.llm_model,
            openai_api_key=settings.openai_api_key,
            openai_api_base=settings.base_url,
            max_tokens=4096,
            temperature=0
        )
        return llm
    except Exception as e:
        print(f"警告: 初始化LLM客户端失败: {e}")
        return None


class OrphanReferenceRepairer:
    """
    修复 JSON 模型中的孤立引用问题。
    
    多轮次渐进式修复策略：
    1. 规则修复轮次
    2. LLM修复轮次（可选）
    3. 保底删除轮次
    """
    
    # ID引用字段及其期望的目标类型
    REFERENCE_FIELD_TYPES = {
        'parentId': ['Package', 'Block', 'Class', 'Activity', 'Interaction', 'StateMachine', 'Region', 'Model', 'ConstraintBlock'],
        'sourceId': ['State', 'Pseudostate', 'InitialNode', 'ActivityFinalNode', 'FlowFinalNode', 'DecisionNode', 'MergeNode', 'ForkNode', 'JoinNode', 'CallBehaviorAction', 'Actor', 'UseCase', 'Block', 'Class', 'Lifeline'],
        'targetId': ['State', 'Pseudostate', 'InitialNode', 'ActivityFinalNode', 'FlowFinalNode', 'DecisionNode', 'MergeNode', 'ForkNode', 'JoinNode', 'CallBehaviorAction', 'Actor', 'UseCase', 'Block', 'Class', 'Requirement', 'Lifeline'],
        'sendEventId': ['MessageOccurrenceSpecification'],
        'receiveEventId': ['MessageOccurrenceSpecification'],
        'messageId': ['Message'],
        'representsId': ['Block', 'Class', 'Actor', 'Property'],
        'coveredId': ['Lifeline'],
        'classifierBehaviorId': ['StateMachine', 'Activity'],
        'blockId': ['Block', 'Class'],
        'requirementId': ['Requirement'],
        'testCaseId': ['TestCase'],
        'derivedRequirementId': ['Requirement'],
        'sourceRequirementId': ['Requirement'],
        'signalId': ['Signal'],
        'behavior': ['Activity'],
        'typeId': ['Block', 'Class', 'Signal', 'DataType', 'ValueType', 'Interface', 'Activity', 'StateMachine', 'Actor', 'UseCase'],
    }
    
    # 列表类型的ID引用字段
    LIST_ID_FIELDS = ['nodes', 'edges', 'groups', 'nodeIds', 'memberEndIds', 'navigableOwnedEndIds', 'triggerIds', 'coveredLifelineIds']
    
    # 嵌套对象中的ID引用字段
    NESTED_ID_FIELDS = {
        'end1': ['propertyRefId', 'partRefId', 'portRefId'],
        'end2': ['propertyRefId', 'partRefId', 'portRefId'],
    }
    
    # 元素类型的默认父级类型
    DEFAULT_PARENT_TYPES = {
        'Block': 'Package',
        'Class': 'Package',
        'Package': 'Package',
        'Requirement': 'Package',
        'TestCase': 'Package',
        'Activity': 'Package',
        'Interaction': 'Package',
        'StateMachine': 'Block',
        'UseCase': 'Package',
        'Actor': 'Package',
        'Satisfy': 'Package',
        'Verify': 'Package',
        'DeriveReqt': 'Package',
    }
    
    def __init__(self, 
                 verbose: bool = True, 
                 use_llm: bool = False, 
                 llm_client=None,
                 max_iterations: int = 5,
                 enable_cascade_delete: bool = True,
                 cascade_mode: bool = False):
        """
        初始化修复器。
        
        Args:
            verbose: 是否输出详细日志
            use_llm: 是否使用大模型进行智能修复
            llm_client: 大模型客户端
            max_iterations: 最大修复迭代次数
            enable_cascade_delete: 是否启用保底删除
            cascade_mode: 删除时是否级联删除引用该元素的其他元素（默认False，只删除直接问题节点）
        """
        self.verbose = verbose
        self.use_llm = use_llm
        self.llm_client = llm_client
        self.max_iterations = max_iterations
        self.enable_cascade_delete = enable_cascade_delete
        self.cascade_mode = cascade_mode
        
        self.all_ids: Set[str] = set()
        self.elements_by_id: Dict[str, Dict] = {}
        self.elements_by_type: Dict[str, List[Dict]] = defaultdict(list)
        self.elements_by_name: Dict[str, List[Dict]] = defaultdict(list)
        
        # 统计信息
        self.repair_log: List[Dict] = []
        self.created_elements: List[Dict] = []
        self.deleted_elements: List[Dict] = []
        self.iteration_stats: List[Dict] = []
        
    def _log(self, message: str):
        """打印日志"""
        if self.verbose:
            print(message)
    
    def _collect_all_elements(self, data: Dict[str, Any]):
        """收集所有元素信息用于匹配"""
        self.all_ids.clear()
        self.elements_by_id.clear()
        self.elements_by_type.clear()
        self.elements_by_name.clear()
        
        # 从 model 部分收集
        for item in data.get('model', []):
            if 'id' in item:
                self.all_ids.add(item['id'])
                self.elements_by_id[item['id']] = item
                self.elements_by_type[item.get('type', 'Unknown')].append(item)
                if 'name' in item:
                    self.elements_by_name[item['name']].append(item)
        
        # 从 elements 部分收集
        for item in data.get('elements', []):
            if 'id' in item:
                self.all_ids.add(item['id'])
                self.elements_by_id[item['id']] = item
                self.elements_by_type[item.get('type', 'Unknown')].append(item)
                if 'name' in item:
                    self.elements_by_name[item['name']].append(item)
    
    def _get_broken_references(self, element: Dict) -> List[Tuple[str, str]]:
        """
        获取元素中所有无效的引用。
        
        Returns:
            [(字段名, 无效引用ID), ...]
        """
        broken = []
        
        # 检查单值引用字段
        for field in self.REFERENCE_FIELD_TYPES.keys():
            if field in element:
                ref_id = element[field]
                if ref_id and ref_id not in self.all_ids:
                    broken.append((field, ref_id))
        
        # 检查列表引用字段
        for field in self.LIST_ID_FIELDS:
            if field in element and isinstance(element[field], list):
                for ref_id in element[field]:
                    if ref_id and ref_id not in self.all_ids:
                        broken.append((field, ref_id))
        
        # 检查嵌套对象引用
        for nested_field, ref_fields in self.NESTED_ID_FIELDS.items():
            if nested_field in element and isinstance(element[nested_field], dict):
                nested_obj = element[nested_field]
                for ref_field in ref_fields:
                    if ref_field in nested_obj:
                        ref_id = nested_obj[ref_field]
                        if ref_id and ref_id not in self.all_ids:
                            broken.append((f"{nested_field}.{ref_field}", ref_id))
        
        return broken
    
    def _find_similar_id(self, broken_id: str, expected_types: List[str] = None) -> Optional[str]:
        """通过相似性匹配找到可能正确的ID"""
        if not broken_id:
            return None
        
        broken_lower = broken_id.lower().replace('-', '').replace('_', '')
        
        best_match = None
        best_score = 0
        
        for existing_id, elem in self.elements_by_id.items():
            if expected_types and elem.get('type') not in expected_types:
                continue
            
            existing_lower = existing_id.lower().replace('-', '').replace('_', '')
            
            score = 0
            if broken_lower in existing_lower or existing_lower in broken_lower:
                score = len(min(broken_lower, existing_lower, key=len)) / len(max(broken_lower, existing_lower, key=len))
            
            common_prefix = os.path.commonprefix([broken_lower, existing_lower])
            if len(common_prefix) > 5:
                score = max(score, len(common_prefix) / len(broken_lower))
            
            if score > best_score and score > 0.6:
                best_score = score
                best_match = existing_id
        
        return best_match
    
    def _find_by_name_hint(self, broken_id: str, expected_types: List[str] = None) -> Optional[str]:
        """从ID中提取名称提示，尝试匹配已有元素"""
        parts = re.split(r'[-_]', broken_id.lower())
        skip_words = {'pkg', 'blk', 'act', 'req', 'tc', 'uc', 'uuid', 'id', 'node', 'edge', 'flow', 'msg', 'll', 'rel'}
        keywords = [p for p in parts if p not in skip_words and len(p) > 2]
        
        if not keywords:
            return None
        
        candidates = []
        for name, elems in self.elements_by_name.items():
            name_lower = name.lower()
            for elem in elems:
                if expected_types and elem.get('type') not in expected_types:
                    continue
                match_count = sum(1 for kw in keywords if kw in name_lower)
                if match_count > 0:
                    candidates.append((elem['id'], match_count, elem))
        
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return candidates[0][0]
        
        return None
    
    def _create_missing_parent(self, broken_parent_id: str, child_element: Dict) -> Optional[Dict]:
        """创建缺失的父级元素"""
        child_type = child_element.get('type', '')
        parent_type = self.DEFAULT_PARENT_TYPES.get(child_type, 'Package')
        
        name_parts = re.split(r'[-_]', broken_parent_id)
        name_parts = [p for p in name_parts if p.lower() not in {'pkg', 'blk', 'act', 'uuid', 'id'}]
        inferred_name = ' '.join(name_parts).title() if name_parts else f"AutoGenerated_{parent_type}"
        
        new_element = {
            'id': broken_parent_id,
            'name': inferred_name,
            'type': parent_type,
            'parentId': 'master-model',
            'description': f'[自动生成] 为 {child_element.get("name", child_element.get("id"))} 创建的父级容器',
            '_auto_generated': True
        }
        
        self._log(f"    [创建] {parent_type}: {inferred_name} (id={broken_parent_id})")
        self.created_elements.append(new_element)
        
        return new_element
    
    def _llm_repair(self, element: Dict, field: str, broken_ref: str, expected_types: List[str]) -> Optional[str]:
        """使用大模型推断正确的引用"""
        if not self.llm_client:
            return None
        
        candidates = []
        for t in expected_types:
            for elem in self.elements_by_type.get(t, [])[:20]:
                candidates.append({
                    'id': elem.get('id'),
                    'name': elem.get('name'),
                    'type': elem.get('type'),
                    'description': str(elem.get('description', ''))[:100]
                })
        
        if not candidates:
            return None
        
        prompt = f"""你是一个 SysML 模型修复专家。以下元素的 {field} 字段引用了一个不存在的ID '{broken_ref}'。

当前元素:
- ID: {element.get('id')}
- 名称: {element.get('name')}
- 类型: {element.get('type')}
- 描述: {str(element.get('description', ''))[:200]}

候选的正确引用 (类型为 {expected_types}):
{json.dumps(candidates, ensure_ascii=False, indent=2)}

请分析语义关系，选择最可能正确的引用ID。只返回一个ID，不要任何解释。如果没有合适的，返回 "NONE"。
"""
        
        try:
            from langchain_core.messages import HumanMessage
            response = self.llm_client.invoke([HumanMessage(content=prompt)])
            result = response.content.strip()
            
            if result != "NONE" and result in self.all_ids:
                return result
        except Exception as e:
            self._log(f"    [LLM错误] {e}")
        
        return None
    
    def _repair_single_reference(self, element: Dict, field: str, broken_ref: str, use_llm: bool = False) -> Tuple[Optional[str], str]:
        """
        尝试修复单个引用。
        
        Returns:
            (新引用ID或None, 修复方法描述)
        """
        expected_types = self.REFERENCE_FIELD_TYPES.get(field.split('.')[0], [])
        
        # 策略1: 相似ID匹配
        new_ref = self._find_similar_id(broken_ref, expected_types)
        if new_ref:
            return new_ref, "相似ID匹配"
        
        # 策略2: 名称关键词匹配
        new_ref = self._find_by_name_hint(broken_ref, expected_types)
        if new_ref:
            return new_ref, "名称关键词匹配"
        
        # 策略3: 创建缺失父级（仅对 parentId）
        if field == 'parentId':
            new_parent = self._create_missing_parent(broken_ref, element)
            if new_parent:
                self.all_ids.add(broken_ref)
                self.elements_by_id[broken_ref] = new_parent
                return broken_ref, "创建缺失父级"
        
        # 策略4: 使用大模型（如果启用）
        if use_llm and self.llm_client:
            new_ref = self._llm_repair(element, field, broken_ref, expected_types)
            if new_ref:
                return new_ref, "大模型推断"
        
        return None, "无法修复"
    
    def _apply_repair(self, element: Dict, field: str, new_ref: str) -> Dict:
        """应用修复到元素"""
        repaired = deepcopy(element)
        
        if '.' in field:
            # 嵌套字段
            nested_field, ref_field = field.split('.')
            if nested_field in repaired and isinstance(repaired[nested_field], dict):
                repaired[nested_field][ref_field] = new_ref
        else:
            repaired[field] = new_ref
        
        return repaired
    
    def _find_elements_referencing(self, target_id: str) -> List[str]:
        """
        找到所有引用指定ID的元素。
        用于级联删除。
        """
        referencing = []
        
        for elem_id, elem in self.elements_by_id.items():
            # 检查单值引用
            for field in self.REFERENCE_FIELD_TYPES.keys():
                if elem.get(field) == target_id:
                    referencing.append(elem_id)
                    break
            
            # 检查列表引用
            for field in self.LIST_ID_FIELDS:
                if field in elem and isinstance(elem[field], list):
                    if target_id in elem[field]:
                        referencing.append(elem_id)
                        break
            
            # 检查嵌套引用
            for nested_field, ref_fields in self.NESTED_ID_FIELDS.items():
                if nested_field in elem and isinstance(elem[nested_field], dict):
                    for ref_field in ref_fields:
                        if elem[nested_field].get(ref_field) == target_id:
                            referencing.append(elem_id)
                            break
        
        return list(set(referencing))
    
    def _cascade_delete(self, element_id: str, deleted_ids: Set[str], depth: int = 0, cascade: bool = False) -> List[Dict]:
        """
        删除元素（可选级联删除依赖）。
        
        Args:
            element_id: 要删除的元素ID
            deleted_ids: 已删除的ID集合（避免循环）
            depth: 递归深度
            cascade: 是否级联删除引用此元素的其他元素
            
        Returns:
            被删除的元素列表
        """
        if element_id in deleted_ids or element_id not in self.elements_by_id:
            return []
        
        deleted = []
        element = self.elements_by_id[element_id]
        deleted_ids.add(element_id)
        
        indent = "  " * depth
        self._log(f"{indent}  [删除] {element.get('type', '?')}: {element.get('name', element_id)}")
        deleted.append(element)
        
        # 仅在启用级联模式时，才删除引用此元素的其他元素
        if cascade:
            referencing = self._find_elements_referencing(element_id)
            for ref_id in referencing:
                deleted.extend(self._cascade_delete(ref_id, deleted_ids, depth + 1, cascade=True))
        
        return deleted
    
    def _run_repair_iteration(self, elements: List[Dict], use_llm: bool = False) -> Tuple[List[Dict], int, int]:
        """
        运行一轮修复迭代。
        
        Returns:
            (修复后的元素列表, 修复数量, 剩余问题数量)
        """
        repaired_elements = []
        repairs_made = 0
        remaining_issues = 0
        
        for element in elements:
            elem_id = element.get('id', 'unknown')
            broken_refs = self._get_broken_references(element)
            
            if not broken_refs:
                repaired_elements.append(element)
                continue
            
            current_element = deepcopy(element)
            element_repaired = False
            
            for field, broken_ref in broken_refs:
                new_ref, method = self._repair_single_reference(current_element, field, broken_ref, use_llm)
                
                if new_ref:
                    current_element = self._apply_repair(current_element, field, new_ref)
                    self._log(f"    [{element.get('type')}] {element.get('name', elem_id)}: {field} '{broken_ref}' -> '{new_ref}' ({method})")
                    
                    self.repair_log.append({
                        'element_id': elem_id,
                        'element_name': element.get('name'),
                        'element_type': element.get('type'),
                        'field': field,
                        'old_ref': broken_ref,
                        'new_ref': new_ref,
                        'method': method
                    })
                    repairs_made += 1
                    element_repaired = True
                else:
                    remaining_issues += 1
            
            repaired_elements.append(current_element)
        
        return repaired_elements, repairs_made, remaining_issues
    
    def _run_delete_iteration(self, elements: List[Dict]) -> Tuple[List[Dict], int]:
        """
        运行清理迭代，清除无法修复的引用字段（而不是删除整个元素）。
        只有当元素是关系类型且缺少必需的源/目标时才删除。
        
        Returns:
            (清理后的元素列表, 清理/删除数量)
        """
        # 重新收集元素信息
        temp_data = {'elements': elements}
        self._collect_all_elements(temp_data)
        
        # 需要删除的关系类型（缺少源或目标就无意义）
        RELATION_TYPES = {
            'ControlFlow', 'ObjectFlow', 'Transition', 'Message', 
            'Association', 'Generalization', 'Include', 'Extend',
            'Satisfy', 'Verify', 'DeriveReqt', 'BindingConnector', 'AssemblyConnector'
        }
        
        cleaned_elements = []
        deleted_count = 0
        cleared_count = 0
        
        for element in elements:
            elem_id = element.get('id', 'unknown')
            elem_type = element.get('type', '')
            broken_refs = self._get_broken_references(element)
            
            if not broken_refs:
                cleaned_elements.append(element)
                continue
            
            # 判断是否是关系类型，且缺少关键引用
            should_delete = False
            if elem_type in RELATION_TYPES:
                # 检查是否缺少关键的源/目标引用
                critical_fields = {'sourceId', 'targetId', 'sendEventId', 'receiveEventId', 
                                   'blockId', 'requirementId', 'testCaseId', 
                                   'derivedRequirementId', 'sourceRequirementId'}
                broken_field_names = {f for f, _ in broken_refs}
                if broken_field_names & critical_fields:
                    should_delete = True
            
            if should_delete:
                # 删除整个元素
                self._log(f"  [删除] {elem_type}: {element.get('name', elem_id)} - 缺少关键引用")
                self.deleted_elements.append(element)
                deleted_count += 1
            else:
                # 清除无效引用字段，保留元素
                cleaned_element = deepcopy(element)
                for field, broken_ref in broken_refs:
                    if '.' in field:
                        # 嵌套字段
                        nested_field, ref_field = field.split('.')
                        if nested_field in cleaned_element and isinstance(cleaned_element[nested_field], dict):
                            cleaned_element[nested_field][ref_field] = None
                            self._log(f"  [清除] {elem_type}: {element.get('name', elem_id)}.{field} (原值: {broken_ref})")
                    elif field in self.LIST_ID_FIELDS:
                        # 列表字段，移除无效ID
                        if field in cleaned_element and isinstance(cleaned_element[field], list):
                            cleaned_element[field] = [ref for ref in cleaned_element[field] if ref in self.all_ids]
                            self._log(f"  [清除] {elem_type}: {element.get('name', elem_id)}.{field} 中的无效引用")
                    else:
                        # 单值字段，置空
                        cleaned_element[field] = None
                        self._log(f"  [清除] {elem_type}: {element.get('name', elem_id)}.{field} (原值: {broken_ref})")
                    cleared_count += 1
                
                cleaned_elements.append(cleaned_element)
        
        total_changes = deleted_count + (1 if cleared_count > 0 else 0)
        return cleaned_elements, deleted_count
    
    def repair(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        多轮次渐进式修复 JSON 数据。
        
        修复流程：
        1. 规则修复轮次（多轮，直到收敛）
        2. LLM修复轮次（如果启用，多轮，直到收敛）
        3. 保底删除轮次（如果启用）
        
        Returns:
            修复后的数据
        """
        result = deepcopy(data)
        self.repair_log.clear()
        self.created_elements.clear()
        self.deleted_elements.clear()
        self.iteration_stats.clear()
        
        self._log("=" * 70)
        self._log("开始多轮次渐进式修复...")
        self._log("=" * 70)
        
        # 初始化
        self._collect_all_elements(data)
        self._log(f"原始模型共有 {len(self.all_ids)} 个实体\n")
        
        elements = result.get('elements', [])
        total_iterations = 0
        
        # ===== 阶段1: 规则修复轮次 =====
        self._log("【阶段1】规则修复")
        self._log("-" * 50)
        
        for i in range(self.max_iterations):
            total_iterations += 1
            self._log(f"\n  --- 规则修复第 {i+1} 轮 ---")
            
            # 更新元素索引
            temp_data = {'model': data.get('model', []), 'elements': self.created_elements + elements}
            self._collect_all_elements(temp_data)
            
            elements, repairs, remaining = self._run_repair_iteration(elements, use_llm=False)
            
            self.iteration_stats.append({
                'phase': 'rule',
                'iteration': i + 1,
                'repairs': repairs,
                'remaining_issues': remaining
            })
            
            self._log(f"  本轮修复: {repairs} 项, 剩余问题: {remaining} 项")
            
            if repairs == 0:
                self._log("  规则修复已收敛")
                break
        
        # ===== 阶段2: LLM修复轮次 =====
        if self.use_llm and self.llm_client:
            self._log("\n" + "【阶段2】大模型修复")
            self._log("-" * 50)
            
            for i in range(self.max_iterations):
                total_iterations += 1
                self._log(f"\n  --- LLM修复第 {i+1} 轮 ---")
                
                # 更新元素索引
                temp_data = {'model': data.get('model', []), 'elements': self.created_elements + elements}
                self._collect_all_elements(temp_data)
                
                elements, repairs, remaining = self._run_repair_iteration(elements, use_llm=True)
                
                self.iteration_stats.append({
                    'phase': 'llm',
                    'iteration': i + 1,
                    'repairs': repairs,
                    'remaining_issues': remaining
                })
                
                self._log(f"  本轮修复: {repairs} 项, 剩余问题: {remaining} 项")
                
                if repairs == 0:
                    self._log("  LLM修复已收敛")
                    break
        
        # ===== 阶段3: 保底删除 =====
        if self.enable_cascade_delete:
            self._log("\n" + "【阶段3】保底删除")
            self._log("-" * 50)
            
            # 更新元素索引
            temp_data = {'model': data.get('model', []), 'elements': self.created_elements + elements}
            self._collect_all_elements(temp_data)
            
            # 检查是否还有问题
            remaining_issues = sum(1 for e in elements if self._get_broken_references(e))
            
            if remaining_issues > 0:
                self._log(f"\n  仍有 {remaining_issues} 个元素存在无法修复的引用，执行级联删除...")
                elements, deleted_count = self._run_delete_iteration(elements)
                
                self.iteration_stats.append({
                    'phase': 'delete',
                    'iteration': 1,
                    'deleted': deleted_count,
                    'remaining_elements': len(elements)
                })
                
                self._log(f"  删除了 {deleted_count} 个元素（含级联）")
            else:
                self._log("  无需删除，所有引用已修复")
        
        # 合并创建的元素
        result['elements'] = self.created_elements + elements
        
        # 打印总结
        self._log("\n" + "=" * 70)
        self._log("修复完成！")
        self._log(f"  - 总迭代轮次: {total_iterations}")
        self._log(f"  - 修复操作: {len(self.repair_log)} 项")
        self._log(f"  - 创建元素: {len(self.created_elements)} 个")
        self._log(f"  - 删除元素: {len(self.deleted_elements)} 个")
        self._log(f"  - 最终元素数: {len(result['elements'])} 个")
        self._log("=" * 70)
        
        return result
    
    def get_repair_report(self) -> Dict[str, Any]:
        """获取详细修复报告"""
        return {
            'summary': {
                'total_repairs': len(self.repair_log),
                'created_elements': len(self.created_elements),
                'deleted_elements': len(self.deleted_elements),
            },
            'iteration_stats': self.iteration_stats,
            'repair_details': self.repair_log,
            'created_elements': self.created_elements,
            'deleted_elements': [
                {
                    'id': e.get('id'),
                    'name': e.get('name'),
                    'type': e.get('type')
                } for e in self.deleted_elements
            ]
        }


def repair_json_data(
    json_data: Dict[str, Any],
    verbose: bool = True,
    use_llm: bool = False,
    llm_client=None,
    max_iterations: int = 5,
    enable_cascade_delete: bool = True,
    cascade_mode: bool = False
) -> Dict[str, Any]:
    """
    修复 JSON 数据中的孤立引用。
    
    Args:
        json_data: JSON 数据字典
        verbose: 是否输出详细日志
        use_llm: 是否使用大模型
        llm_client: 大模型客户端
        max_iterations: 每个阶段的最大迭代次数
        enable_cascade_delete: 是否启用保底删除
        cascade_mode: 删除时是否级联删除（默认False，只删除直接问题节点）
        
    Returns:
        修复后的数据
    """
    repairer = OrphanReferenceRepairer(
        verbose=verbose,
        use_llm=use_llm,
        llm_client=llm_client,
        max_iterations=max_iterations,
        enable_cascade_delete=enable_cascade_delete,
        cascade_mode=cascade_mode
    )
    return repairer.repair(json_data)


def repair_json_file(
    input_path: str,
    output_path: Optional[str] = None,
    verbose: bool = True,
    use_llm: bool = False,
    llm_client=None,
    max_iterations: int = 5,
    enable_cascade_delete: bool = True,
    cascade_mode: bool = False,
    save_report: bool = True
) -> Dict[str, Any]:
    """修复 JSON 文件中的孤立引用"""
    print(f"读取文件: {input_path}")
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    repairer = OrphanReferenceRepairer(
        verbose=verbose,
        use_llm=use_llm,
        llm_client=llm_client,
        max_iterations=max_iterations,
        enable_cascade_delete=enable_cascade_delete,
        cascade_mode=cascade_mode
    )
    repaired_data = repairer.repair(data)
    
    # 生成输出路径
    if output_path is None:
        base_dir = os.path.dirname(input_path)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(base_dir, f"{base_name}_repaired_{timestamp}.json")
    
    # 保存修复后的文件
    print(f"\n保存修复后的文件: {output_path}")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(repaired_data, f, ensure_ascii=False, indent=2)
    
    # 保存修复报告
    if save_report:
        report = repairer.get_repair_report()
        report_path = output_path.replace('.json', '_repair_report.json')
        print(f"保存修复报告: {report_path}")
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    
    return repaired_data


# 命令行接口
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='多轮次渐进式修复 SysML JSON 模型中的孤立引用')
    parser.add_argument('input', help='输入JSON文件路径')
    parser.add_argument('-o', '--output', help='输出JSON文件路径', default=None)
    parser.add_argument('--use-llm', help='使用大模型进行智能修复', action='store_true')
    parser.add_argument('--max-iterations', help='每阶段最大迭代次数（默认5）', type=int, default=5)
    parser.add_argument('--no-delete', help='禁用保底删除', action='store_true')
    parser.add_argument('--cascade', help='启用级联删除（删除问题节点时同时删除引用它的元素）', action='store_true')
    parser.add_argument('-q', '--quiet', help='安静模式', action='store_true')
    parser.add_argument('--no-report', help='不保存修复报告', action='store_true')
    
    args = parser.parse_args()
    
    # 初始化LLM客户端
    llm_client = None
    if args.use_llm:
        llm_client = get_llm_client()
        if llm_client:
            print(f"已启用大模型辅助修复 (模型: {settings.llm_model})")
        else:
            print("警告: 无法初始化大模型客户端，将仅使用规则修复")
    
    repair_json_file(
        args.input,
        output_path=args.output,
        verbose=not args.quiet,
        use_llm=args.use_llm,
        llm_client=llm_client,
        max_iterations=args.max_iterations,
        enable_cascade_delete=not args.no_delete,
        cascade_mode=args.cascade,
        save_report=not args.no_report
    )
