import json
from typing import Dict, List, Any, Optional

class CanonicalKeyGenerator:
    """
    为SysML模型元素生成一个稳定且唯一的规范键（指纹）。
    这个键基于元素在模型层次结构中的路径和名称，而不是其易变的UUID。
    """
    def __init__(self, all_elements: List[Dict[str, Any]]):
        """
        初始化生成器。

        Args:
            all_elements: 从所有JSON文件中合并而来的元素列表。
        """
        # 创建一个从id到元素对象的快速查找映射
        self.elements_map: Dict[str, Dict[str, Any]] = {
            elem['id']: elem for elem in all_elements
        }
        # 缓存已经计算过的键，以提高性能
        self.key_cache: Dict[str, str] = {}
        print(f"成功加载并索引了 {len(all_elements)} 个元素。")

    def _get_path_list(self, element_id: str) -> List[str]:
        """
        递归地获取从根到指定元素的名称路径列表。

        Args:
            element_id: 当前要计算路径的元素ID。

        Returns:
            一个包含路径上所有元素名称的列表，例如 ['FanSystemModel', 'FanSystemPackage', 'Fan']。
        """
        # 如果当前元素不在映射中，说明数据有问题，返回空路径
        if element_id not in self.elements_map:
            return []

        current_element = self.elements_map[element_id]
        
        # Base Case: 如果元素没有parentId，它就是路径的起点（通常是model或package）
        parent_id = current_element.get('parentId')
        if not parent_id:
            # 注意：某些元素可能没有'name'，这里做一个健壮性处理
            return [current_element.get('name', current_element['id'])]

        # 递归调用以获取父路径
        parent_path = self._get_path_list(parent_id)
        
        # 将当前元素的名称追加到父路径后
        current_name = current_element.get('name', current_element['id'])
        return parent_path + [current_name]

    def get_canonical_key(self, element_id: str) -> Optional[str]:
        """
        计算并返回指定元素的完整规范键。

        Args:
            element_id: 需要计算键的元素ID。

        Returns:
            格式化的规范键字符串，例如 "Block::FanSystemModel.FanSystemPackage.Fan"，
            如果元素未找到则返回 None。
        """
        # 检查缓存
        if element_id in self.key_cache:
            return self.key_cache[element_id]
            
        if element_id not in self.elements_map:
            return None

        element = self.elements_map[element_id]
        element_type = element.get('type', 'UnknownType')

        # 生成路径并用 '.' 连接
        path_list = self._get_path_list(element_id)
        path_str = ".".join(path_list)
        
        # 格式化最终的键
        canonical_key = f"{element_type}::{path_str}"
        
        # 存入缓存
        self.key_cache[element_id] = canonical_key
        return canonical_key

    def generate_all_keys(self) -> Dict[str, str]:
        """
        为所有已加载的元素生成规范键。

        Returns:
            一个字典，key是原始的元素id，value是计算出的规范键。
        """
        print("\n开始为所有元素生成规范键...")
        all_keys = {
            elem_id: self.get_canonical_key(elem_id)
            for elem_id in self.elements_map.keys()
        }
        print("所有规范键生成完毕。")
        return all_keys

def load_json_files(*file_paths: str) -> List[Dict[str, Any]]:
    """从多个JSON文件中加载并合并所有'elements'。"""
    all_elements = []
    
    # 将顶层的 'model' 对象也视为一个可处理的元素
    # 这对于建立完整的父子关系至关重要
    def treat_model_as_element(model_data: Dict[str, Any], file_path: str) -> Dict[str, Any]:
        if 'id' not in model_data:
            # 如果model没有id，我们根据文件名给它一个临时的、唯一的id
            model_data['id'] = f"model-from-{file_path.split('.')[0]}"
        if 'type' not in model_data:
            model_data['type'] = 'Model' # 赋予一个默认类型
        return model_data

    for path in file_paths:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
            # 统一处理 'model' 字段，它可能是对象也可能是列表
            model_info = data.get('model')
            if isinstance(model_info, list) and model_info:
                all_elements.append(treat_model_as_element(model_info[0], path))
            elif isinstance(model_info, dict):
                 all_elements.append(treat_model_as_element(model_info, path))

            if 'elements' in data and data['elements']:
                all_elements.extend(data['elements'])
    return all_elements


if __name__ == "__main__":
    # --- 准备工作 ---
    # 1. 请将您提供的三个JSON示例保存为以下文件名，或修改文件名以匹配您的文件。
    #    确保这些文件与此Python脚本在同一个目录下。
    bdd_ibd_file = 'example_bdd_ibd.json'
    activity_file = 'example_activity.json'
    parametric_file = 'example_parametric.json'

    # --- 执行流程 ---
    try:
        # 1. 从所有文件中加载并合并元素
        print("正在加载JSON文件...")
        master_element_list = load_json_files(bdd_ibd_file, activity_file, parametric_file)

        # 2. 初始化规范键生成器
        key_generator = CanonicalKeyGenerator(master_element_list)

        # 3. 为所有元素生成键
        generated_keys = key_generator.generate_all_keys()

        # --- 结果展示 ---
        print("\n--- 规范键生成结果示例 ---")
        
        # 随机选择几个ID进行展示
        sample_ids = [
            # BDD/IBD中的例子
            "blk-fan-uuid", 
            "prop-fan-motor",
            "port-fan-powerin",
            # 活动图中的例子
            "act-main-review-uuid",
            "node-dr-prepare",
            "edge-dr-cf1",
            # 参数图中的例子
            "block1",
            "constraintBlock1",
            "conn1"
        ]

        for element_id in sample_ids:
            if element_id in generated_keys:
                print(f"  原始ID: {element_id:<25} -> 规范键: {generated_keys[element_id]}")
            else:
                 print(f"  警告: 示例ID '{element_id}' 在加载的元素中未找到。")
                 
    except FileNotFoundError as e:
        print(f"\n错误: 找不到文件 '{e.filename}'。")
        print("请确保您已将JSON示例文件保存在正确的路径下，并且文件名与脚本中的设置一致。")
    except Exception as e:
        print(f"\n发生了一个未知错误: {e}")