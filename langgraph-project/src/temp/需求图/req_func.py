import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
import traceback # 导入 traceback 以便更好地调试错误

# --- 定义命名空间字典，与目标需求图XMI头对齐 ---
NAMESPACES_REQ_DICT = {
    "xmlns:xmi": "http://www.omg.org/spec/XMI/20131001",
    "xmlns:uml": "http://www.omg.org/spec/UML/20131001",
    "xmlns:sysml": "http://www.omg.org/spec/SysML/20181001/SysML",
    # 假设这些是 MagicDraw 特定的，如果您的目标工具是它，则保留
    "xmlns:DSL_Customization": "http://www.magicdraw.com/schemas/DSL_Customization.xmi",
    "xmlns:MD_Customization_for_SysML__additional_stereotypes": "http://www.magicdraw.com/spec/Customization/180/SysML",
    "xmlns:StandardProfile": "http://www.omg.org/spec/UML/20131001/StandardProfile",
    "xmlns:MagicDraw_Profile": "http://www.omg.org/spec/UML/20131001/MagicDrawProfile"
}

# --- 全局字典 ---
elements_by_id_req = {}
children_by_parent_req = {}
requirements_data_list = []
blocks_data_list_req = []
testcases_data_list = []
abstractions_data_list = []
processed_elements_req = set()

def generate_unique_id_for_internal_elements(base_id, suffix):
    """为 TestCase 内部元素生成 ID (确保与主元素ID不同但相关)"""
    clean_base = str(base_id).replace("-", "_")
    clean_suffix = str(suffix).replace("-", "_")
    # 避免与主ID或构造型ID冲突，可以加一个特定的前缀或不同的后缀模式
    return f"_{clean_base}_internal_{clean_suffix}"


def generate_stereotype_id(base_id, suffix="application"):
    clean_base = str(base_id).replace("-", "_")
    clean_suffix = str(suffix).replace("-", "_")
    # 确保构造型ID的格式与您的示例一致
    # 示例: _2021x_2_320032_1748340755485_929307_3380_application
    # 如果您的 base_id 已经是这种格式，则直接添加后缀
    if base_id.startswith("_") and suffix == "application": # 特殊处理原始ID的追加
        return f"{base_id}_application"
    elif base_id.startswith("_") and suffix.endswith("_"): # 针对 Requirement ID 的特殊后缀
         return f"{base_id}{suffix}"

    return f"_{clean_base}_{clean_suffix}"


def preprocess_req_data(json_data):
    global elements_by_id_req, children_by_parent_req
    global requirements_data_list, blocks_data_list_req, testcases_data_list, abstractions_data_list
    global processed_elements_req

    elements_by_id_req.clear()
    children_by_parent_req.clear()
    requirements_data_list.clear()
    blocks_data_list_req.clear()
    testcases_data_list.clear()
    abstractions_data_list.clear()
    processed_elements_req.clear()

    if "elements" not in json_data:
        print("错误：JSON数据必须包含 'elements' 键。")
        return False, None, None

    elements_list = json_data["elements"]
    try:
        elements_by_id_req = {elem["id"]: elem for elem in elements_list}
    except KeyError as e:
        print(f"错误：JSON元素缺少 'id' 字段：{e}")
        return False, None, None
    except TypeError as e:
        print(f"错误：迭代JSON元素列表时出现问题：{e}")
        return False, None, None

    model_id_req = "model-root-req-uuid"
    model_name_req = "DefaultRequirementsModel"
    if "model" in json_data and json_data["model"]:
        try:
            model_name_req = json_data["model"][0].get("name", model_name_req)
            model_id_req = json_data["model"][0].get("id", model_id_req)
        except (IndexError, AttributeError):
             print("警告：无法从JSON读取模型名称或ID。")

    for elem_id, elem_data in elements_by_id_req.items():
        parent_id = elem_data.get("parentId")
        elem_type = elem_data.get("type", "Unknown")
        current_parent_id = parent_id if parent_id is not None else model_id_req
        if current_parent_id not in children_by_parent_req:
            children_by_parent_req[current_parent_id] = []
        children_by_parent_req[current_parent_id].append(elem_id)

        if elem_type == "Requirement":
            requirements_data_list.append(elem_data)
        elif elem_type == "Block":
            blocks_data_list_req.append(elem_data)
        elif elem_type == "TestCase":
            testcases_data_list.append(elem_data)
        elif elem_type in ["DeriveReqt", "Satisfy", "Verify"]:
            abstractions_data_list.append(elem_data)
    return True, model_id_req, model_name_req


def create_req_xml_packaged_element(parent_xml_element, elem_id):
    global processed_elements_req, elements_by_id_req, children_by_parent_req

    if elem_id in processed_elements_req or elem_id not in elements_by_id_req:
        return None

    elem_data = elements_by_id_req[elem_id]
    elem_type = elem_data["type"]
    elem_name = elem_data.get("name")
    elem_xmi_id = elem_data["id"]

    packaged_element_attrs = {"xmi:id": elem_xmi_id}
    if elem_name:
        packaged_element_attrs["name"] = elem_name

    if elem_type == 'Package':
        packaged_element_attrs["xmi:type"] = "uml:Package"
    elif elem_type == 'Requirement':
        packaged_element_attrs["xmi:type"] = "uml:Class"
    elif elem_type == 'Block':
        packaged_element_attrs["xmi:type"] = "uml:Class"
    elif elem_type == 'TestCase':
        packaged_element_attrs["xmi:type"] = "uml:Activity" # TestCase 映射到 uml:Activity
    elif elem_type in ['DeriveReqt', 'Satisfy', 'Verify']:
        packaged_element_attrs["xmi:type"] = "uml:Abstraction"
    else:
        print(f"信息：跳过在包结构中创建未知或非预期的元素类型 {elem_type} (ID: {elem_xmi_id})")
        processed_elements_req.add(elem_xmi_id)
        return None

    try:
        xml_elem = ET.SubElement(parent_xml_element, 'packagedElement', attrib=packaged_element_attrs)
        processed_elements_req.add(elem_xmi_id)
    except Exception as e:
        print(f"错误：创建 packagedElement {elem_xmi_id} ({elem_type}) 时出错：{e}")
        return None

    # --- 重点修正：处理 TestCase (uml:Activity) 的内部结构 ---
    if elem_type == 'TestCase':
        # 1. 创建 ownedParameter for verdict
        verdict_param_id = generate_unique_id_for_internal_elements(elem_xmi_id, "verdict_param")
        owned_param_attrs = {
            "xmi:type": "uml:Parameter",
            "xmi:id": verdict_param_id,
            "name": "verdict",
            "visibility": "public", # 与您的示例一致
            "direction": "return"
        }
        owned_param_elem = ET.SubElement(xml_elem, "ownedParameter", attrib=owned_param_attrs)

        # 1a. 添加 ownedParameter 的 type 和 xmi:Extension
        param_type_elem = ET.SubElement(owned_param_elem, "type", {
            "href": "http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.VerdictKind"
        })
        # 注意：这里的 xmi:Extension 和 referenceExtension 是 MagicDraw 特定的。
        # 如果您的目标工具不同，这部分可能需要调整或移除。
        
        # param_type_ext_elem = ET.SubElement(param_type_elem, "xmi:Extension", {"extender": "MagicDraw UML 2021x"}) # 或您使用的工具版本
        # ET.SubElement(param_type_ext_elem, "referenceExtension", {
        #     "referentPath": "SysML::Requirements::VerdictKind", # 标准路径
        #     "referentType": "Enumeration"
        #     # "originalID" 属性在您的示例中有，但通常是工具内部ID，可能难以从JSON生成，
        #     # 如果不是严格必要，可以考虑省略或使用占位符。
        #     # "originalID": "_11_5EAPbeta_be00301_1147937844838_242658_2617"
        # })


        # 2. 创建 node (ActivityParameterNode) for verdict
        verdict_node_id = generate_unique_id_for_internal_elements(elem_xmi_id, "verdict_node")
        activity_node_attrs = {
            "xmi:type": "uml:ActivityParameterNode",
            "xmi:id": verdict_node_id,
            "name": "verdict",
            "visibility": "public", # 与您的示例一致
            "parameter": verdict_param_id # 引用上面创建的 ownedParameter
        }
        activity_node_elem = ET.SubElement(xml_elem, "node", attrib=activity_node_attrs)

        # 2a. 添加 ActivityParameterNode 的 type 和 xmi:Extension
        node_type_elem = ET.SubElement(activity_node_elem, "type", {
             "href": "http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.VerdictKind"
        })
        # node_type_ext_elem = ET.SubElement(node_type_elem, "xmi:Extension", {"extender": "MagicDraw UML 2021x"})
        # ET.SubElement(node_type_ext_elem, "referenceExtension", {
        #     "referentPath": "SysML::Requirements::VerdictKind",
        #     "referentType": "Enumeration"
        #     # "originalID": "_11_5EAPbeta_be00301_1147937844838_242658_2617"
        # })
    # --- 修正结束 ---

    elif elem_type in ['DeriveReqt', 'Satisfy', 'Verify']:
        client_id = None
        supplier_id = None
        if elem_type == 'DeriveReqt':
            client_id = elem_data.get("derivedRequirementId") # Specific/Derived
            supplier_id = elem_data.get("sourceRequirementId") # General
        elif elem_type == 'Satisfy':
            client_id = elem_data.get("blockId")
            supplier_id = elem_data.get("requirementId")
        elif elem_type == 'Verify':
            client_id = elem_data.get("testCaseId")
            supplier_id = elem_data.get("requirementId")

        if client_id and client_id in elements_by_id_req:
            ET.SubElement(xml_elem, "client", {"xmi:idref": client_id})
        else:
            print(f"警告：关系 {elem_xmi_id} ({elem_type}) 的 Client ID '{client_id}' 无效。")
        if supplier_id and supplier_id in elements_by_id_req:
            ET.SubElement(xml_elem, "supplier", {"xmi:idref": supplier_id})
        else:
            print(f"警告：关系 {elem_xmi_id} ({elem_type}) 的 Supplier ID '{supplier_id}' 无效。")

    if elem_type == 'Package' and elem_xmi_id in children_by_parent_req:
        child_ids = sorted(children_by_parent_req.get(elem_xmi_id, []))
        for child_id in child_ids:
            child_data = elements_by_id_req.get(child_id)
            if child_data and child_data.get('parentId') == elem_xmi_id:
                 create_req_xml_packaged_element(xml_elem, child_id)
    return xml_elem


def json_to_requirements_xmi(json_data_str):
    global elements_by_id_req, children_by_parent_req, processed_elements_req
    global requirements_data_list, blocks_data_list_req, testcases_data_list, abstractions_data_list

    try:
        json_data = json.loads(json_data_str)
    except json.JSONDecodeError as e:
        print(f"错误: JSON 解析失败 - {e}")
        return None

    success, model_id_req, model_name_req = preprocess_req_data(json_data)
    if not success: return None

    xmi_root = ET.Element("xmi:XMI")
    for ns_attr, ns_uri in NAMESPACES_REQ_DICT.items():
        xmi_root.set(ns_attr, ns_uri)

    model_attrib = {"xmi:type": "uml:Model", "xmi:id": model_id_req, "name": model_name_req}
    model_element = ET.SubElement(xmi_root, "uml:Model", attrib=model_attrib)

    root_ids_in_model = children_by_parent_req.get(model_id_req, [])
    for elem_id_in_model in sorted(root_ids_in_model):
        create_req_xml_packaged_element(model_element, elem_id_in_model)

    for req_data in requirements_data_list:
        req_uml_id = req_data["id"]
        if req_uml_id in processed_elements_req:
            # 您的示例中 Requirement 的 stereotype ID 以 '_' 结尾
            stereotype_xmi_id = generate_stereotype_id(req_uml_id, "_") # 特殊后缀
            attrs = {
                "xmi:id": stereotype_xmi_id,
                "base_Class": req_uml_id,
                "Id": req_data.get("reqId", ""),
                "Text": req_data.get("text", "")
            }
            ET.SubElement(xmi_root, "sysml:Requirement", attrib=attrs)

    for blk_data in blocks_data_list_req:
        blk_uml_id = blk_data["id"]
        if blk_uml_id in processed_elements_req:
            attrs = {
                "xmi:id": generate_stereotype_id(blk_uml_id, "application"),
                "base_Class": blk_uml_id
            }
            ET.SubElement(xmi_root, "sysml:Block", attrib=attrs)

    for tc_data in testcases_data_list:
        tc_uml_id = tc_data["id"]
        if tc_uml_id in processed_elements_req:
            attrs = {
                "xmi:id": generate_stereotype_id(tc_uml_id, "application"),
                "base_Behavior": tc_uml_id
            }
            ET.SubElement(xmi_root, "sysml:TestCase", attrib=attrs)

    for abstr_data in abstractions_data_list:
        abstr_uml_id = abstr_data["id"]
        rel_type = abstr_data["type"]
        stereotype_tag = None
        if rel_type == "DeriveReqt": stereotype_tag = "sysml:DeriveReqt"
        elif rel_type == "Satisfy": stereotype_tag = "sysml:Satisfy"
        elif rel_type == "Verify": stereotype_tag = "sysml:Verify"

        if stereotype_tag and abstr_uml_id in processed_elements_req:
            attrs = {
                "xmi:id": generate_stereotype_id(abstr_uml_id, "application"),
                "base_Abstraction": abstr_uml_id
            }
            ET.SubElement(xmi_root, stereotype_tag, attrib=attrs)

    try:
        rough_string = ET.tostring(xmi_root, encoding='utf-8', method='xml')
        reparsed = minidom.parseString(rough_string)
        pretty_xml = reparsed.toprettyxml(indent="    ", encoding="utf-8").decode('utf-8')
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n' # 使用大写 UTF-8
        
        # 确保XML声明只出现一次
        if pretty_xml.startswith('<?xml'):
            pretty_xml = pretty_xml.split('\n', 1)[1] if len(pretty_xml.split('\n', 1)) > 1 else ''

        # 移除由 minidom.toprettyxml 添加的多余空行
        lines = [line for line in pretty_xml.splitlines() if line.strip()]
        final_xml_string = xml_declaration + "\n".join(lines)
        return final_xml_string
    except Exception as e:
        print(f"\n错误：在最终XML解析/美化打印期间出错： {e}")
        traceback.print_exc()
        raw_output_fallback = ET.tostring(xmi_root, encoding='unicode', method='xml')
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + raw_output_fallback

# --- 示例JSON (你需要用你实际的JSON替换这里) ---
# 这是根据你之前给的“智慧办公套件”文字描述可能生成的JSON结构（简化的示意）
sample_req_json_str = """
{
        "model": [
          {
            "id": "model-bicycle-req-uuid",
            "name": "自行车系统需求模型"
          }
        ],
        "elements": [
          {
            "id": "pkg-systemfunc-uuid",
            "type": "Package",
            "name": "系统功能",
            "parentId": "model-bicycle-req-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-frontrear-drive-uuid",
            "type": "Requirement",
            "name": "前后轮驱动",
            "reqId": "REQ-001",
            "text": "系统应能实现前后轮驱动，支持人力踩踏驱动自行车前行",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-shift-uuid",
            "type": "Requirement",
            "name": "变速功能",
            "reqId": "REQ-002",
            "text": "具备变速功能，支持至少3个档位（低速、中速、高速）",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-brake-uuid",
            "type": "Requirement",
            "name": "刹车系统",
            "reqId": "REQ-003",
            "text": "具有刹车系统，支持前后轮制动",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-steering-uuid",
            "type": "Requirement",
            "name": "转向控制",
            "reqId": "REQ-004",
            "text": "提供车把转向控制，支持左右转向",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-weight-uuid",
            "type": "Requirement",
            "name": "结构重量",
            "reqId": "REQ-005",
            "text": "结构重量不超过15公斤，确保便携性",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-wheel-uuid",
            "type": "Requirement",
            "name": "轮径和轮胎宽度",
            "reqId": "REQ-006",
            "text": "轮径为700mm，轮胎宽度为25mm，适应城市道路",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-frame-uuid",
            "type": "Requirement",
            "name": "车架材料",
            "reqId": "REQ-007",
            "text": "车架材料为铝合金，强度满足承载200kg的要求",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-brake-response-uuid",
            "type": "Requirement",
            "name": "制动响应时间",
            "reqId": "REQ-008",
            "text": "制动响应时间不超过0.2秒",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-mech-conn-uuid",
            "type": "Requirement",
            "name": "机械连接标准",
            "reqId": "REQ-009",
            "text": "所有机械连接必须符合标准尺寸（如螺栓孔径为10mm）",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-electrical-uuid",
            "type": "Requirement",
            "name": "电气安全",
            "reqId": "REQ-010",
            "text": "电气部分（如灯光）必须符合安全规范",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-load-test-uuid",
            "type": "Requirement",
            "name": "载重测试",
            "reqId": "REQ-011",
            "text": "通过载重测试验证承载能力",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-roadtest-uuid",
            "type": "Requirement",
            "name": "行驶测试",
            "reqId": "REQ-012",
            "text": "通过行驶测试验证变速和刹车性能",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "req-priority-uuid",
            "type": "Requirement",
            "name": "优先级",
            "reqId": "REQ-013",
            "text": "基础行驶功能优先，次要为变速和灯光",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "blk-mech-uuid",
            "type": "Block",
            "name": "机械结构",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "blk-elec-uuid",
            "type": "Block",
            "name": "电气系统",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "blk-shift-uuid",
            "type": "Block",
            "name": "变速机构",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "blk-brake-uuid",
            "type": "Block",
            "name": "刹车系统",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "blk-steering-uuid",
            "type": "Block",
            "name": "转向控制",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "blk-wheel-uuid",
            "type": "Block",
            "name": "轮子",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "blk-frame-uuid",
            "type": "Block",
            "name": "车架",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "tc-loadtest-uuid",
            "type": "TestCase",
            "name": "载重测试",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "tc-roadtest-uuid",
            "type": "TestCase",
            "name": "行驶性能测试",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "tc-shift-test-uuid",
            "type": "TestCase",
            "name": "变速测试",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "tc-brake-resp-uuid",
            "type": "TestCase",
            "name": "刹车响应测试",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "tc-light-safety-uuid",
            "type": "TestCase",
            "name": "灯光安全测试",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-derive-weight-frame-uuid",
            "type": "DeriveReqt",
            "sourceRequirementId": "req-weight-uuid",
            "derivedRequirementId": "req-frame-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-satisfy-mech-frontrear-uuid",
            "type": "Satisfy",
            "blockId": "blk-mech-uuid",
            "requirementId": "req-frontrear-drive-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-satisfy-shift-uuid",
            "type": "Satisfy",
            "blockId": "blk-shift-uuid",
            "requirementId": "req-shift-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-satisfy-brake-uuid",
            "type": "Satisfy",
            "blockId": "blk-brake-uuid",
            "requirementId": "req-brake-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-satisfy-steering-uuid",
            "type": "Satisfy",
            "blockId": "blk-steering-uuid",
            "requirementId": "req-steering-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-satisfy-weight-uuid",
            "type": "Satisfy",
            "blockId": "blk-frame-uuid",
            "requirementId": "req-weight-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-satisfy-wheel-uuid",
            "type": "Satisfy",
            "blockId": "blk-wheel-uuid",
            "requirementId": "req-wheel-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-satisfy-frame-material-uuid",
            "type": "Satisfy",
            "blockId": "blk-frame-uuid",
            "requirementId": "req-frame-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-satisfy-elec-safety-uuid",
            "type": "Satisfy",
            "blockId": "blk-elec-uuid",
            "requirementId": "req-electrical-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-verify-load-uuid",
            "type": "Verify",
            "testCaseId": "tc-loadtest-uuid",
            "requirementId": "req-weight-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-verify-roadtest-uuid",
            "type": "Verify",
            "testCaseId": "tc-roadtest-uuid",
            "requirementId": "req-roadtest-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-verify-shift-uuid",
            "type": "Verify",
            "testCaseId": "tc-shift-test-uuid",
            "requirementId": "req-shift-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-verify-brake-resp-uuid",
            "type": "Verify",
            "testCaseId": "tc-brake-resp-uuid",
            "requirementId": "req-brake-response-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          },
          {
            "id": "rel-verify-electrical-uuid",
            "type": "Verify",
            "testCaseId": "tc-light-safety-uuid",
            "requirementId": "req-electrical-uuid",
            "parentId": "pkg-systemfunc-uuid",
            "_source": "Requirement"
          }
        ]
      }
"""

if __name__ == '__main__':
    # 假设你的 LLM 生成的 JSON 在一个名为 generated_req_json 的变量中
    # generated_req_json = '...' # 这里应该是你的 Agent 输出的 JSON 字符串

    print("--- 正在使用示例JSON生成需求图XMI ---")
    generated_xmi = json_to_requirements_xmi(sample_req_json_str)

    if generated_xmi:
        print("\n--- 生成的 XMI: ---")
        print(generated_xmi)
        # 你可以将 generated_xmi 保存到文件
        # with open("requirements_diagram.xmi", "w", encoding="utf-8") as f:
        #     f.write(generated_xmi)
        # print("\n--- XMI 已保存到 requirements_diagram.xmi ---")
    else:
        print("--- XMI 生成失败 ---")