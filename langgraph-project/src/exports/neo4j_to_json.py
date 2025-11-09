# reverse_to_json.py

import json
import sys
import os

# 确保可以导入项目模块
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__))))

from connections.database_connectors import get_neo4j_driver, close_connections

class JsonReverser:
    """
    从一个已经过统一处理的Neo4j数据库中逆向工程，生成一个融合后的JSON文件。
    该类假设数据库中只有一个Model节点，且所有顶层包都已正确链接。
    """
    def __init__(self):
        self.driver = get_neo4j_driver()
        if not self.driver:
            raise ConnectionError("无法连接到Neo4j。")
        print("JsonReverser (简化版) 初始化成功。")

    def _fetch_all_elements_from_neo4j(self):
        """
        升级版查询：获取所有节点、其父ID，以及它的第一个标签。
        """
        print("  - 正在从Neo4j获取所有模型元素 (包含标签信息)...")
        
        query = """
        MATCH (n)
        OPTIONAL MATCH (n)-[:IS_CHILD_OF]->(p)
        // labels(n)[0] 获取节点的第一个标签，对于我们的模型来说足够了
        RETURN properties(n) AS props, p.original_id AS parent_id, labels(n)[0] AS label
        """
        with self.driver.session() as session:
            result = session.run(query)
            return [record.data() for record in result]
            

    def _deserialize_and_clean(self, element_props: dict, parent_id: str) -> dict:
        """
        对单个元素进行反序列化和清理，确保其格式与输入一致。
        """
        processed_element = {}
        
        # 1. 反序列化可能存在的JSON字符串属性
        for key, value in element_props.items():
            if isinstance(value, str):
                try:
                    processed_element[key] = json.loads(value)
                except json.JSONDecodeError:
                    processed_element[key] = value
            else:
                processed_element[key] = value

        # 2. 恢复原始ID，并移除内部使用的属性
        processed_element['id'] = processed_element.pop('original_id', 'missing_original_id')
        processed_element.pop('canonicalKey', None)
        
        # 3. 设置正确的 parentId
        if parent_id:
            processed_element['parentId'] = parent_id
            
        return processed_element


    def reconstruct_json(self) -> dict:
        """
        执行完整的逆向工程流程，使用更健壮的判断逻辑。
        """
        all_data = self._fetch_all_elements_from_neo4j()
        
        print(f"  - 从数据库中获取了 {len(all_data)} 个节点。")
        print("  - 正在分离模型根并重建元素列表...")

        final_model = None
        final_elements = []

        for record in all_data:
            props = record['props']
            parent_id = record['parent_id']
            label = record['label'] # <-- 获取节点的标签
            
            clean_element = self._deserialize_and_clean(props, parent_id)

            # --- 核心修正：更健壮的判断逻辑 ---
            # 我们现在同时检查 属性type 和 节点的标签label
            # 这样无论哪种方式存储了类型信息，都能被正确识别
            if clean_element.get('type') == 'Model' or label == 'Model':
                
                # 如果'type'属性不存在，我们从标签补上，确保JSON的完整性
                if 'type' not in clean_element:
                    clean_element['type'] = 'Model'

                clean_element.pop('parentId', None)
                final_model = clean_element
            else:
                final_elements.append(clean_element)

        if not final_model:
            raise ValueError("错误：在数据库中未能找到唯一的Model根节点。请先运行模型统一流程。")
            
        # 4. 组装成最终的、与输入格式一致的JSON
        final_json = {
            "model": [final_model], # 保持与输入一致的列表格式
            "elements": final_elements
        }
        
        print("✅ JSON重建成功，格式与输入保持一致！")
        return final_json

if __name__ == "__main__":
    try:
        reverser = JsonReverser()
        fused_json_data = reverser.reconstruct_json()
        
        output_filename = "fused_model_output_final.json"
        with open(output_filename, 'w', encoding='utf-8') as f:
            json.dump(fused_json_data, f, ensure_ascii=False, indent=2)
            
        print(f"\n融合后的JSON已成功保存到文件: {output_filename}")

    except Exception as e:
        print(f"\n❌ 发生错误: {e}")
    finally:
        close_connections()