import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
import copy
import traceback # 导入 traceback 以便更好地调试错误

# --- 定义命名空间字典，与目标XMI头完全一致 ---
NAMESPACES_DICT = {
    "xmlns:MD_Customization_for_SysML__additional_stereotypes": "http://www.magicdraw.com/spec/Customization/190/SysML",
    "xmlns:diagram": "http://www.nomagic.com/ns/magicdraw/core/diagram/1.0",
    "xmlns:sysml": "http://www.omg.org/spec/SysML/20131001",
    "xmlns:uml": "http://www.omg.org/spec/UML/20090901",
    "xmlns:xmi": "http://www.omg.org/XMI"
}

# --- 全局字典 ---
elements_by_id = {}
children_by_parent = {}
pins_by_parent_action = {}
activity_elements = {}
activity_partitions_data = [] # 存储原始 ActivityPartition 数据
blocks_data = []            # 存储原始 Block 数据
processed_elements = set()  # 跟踪已处理的元素ID


def generate_unique_id(base_id, suffix):
    """辅助函数，用于生成可能唯一的ID。"""
    clean_base = str(base_id).replace("-","")
    clean_suffix = str(suffix).replace("-","")
    return f"{clean_base}_{clean_suffix}"


def preprocess_data(json_data):
    """预处理JSON数据，填充全局字典以便查找。"""
    # 声明此函数修改的全局变量
    global elements_by_id, children_by_parent, pins_by_parent_action, activity_elements
    global activity_partitions_data, blocks_data, processed_elements

    # 重置全局状态
    elements_by_id.clear(); children_by_parent.clear(); pins_by_parent_action.clear(); activity_elements.clear()
    activity_partitions_data.clear(); blocks_data.clear(); processed_elements.clear()

    if "elements" not in json_data:
        print("错误：JSON数据必须包含 'elements' 键。")
        return False, None, None

    elements_list = json_data["elements"]
    try:
        elements_by_id = {elem["id"]: elem for elem in elements_list}
    except KeyError as e:
        print(f"错误：JSON元素缺少 'id' 字段：{e}")
        return False, None, None
    except TypeError as e:
        print(f"错误：迭代JSON元素列表时出现问题：{e}")
        return False, None, None

    model_id = "model-root-uuid"; model_name = "DefaultModelName" # 默认值
    if "model" in json_data and json_data["model"]:
        try:
            model_name = json_data["model"][0].get("name", model_name)
        except (IndexError, AttributeError):
             print("警告：无法从JSON读取模型名称。")

    root_element_ids = []
    pins_by_parent_action = {} # 确保在此处初始化

    for elem_id, elem_data in elements_by_id.items():
        parent_id = elem_data.get("parentId"); elem_type = elem_data.get("type", "Unknown")

        # 构建父子关系
        current_parent_id = parent_id if parent_id is not None else model_id
        if current_parent_id not in children_by_parent: children_by_parent[current_parent_id] = []
        children_by_parent[current_parent_id].append(elem_id)
        if parent_id is None: root_element_ids.append(elem_id)

        # 1. 分别存储活动内容
        activity_parent_id = parent_id
        if activity_parent_id and activity_parent_id in elements_by_id and elements_by_id[activity_parent_id]["type"] == "Activity":
             if activity_parent_id not in activity_elements:
                 activity_elements[activity_parent_id] = {"nodes": [], "edges": [], "groups": []}
             node_types = ["InitialNode", "ActivityFinalNode", "FlowFinalNode", "DecisionNode",
                           "MergeNode", "ForkNode", "JoinNode", "CallBehaviorAction",
                           "ActivityParameterNode", "CentralBufferNode"]
             edge_types = ["ControlFlow", "ObjectFlow"]
             if elem_type in node_types:
                 activity_elements[activity_parent_id]["nodes"].append(elem_id)
             elif elem_type in edge_types:
                 activity_elements[activity_parent_id]["edges"].append(elem_id)
             elif elem_type == "ActivityPartition":
                 activity_elements[activity_parent_id]["groups"].append(elem_id)

        # 2. 填充引脚字典
        if elem_type in ["InputPin", "OutputPin"]:
             if parent_id:
                 if parent_id not in pins_by_parent_action: pins_by_parent_action[parent_id] = []
                 pins_by_parent_action[parent_id].append(elem_id)

        # 3. 填充用于生成构造型的列表 (确保总能执行)
        if elem_type == "ActivityPartition":
            activity_partitions_data.append(elem_data) # <-- 关键修正：使用独立的 if
        elif elem_type == "Block":
            blocks_data.append(elem_data)

    return True, model_id, model_name


def create_xml_structure(parent_xml_element, elem_id):
    """递归函数，用于创建主要结构（包、块、活动）。活动内容单独处理。"""
    global processed_elements, elements_by_id, children_by_parent

    if elem_id in processed_elements or elem_id not in elements_by_id: return None

    elem_data = elements_by_id[elem_id]
    elem_type = elem_data["type"]; elem_name = elem_data.get("name")

    element_tag = 'packagedElement'
    current_attrs = {"xmi:id": elem_id}
    if elem_name: current_attrs["name"] = elem_name

    if elem_type == 'Package': current_attrs["xmi:type"] = "uml:Package"
    elif elem_type == 'Block': current_attrs["xmi:type"] = "uml:Class"
    elif elem_type == 'Activity': current_attrs["xmi:type"] = "uml:Activity"
    elif elem_type == 'Diagram': processed_elements.add(elem_id); return None
    else: processed_elements.add(elem_id); return None

    try:
        xml_elem = ET.SubElement(parent_xml_element, element_tag, attrib=current_attrs)
        processed_elements.add(elem_id)
    except Exception as e:
        print(f"错误：创建结构元素 {elem_id} ({elem_type}) 时出错：{e}")
        return None

    if elem_type == 'Activity': process_activity_content(xml_elem, elem_id)

    if elem_id in children_by_parent:
        child_ids = sorted(children_by_parent.get(elem_id, []))
        for child_id in child_ids:
            child_data = elements_by_id.get(child_id)
            if child_data and child_data.get('type') in ['Package', 'Block', 'Activity', 'Diagram']:
                 create_xml_structure(xml_elem, child_id)

    return xml_elem


def process_activity_content(activity_xml_elem, activity_id):
    """处理在活动元素内部创建节点、边和组。"""
    global elements_by_id, activity_elements, processed_elements

    if activity_id not in activity_elements: return
    content = activity_elements.get(activity_id, {})

    # --- 添加占位符元素 ---
    placeholder_elements_local = {}
    edge_ids_local = content.get("edges", [])
    group_ids_local = content.get("groups", [])
    node_ids_local = content.get("nodes", [])

    # 边相关的占位符 (weight, guard, body, language)
    for edge_id in edge_ids_local:
        edge_data = elements_by_id.get(edge_id)
        if edge_data:
             weight_id = generate_unique_id(edge_id, "weight")
             if weight_id not in elements_by_id and weight_id not in placeholder_elements_local:
                  placeholder_elements_local[weight_id] = {"id": weight_id, "type": "LiteralUnlimitedNatural", "parentId": edge_id}
             guard = edge_data.get("guard")
             if edge_data["type"] == "ControlFlow" and guard:
                  guard_id = generate_unique_id(edge_id, "guard-expr")
                  if guard_id not in elements_by_id and guard_id not in placeholder_elements_local:
                       placeholder_elements_local[guard_id] = {"id": guard_id, "type": "OpaqueExpression", "parentId": edge_id, "body": guard}
                       body_id = generate_unique_id(guard_id, "body")
                       lang_id = generate_unique_id(guard_id, "lang")
                       placeholder_elements_local[body_id] = {"id": body_id, "type": "BodyText", "parentId": guard_id}
                       placeholder_elements_local[lang_id] = {"id": lang_id, "type": "LanguageSpec", "parentId": guard_id}

    # 分区引用 (<partition>) 占位符
    for group_id in group_ids_local:
        if group_id in elements_by_id:
            part_ref_id = generate_unique_id(activity_id, group_id + "_partRef")
            if part_ref_id not in elements_by_id and part_ref_id not in placeholder_elements_local:
                placeholder_elements_local[part_ref_id] = {"id": group_id, "type": "PartitionRef", "parentId": activity_id}

    # 分区组内的节点/边引用 (<node>/<edge> in <group>) 占位符
    for group_id in group_ids_local:
            group_data = elements_by_id.get(group_id)
            if group_data:
                 for node_edge_id in group_data.get("nodeIds", []):
                     if node_edge_id in elements_by_id:
                          node_edge_data = elements_by_id.get(node_edge_id)
                          if node_edge_data: # 确保节点/边数据存在
                            ref_type_orig = node_edge_data.get("type")
                            # *** 关键修正：不过滤 Fork/Join ***
                            ref_tag_type = 'GroupNodeRef' if ref_type_orig not in ["ControlFlow", "ObjectFlow", "InputPin", "OutputPin", "Diagram", None] else 'GroupEdgeRef'
                            if ref_tag_type in ['GroupNodeRef', 'GroupEdgeRef']:
                                ref_id = generate_unique_id(group_id, node_edge_id + "_grpRef")
                                if ref_id not in elements_by_id and ref_id not in placeholder_elements_local:
                                    placeholder_elements_local[ref_id] = {"id": node_edge_id, "type": ref_tag_type, "parentId": group_id}

    # 节点内的分区引用 (<inPartition>) 占位符
    for node_id in node_ids_local:
         node_data = elements_by_id.get(node_id)
         if node_data and node_data['type'] not in ['InputPin', 'OutputPin']:
              for part_data in activity_partitions_data: # 使用全局数据
                   if part_data.get("parentId") == activity_id and node_id in part_data.get("nodeIds",[]):
                       p_id = part_data["id"]
                       if p_id in elements_by_id:
                            in_part_ref_id = generate_unique_id(node_id, "inPartRef")
                            if in_part_ref_id not in elements_by_id and in_part_ref_id not in placeholder_elements_local:
                                 placeholder_elements_local[in_part_ref_id] = {"id": p_id, "type": "InPartitionRef", "parentId": node_id}
                       break

    elements_by_id.update(placeholder_elements_local) # 在处理前更新全局字典

    # --- 按顺序创建活动内容 ---
    # 1. 创建 <partition> 引用
    for group_id in group_ids_local:
         if group_id in elements_by_id:
              part_ref_id = generate_unique_id(activity_id, group_id + "_partRef")
              if part_ref_id not in processed_elements: create_activity_element(activity_xml_elem, part_ref_id)

    # 2. 创建 <edge> 元素
    for edge_id in edge_ids_local:
        if edge_id in elements_by_id and edge_id not in processed_elements: create_activity_element(activity_xml_elem, edge_id)

    # 3. 创建 <node> 元素 (除引脚外)
    for node_id in node_ids_local:
        if node_id in elements_by_id and node_id not in processed_elements:
             if elements_by_id[node_id]['type'] not in ['InputPin', 'OutputPin']:
                 create_activity_element(activity_xml_elem, node_id)

    # 4. 创建 <group> 元素 (分区定义)
    for group_id in group_ids_local:
        if group_id in elements_by_id and group_id not in processed_elements: create_activity_element(activity_xml_elem, group_id)


def create_activity_element(parent_xml_element, elem_id):
    """为活动创建特定元素（节点、边、组、引脚等）。"""
    global processed_elements, elements_by_id, activity_partitions_data, pins_by_parent_action

    if elem_id in processed_elements or elem_id not in elements_by_id: return None

    elem_data = elements_by_id[elem_id]; elem_type = elem_data["type"]; elem_name = elem_data.get("name")

    element_tag = None
    type_to_tag_map = {
        "ControlFlow": 'edge', "ObjectFlow": 'edge', "ActivityPartition": 'group',
        "InputPin": 'argument', "OutputPin": 'result', "LiteralUnlimitedNatural": 'weight',
        "OpaqueExpression": 'guard', "BodyText": 'body', "LanguageSpec": 'language',
        "InPartitionRef": 'inPartition', "PartitionRef": 'partition',
        "GroupNodeRef": 'node', "GroupEdgeRef": 'edge',
        "InitialNode": 'node', "ActivityFinalNode": 'node', "FlowFinalNode": 'node',
        "DecisionNode": 'node', "MergeNode": 'node', "ForkNode": 'node', "JoinNode": 'node',
        "CallBehaviorAction": 'node', "ActivityParameterNode": 'node', "CentralBufferNode": 'node'
    }
    element_tag = type_to_tag_map.get(elem_type)

    if not element_tag:
        print(f"错误：无法确定活动元素 {elem_type} (ID: {elem_id}) 的标签")
        processed_elements.add(elem_id); return None

    # --- 准备属性 ---
    is_reference = elem_type in ['InPartitionRef', 'PartitionRef', 'GroupNodeRef', 'GroupEdgeRef']
    if is_reference:
        current_attrs = {"xmi:idref": elem_data["id"]} # 占位符数据中的ID是目标ID
    else:
        current_attrs = {"xmi:id": elem_id}
        if elem_name: current_attrs["name"] = elem_name
        # 添加 xmi:type
        if elem_type in ["Package", "Activity", "Class", "ControlFlow", "ObjectFlow", "InitialNode",
                          "ActivityFinalNode", "FlowFinalNode", "DecisionNode", "MergeNode", "ForkNode",
                          "JoinNode", "CallBehaviorAction", "ActivityParameterNode", "InputPin",
                          "OutputPin", "ActivityPartition", "LiteralUnlimitedNatural",
                          "OpaqueExpression", "CentralBufferNode"]:
            xmi_type_value = f"uml:{elem_type}"
            current_attrs["xmi:type"] = xmi_type_value
        # 添加 visibility
        if element_tag in ['edge', 'node', 'argument', 'result', 'group']:
             current_attrs["visibility"] = "public"
        # 添加其他特定属性
        if element_tag == 'edge' and elem_type in ["ControlFlow", "ObjectFlow"]:
            source_id=elem_data.get("sourceId"); target_id=elem_data.get("targetId")
            valid=True
            if not (source_id and source_id in elements_by_id): valid=False
            if not (target_id and target_id in elements_by_id): valid=False
            if valid: current_attrs["source"] = source_id; current_attrs["target"] = target_id
            else: processed_elements.add(elem_id); return None # 无效边不创建
        elif element_tag == 'node' and elem_type == "CallBehaviorAction":
            if "behavior" in elem_data: current_attrs["behavior"] = elem_data["behavior"]
        elif element_tag in ['argument', 'result', 'node'] and elem_type in ["InputPin", "OutputPin", "ActivityParameterNode", "CentralBufferNode"]:
            type_id = elem_data.get("typeId")
            if type_id and type_id in elements_by_id: current_attrs["type"] = type_id
        elif element_tag == 'group':
            represents_id = elem_data.get("representsId")
            if represents_id and represents_id in elements_by_id: current_attrs["represents"] = represents_id
        elif element_tag == 'weight':
            current_attrs["value"] = "1"

    # --- 创建 XML 元素 ---
    try:
        xml_elem = None
        if elem_type not in ['BodyText', 'LanguageSpec']:
             xml_elem = ET.SubElement(parent_xml_element, element_tag, attrib=current_attrs)
        elif elem_type == 'BodyText':
            xml_elem = ET.SubElement(parent_xml_element, element_tag)
            # 从父 guard 占位符获取 body 文本
            parent_guard_data = elements_by_id.get(elem_data.get('parentId'))
            if parent_guard_data: xml_elem.text = parent_guard_data.get('body', '')
        elif elem_type == 'LanguageSpec':
            xml_elem = ET.SubElement(parent_xml_element, element_tag)
            xml_elem.text = "English"

        if xml_elem is not None or elem_type in ['BodyText', 'LanguageSpec']:
             processed_elements.add(elem_id)
        else: return None
    except Exception as e:
        print(f"错误：创建活动元素 {elem_id} ({elem_type}) 标签 '{element_tag}' 时出错：{e}")
        processed_elements.add(elem_id); return None

    # --- 处理子元素 ---
    if element_tag == 'edge' and elem_type in ["ControlFlow", "ObjectFlow"]:
        weight_id = generate_unique_id(elem_id, "weight")
        create_activity_element(xml_elem, weight_id)
        guard_text = elem_data.get("guard")
        if elem_type == "ControlFlow" and guard_text:
            guard_id = generate_unique_id(elem_id, "guard-expr")
            create_activity_element(xml_elem, guard_id)
    elif element_tag == 'guard':
         body_id = generate_unique_id(elem_id, "body")
         lang_id = generate_unique_id(elem_id, "lang")
         create_activity_element(xml_elem, body_id)
         create_activity_element(xml_elem, lang_id)
    elif element_tag == 'node' and elem_type == 'CallBehaviorAction':
        for pin_id in pins_by_parent_action.get(elem_id, []):
             if pin_id not in processed_elements: create_activity_element(xml_elem, pin_id)
    elif element_tag == 'node' and elem_type not in ['InputPin', 'OutputPin']:
         in_part_ref_id = generate_unique_id(elem_id, "inPartRef")
         if in_part_ref_id in elements_by_id and in_part_ref_id not in processed_elements:
             create_activity_element(xml_elem, in_part_ref_id)
    elif element_tag == 'group': # 处理组内的节点/边引用
         for node_edge_id_orig in elem_data.get("nodeIds", []):
             ref_id = generate_unique_id(elem_id, node_edge_id_orig + "_grpRef")
             if ref_id in elements_by_id and ref_id not in processed_elements:
                  # *** 关键修正：在此处添加过滤逻辑 ***
                  node_edge_data = elements_by_id.get(node_edge_id_orig)
                  if node_edge_data and node_edge_data.get("type") not in ['ForkNode', 'JoinNode']:
                      create_activity_element(xml_elem, ref_id)
                  # else: 如果是 Fork/Join 节点，则跳过，不在此处创建引用

    return xml_elem


def json_to_uml_xmi_v11(json_data):
    """主函数 v11 - 包含所有修正"""
    global elements_by_id, children_by_parent, processed_elements, activity_partitions_data, blocks_data

    success, model_id, model_name = preprocess_data(json_data)
    if not success: return None

    # --- XML 构建 ---
    xmi_root = ET.Element("xmi:XMI")
    for ns_attr, ns_uri in NAMESPACES_DICT.items(): xmi_root.set(ns_attr, ns_uri)
    xmi_root.set("xmi:version", "2.5")

    model_attrib = {"xmi:type": "uml:Model", "xmi:id": model_id, "name": model_name}
    model_element = ET.SubElement(xmi_root, "uml:Model", attrib=model_attrib)

    # --- 添加固定的 ProfileApplication ---
    profile_app_attrs = {"xmi:type": "uml:ProfileApplication", "xmi:id": "SysML_Profile_App"} # 使用固定ID或生成唯一ID
    profile_app_elem = ET.SubElement(model_element, "profileApplication", attrib=profile_app_attrs)
    applied_profile_attrs = {"href": "http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML"} # 使用文档中的版本
    ET.SubElement(profile_app_elem, "appliedProfile", attrib=applied_profile_attrs)
    # <xmi:Extension> 部分比较复杂且依赖工具，暂时省略，如果需要可以添加

    # --- 处理根元素 ---
    root_ids = children_by_parent.get(model_id, [])
    for root_id in sorted(root_ids):
         create_xml_structure(model_element, root_id)

    # --- 添加 Stereotypes ---
    local_blocks_data = copy.deepcopy(blocks_data)
    local_partitions_data = copy.deepcopy(activity_partitions_data)

    for block_data in local_blocks_data:
        b_id = block_data["id"]
        if b_id in processed_elements:
             ET.SubElement(xmi_root, "sysml:Block", attrib={
                 "xmi:id": generate_unique_id(b_id, "app"), "base_Class": b_id })


    for part_data in local_partitions_data:
        p_id = part_data["id"]
        # 检查对应的<group>元素是否真的被处理了（可能因为无效被跳过）
        if p_id in processed_elements and elements_by_id.get(p_id, {}).get('type') == 'ActivityPartition':
            ET.SubElement(xmi_root, "sysml:AllocateActivityPartition", attrib={
                "xmi:id": generate_unique_id(p_id, "app"), "base_ActivityPartition": p_id })


    # --- Pretty Print ---
    try:
        rough_string = ET.tostring(xmi_root, encoding='unicode', method='xml', xml_declaration=False)
        xml_declaration = '<?xml version="1.0" encoding="utf-8"?>\n'
        rough_string = rough_string.replace(' />', '/>') # 尝试修复空标签问题
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="  ")
        if pretty_xml.startswith('<?xml'): pretty_xml = pretty_xml.split('\n', 1)[1]
        final_xml_string = xml_declaration + pretty_xml
        final_xml_string = final_xml_string.replace(' standalone="no"', '')
        return final_xml_string
    except Exception as e:
        print(f"\n错误：在最终XML解析/美化打印期间出错： {e}")
        traceback.print_exc()
        raw_output = ET.tostring(xmi_root, encoding='unicode', method='xml')
        print("返回原始XML字符串:\n", raw_output)
        return raw_output

# --- 加载 JSON 数据 ---
json_string = """
{
  "model": [
    {
      "id": "model-docreview-v18-partitions-uuid",
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

# --- 执行 ---
try: data = json.loads(json_string)
except json.JSONDecodeError as e: print(f"JSON 错误: {e}"); data = None

xml_output = None
if data: xml_output = json_to_uml_xmi_v11(data) 

if xml_output:
    if "Error" not in xml_output[:150] and "Traceback" not in xml_output:
        print(xml_output)
        with open("act_output_strict.xml", "w", encoding="utf-8") as f:
            f.write(xml_output)
else: print("XML 生成失败。")