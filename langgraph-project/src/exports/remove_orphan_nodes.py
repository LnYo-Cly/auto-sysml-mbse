# remove_orphan_nodes.py
"""
该模块用于从SysML JSON模型文件中去除孤立节点。
孤立节点是指其引用的ID（如parentId, sourceId, targetId等）
在模型中找不到对应实体的节点。
"""

import json
import os
import sys
from datetime import datetime
from typing import Dict, List, Set, Any, Optional
from copy import deepcopy

# 确保可以导入项目模块
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))


class OrphanNodeRemover:
    """
    从JSON模型中识别并移除孤立节点的工具类。
    
    孤立节点定义：
    1. parentId 引用的实体不存在
    2. sourceId / targetId 引用的实体不存在
    3. sendEventId / receiveEventId 引用的实体不存在
    4. messageId 引用的实体不存在
    5. representsId 引用的实体不存在
    6. coveredId 引用的实体不存在
    7. typeId 引用的实体不存在（可选，因为可能是内置类型）
    8. classifierBehaviorId 引用的实体不存在
    9. 列表类型引用（如 nodes, edges, groups）中的ID不存在
    10. 嵌套对象中的引用（如 end1, end2 中的引用）
    11. 特定类型元素缺少必需的字段（如 MessageOccurrenceSpecification 缺少 parentId, coveredId）
    12. Satisfy/Verify/DeriveReqt 关系中的 blockId, requirementId, testCaseId 等引用不存在
    """
    
    # 需要检查的单值ID引用字段
    SINGLE_ID_FIELDS = [
        'parentId',
        'sourceId', 
        'targetId',
        'sendEventId',
        'receiveEventId',
        'messageId',
        'representsId',
        'coveredId',
        'classifierBehaviorId',
        # Satisfy/Verify/DeriveReqt 关系字段
        'blockId',
        'requirementId',
        'testCaseId',
        'derivedRequirementId',
        'sourceRequirementId',
        # 其他可能的引用字段
        'signalId',
        'behavior',
    ]
    
    # 可选检查的类型引用字段（可能是内置类型如 Real, Integer 等）
    OPTIONAL_TYPE_FIELDS = [
        'typeId',
    ]
    
    # 内置类型列表（不需要在模型中找到对应实体）
    BUILTIN_TYPES = {
        'Real', 'Integer', 'Boolean', 'String', 'UnlimitedNatural',
        'real', 'integer', 'boolean', 'string',
        'int', 'float', 'double', 'char', 'void',
    }
    
    # 列表类型的ID引用字段
    LIST_ID_FIELDS = [
        'nodes',
        'edges', 
        'groups',
    ]
    
    # 嵌套对象中需要检查的ID引用字段
    NESTED_ID_FIELDS = {
        'end1': ['propertyRefId', 'partRefId', 'portRefId'],
        'end2': ['propertyRefId', 'partRefId', 'portRefId'],
    }
    
    # 特定类型元素的必需字段（缺少这些字段则视为孤立节点）
    REQUIRED_FIELDS_BY_TYPE = {
        'MessageOccurrenceSpecification': ['parentId', 'coveredId'],
        'DestructionOccurrenceSpecification': ['parentId', 'coveredId'],
        'ControlFlow': ['parentId', 'sourceId', 'targetId'],
        'ObjectFlow': ['parentId', 'sourceId', 'targetId'],
        'Transition': ['parentId', 'sourceId', 'targetId'],
        'Message': ['parentId', 'sendEventId', 'receiveEventId'],
        'Lifeline': ['parentId', 'representsId'],
        'Association': ['parentId'],
        'Generalization': ['sourceId', 'targetId'],
        # 需求关系类型
        'Satisfy': ['blockId', 'requirementId'],
        'Verify': ['testCaseId', 'requirementId'],
        'DeriveReqt': ['derivedRequirementId', 'sourceRequirementId'],
    }
    
    def __init__(self, check_type_refs: bool = False, verbose: bool = True):
        """
        初始化孤立节点移除器。
        
        Args:
            check_type_refs: 是否检查typeId引用（默认False，因为可能是内置类型）
            verbose: 是否输出详细日志
        """
        self.check_type_refs = check_type_refs
        self.verbose = verbose
        self.all_ids: Set[str] = set()
        self.removed_elements: List[Dict] = []
        self.removal_reasons: Dict[str, str] = {}
        
    def _log(self, message: str):
        """打印日志信息"""
        if self.verbose:
            print(message)
    
    def _collect_all_ids(self, data: Dict[str, Any]) -> Set[str]:
        """
        收集模型中所有实体的ID。
        
        Args:
            data: JSON模型数据
            
        Returns:
            所有实体ID的集合
        """
        ids = set()
        
        # 从 model 部分收集
        if 'model' in data:
            for item in data['model']:
                if 'id' in item:
                    ids.add(item['id'])
        
        # 从 elements 部分收集
        if 'elements' in data:
            for item in data['elements']:
                if 'id' in item:
                    ids.add(item['id'])
                    
        return ids
    
    def _is_valid_reference(self, ref_id: str, field_name: str) -> bool:
        """
        检查引用ID是否有效。
        
        Args:
            ref_id: 引用的ID
            field_name: 字段名称
            
        Returns:
            引用是否有效
        """
        if not ref_id:
            return True  # 空引用视为有效
            
        # 如果是typeId且在内置类型列表中，视为有效
        if field_name == 'typeId' and ref_id in self.BUILTIN_TYPES:
            return True
            
        return ref_id in self.all_ids
    
    def _check_nested_refs(self, element: Dict, nested_field: str) -> Optional[str]:
        """
        检查嵌套对象中的引用是否有效。
        
        Args:
            element: 元素对象
            nested_field: 嵌套字段名（如 'end1', 'end2'）
            
        Returns:
            无效引用的字段名，如果都有效则返回None
        """
        if nested_field not in element:
            return None
            
        nested_obj = element[nested_field]
        if not isinstance(nested_obj, dict):
            return None
            
        ref_fields = self.NESTED_ID_FIELDS.get(nested_field, [])
        for ref_field in ref_fields:
            if ref_field in nested_obj:
                ref_id = nested_obj[ref_field]
                if ref_id and ref_id not in self.all_ids:
                    return f"{nested_field}.{ref_field}"
                    
        return None
    
    def _check_list_refs(self, element: Dict, list_field: str) -> Optional[str]:
        """
        检查列表类型引用中的ID是否都有效。
        
        Args:
            element: 元素对象
            list_field: 列表字段名
            
        Returns:
            第一个无效引用的ID，如果都有效则返回None
        """
        if list_field not in element:
            return None
            
        id_list = element[list_field]
        if not isinstance(id_list, list):
            return None
            
        for ref_id in id_list:
            if ref_id and ref_id not in self.all_ids:
                return ref_id
                
        return None
    
    def _check_required_fields(self, element: Dict) -> Optional[str]:
        """
        检查特定类型元素是否缺少必需的字段。
        
        Args:
            element: 元素对象
            
        Returns:
            缺失的字段名，如果都存在则返回None
        """
        element_type = element.get('type', '')
        required_fields = self.REQUIRED_FIELDS_BY_TYPE.get(element_type, [])
        
        for field in required_fields:
            if field not in element or element[field] is None or element[field] == '':
                return field
                
        return None
    
    def _is_orphan(self, element: Dict) -> tuple[bool, str]:
        """
        判断元素是否为孤立节点。
        
        Args:
            element: 要检查的元素
            
        Returns:
            (是否孤立, 原因描述)
        """
        element_id = element.get('id', 'unknown')
        element_name = element.get('name', 'unnamed')
        element_type = element.get('type', 'unknown')
        
        # 首先检查是否缺少必需字段
        missing_field = self._check_required_fields(element)
        if missing_field:
            return True, f"类型 '{element_type}' 缺少必需字段 '{missing_field}'"
        
        # 检查单值ID引用字段
        for field in self.SINGLE_ID_FIELDS:
            if field in element:
                ref_id = element[field]
                if not self._is_valid_reference(ref_id, field):
                    return True, f"{field}='{ref_id}' 引用的实体不存在"
        
        # 可选：检查类型引用
        if self.check_type_refs:
            for field in self.OPTIONAL_TYPE_FIELDS:
                if field in element:
                    ref_id = element[field]
                    if not self._is_valid_reference(ref_id, field):
                        return True, f"{field}='{ref_id}' 引用的实体不存在"
        
        # 检查列表类型引用
        for field in self.LIST_ID_FIELDS:
            invalid_ref = self._check_list_refs(element, field)
            if invalid_ref:
                return True, f"{field} 中的 '{invalid_ref}' 引用的实体不存在"
        
        # 检查嵌套对象中的引用
        for nested_field in self.NESTED_ID_FIELDS:
            invalid_ref = self._check_nested_refs(element, nested_field)
            if invalid_ref:
                return True, f"{invalid_ref} 引用的实体不存在"
        
        return False, ""
    
    def _remove_orphans_single_pass(self, elements: List[Dict]) -> tuple[List[Dict], int]:
        """
        单次遍历移除孤立节点。
        
        Args:
            elements: 元素列表
            
        Returns:
            (过滤后的元素列表, 移除的元素数量)
        """
        filtered = []
        removed_count = 0
        
        for element in elements:
            is_orphan, reason = self._is_orphan(element)
            if is_orphan:
                self.removed_elements.append(element)
                self.removal_reasons[element.get('id', 'unknown')] = reason
                removed_count += 1
                self._log(f"  [移除] {element.get('type', '?')}: {element.get('name', element.get('id', '?'))} - {reason}")
            else:
                filtered.append(element)
                
        return filtered, removed_count
    
    def remove_orphans(self, data: Dict[str, Any], max_iterations: int = 10) -> Dict[str, Any]:
        """
        迭代移除孤立节点，直到没有更多孤立节点为止。
        
        由于移除一个节点可能导致其他节点变成孤立节点（级联效应），
        需要多次迭代直到稳定。
        
        Args:
            data: JSON模型数据
            max_iterations: 最大迭代次数（防止无限循环）
            
        Returns:
            清理后的模型数据
        """
        result = deepcopy(data)
        self.removed_elements = []
        self.removal_reasons = {}
        
        self._log("=" * 60)
        self._log("开始清理孤立节点...")
        self._log("=" * 60)
        
        iteration = 0
        total_removed = 0
        
        while iteration < max_iterations:
            iteration += 1
            self._log(f"\n--- 第 {iteration} 轮迭代 ---")
            
            # 重新收集所有ID（因为上一轮可能移除了一些）
            self.all_ids = self._collect_all_ids(result)
            self._log(f"当前模型中共有 {len(self.all_ids)} 个实体")
            
            # 移除孤立节点
            if 'elements' in result:
                original_count = len(result['elements'])
                result['elements'], removed = self._remove_orphans_single_pass(result['elements'])
                total_removed += removed
                self._log(f"本轮移除 {removed} 个孤立节点 (剩余 {len(result['elements'])} 个)")
                
                if removed == 0:
                    self._log("没有发现更多孤立节点，清理完成。")
                    break
        
        self._log("\n" + "=" * 60)
        self._log(f"清理完成！共移除 {total_removed} 个孤立节点")
        self._log("=" * 60)
        
        return result
    
    def get_removal_report(self) -> Dict[str, Any]:
        """
        获取移除报告。
        
        Returns:
            包含移除详情的报告字典
        """
        report = {
            'total_removed': len(self.removed_elements),
            'removed_by_type': {},
            'removed_elements': []
        }
        
        for element in self.removed_elements:
            element_type = element.get('type', 'Unknown')
            element_id = element.get('id', 'unknown')
            
            # 按类型统计
            if element_type not in report['removed_by_type']:
                report['removed_by_type'][element_type] = 0
            report['removed_by_type'][element_type] += 1
            
            # 记录详细信息
            report['removed_elements'].append({
                'id': element_id,
                'name': element.get('name', 'unnamed'),
                'type': element_type,
                'reason': self.removal_reasons.get(element_id, 'unknown')
            })
            
        return report


def clean_json_data(
    json_data: Dict[str, Any],
    check_type_refs: bool = False,
    verbose: bool = True
) -> Dict[str, Any]:
    """
    直接清理JSON字典数据中的孤立节点。
    
    Args:
        json_data: JSON模型数据字典（如 json.load() 的结果）
        check_type_refs: 是否检查typeId引用（默认False，因为可能是内置类型）
        verbose: 是否输出详细日志
        
    Returns:
        清理后的数据字典
        
    Example:
        >>> with open('model.json', 'r', encoding='utf-8') as f:
        ...     json_data = json.load(f)
        >>> cleaned_data = clean_json_data(json_data)
    """
    original_count = len(json_data.get('elements', []))
    if verbose:
        print(f"原始元素数量: {original_count}")
    
    # 清理孤立节点
    remover = OrphanNodeRemover(check_type_refs=check_type_refs, verbose=verbose)
    cleaned_data = remover.remove_orphans(json_data)
    
    cleaned_count = len(cleaned_data.get('elements', []))
    if verbose:
        print(f"\n清理后元素数量: {cleaned_count}")
        print(f"移除元素数量: {original_count - cleaned_count}")
    
    return cleaned_data


def clean_json_file(
    input_path: str, 
    output_path: Optional[str] = None,
    check_type_refs: bool = False,
    verbose: bool = True,
    save_report: bool = True
) -> Dict[str, Any]:
    """
    清理JSON文件中的孤立节点。
    
    Args:
        input_path: 输入JSON文件路径
        output_path: 输出JSON文件路径（默认在同目录下生成带时间戳的新文件）
        check_type_refs: 是否检查typeId引用
        verbose: 是否输出详细日志
        save_report: 是否保存移除报告
        
    Returns:
        清理后的数据
    """
    # 读取输入文件
    print(f"读取文件: {input_path}")
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    original_count = len(data.get('elements', []))
    print(f"原始元素数量: {original_count}")
    
    # 清理孤立节点
    remover = OrphanNodeRemover(check_type_refs=check_type_refs, verbose=verbose)
    cleaned_data = remover.remove_orphans(data)
    
    cleaned_count = len(cleaned_data.get('elements', []))
    print(f"\n清理后元素数量: {cleaned_count}")
    print(f"移除元素数量: {original_count - cleaned_count}")
    
    # 生成输出路径
    if output_path is None:
        base_dir = os.path.dirname(input_path)
        base_name = os.path.splitext(os.path.basename(input_path))[0]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(base_dir, f"{base_name}_cleaned_{timestamp}.json")
    
    # 保存清理后的文件
    print(f"\n保存清理后的文件: {output_path}")
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, ensure_ascii=False, indent=2)
    
    # 保存移除报告
    if save_report:
        report = remover.get_removal_report()
        report_path = output_path.replace('.json', '_removal_report.json')
        print(f"保存移除报告: {report_path}")
        with open(report_path, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    
    return cleaned_data


def validate_json_file(input_path: str, check_type_refs: bool = False, verbose: bool = True) -> Dict[str, Any]:
    """
    验证JSON文件，返回孤立节点报告但不修改文件。
    
    Args:
        input_path: 输入JSON文件路径
        check_type_refs: 是否检查typeId引用
        verbose: 是否输出详细日志
        
    Returns:
        验证报告
    """
    print(f"验证文件: {input_path}")
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    remover = OrphanNodeRemover(check_type_refs=check_type_refs, verbose=verbose)
    remover.remove_orphans(data)
    
    return remover.get_removal_report()


# 命令行接口
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(
        description='从SysML JSON模型文件中移除孤立节点'
    )
    parser.add_argument(
        'input', 
        help='输入JSON文件路径'
    )
    parser.add_argument(
        '-o', '--output',
        help='输出JSON文件路径（默认自动生成）',
        default=None
    )
    parser.add_argument(
        '--check-types',
        help='是否检查typeId引用（默认不检查，因为可能是内置类型）',
        action='store_true'
    )
    parser.add_argument(
        '--validate-only',
        help='仅验证，不生成清理后的文件',
        action='store_true'
    )
    parser.add_argument(
        '-q', '--quiet',
        help='安静模式，只输出摘要信息',
        action='store_true'
    )
    parser.add_argument(
        '--no-report',
        help='不保存移除报告',
        action='store_true'
    )
    
    args = parser.parse_args()
    
    if args.validate_only:
        report = validate_json_file(
            args.input,
            check_type_refs=args.check_types,
            verbose=not args.quiet
        )
        print("\n验证报告:")
        print(f"  发现孤立节点: {report['total_removed']} 个")
        if report['removed_by_type']:
            print("  按类型统计:")
            for t, count in sorted(report['removed_by_type'].items()):
                print(f"    - {t}: {count}")
    else:
        clean_json_file(
            args.input,
            output_path=args.output,
            check_type_refs=args.check_types,
            verbose=not args.quiet,
            save_report=not args.no_report
        )
