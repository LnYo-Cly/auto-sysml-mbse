import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
import traceback

# --- 定义命名空间字典 (应与目标XMI完全一致) ---
NAMESPACES_SEQ_DICT = {
    "xmlns:xmi": "http://www.omg.org/spec/XMI/20131001",
    "xmlns:uml": "http://www.omg.org/spec/UML/20131001",
    "xmlns:sysml": "http://www.omg.org/spec/SysML/20181001/SysML",
    "xmlns:MD_Customization_for_Requirements__additional_stereotypes": "http://www.magicdraw.com/spec/Customization/180/Requirements",
    "xmlns:MD_Customization_for_SysML__additional_stereotypes": "http://www.magicdraw.com/spec/Customization/180/SysML",
    "xmlns:DSL_Customization": "http://www.magicdraw.com/schemas/DSL_Customization.xmi",
    "xmlns:Validation_Profile": "http://www.magicdraw.com/schemas/Validation_Profile.xmi",
    "xmlns:MagicDraw_Profile": "http://www.omg.org/spec/UML/20131001/MagicDrawProfile",
    "xmlns:StandardProfile": "http://www.omg.org/spec/UML/20131001/StandardProfile"
}

# --- 全局字典 ---
elements_by_id_seq = {}
children_by_parent_seq = {} # 新增: {parentId: [childId1, childId2]}
xml_node_created_map = {}   # 新增: {elementId: ET.Element} - 用于coveredBy等后期处理
stereotypes_to_apply_seq = []


def generate_stereotype_id_seq(base_id, stereotype_name_suffix="stereotype_app"):
    clean_base = str(base_id).replace("-", "_")
    clean_suffix = str(stereotype_name_suffix).replace("-", "_").replace(":", "_")
    return f"_{clean_base}_{clean_suffix}"

def preprocess_seq_data(json_data):
    global elements_by_id_seq, children_by_parent_seq, stereotypes_to_apply_seq, xml_node_created_map
    elements_by_id_seq.clear()
    children_by_parent_seq.clear()
    stereotypes_to_apply_seq.clear()
    xml_node_created_map.clear()

    if "elements" not in json_data:
        print("错误：JSON数据必须包含 'elements' 键。")
        return False, None, None, None

    elements_list = json_data["elements"]
    try:
        for elem in elements_list:
            elements_by_id_seq[elem["id"]] = elem
            if "stereotypes" in elem and isinstance(elem["stereotypes"], list):
                for stereo_app in elem["stereotypes"]:
                    if "type" in stereo_app:
                         stereotypes_to_apply_seq.append({
                             "stereotype_qname": stereo_app["type"],
                             "base_element_id": elem["id"],
                             "stereotype_id_suffix": stereo_app.get("id_suffix", stereo_app["type"].split(':')[-1].lower()),
                             "stereotype_props": stereo_app.get("properties", {})
                         })
    except KeyError as e:
        print(f"错误：JSON元素缺少 'id' 字段：{e}")
        return False, None, None, None
    except TypeError as e:
        print(f"错误：迭代JSON元素列表时出现问题：{e}")
        return False, None, None, None

    model_id_seq = "model-root-seq-uuid"
    model_name_seq = "DefaultSequenceModel"
    model_type_str = "uml:Model"

    if "model" in json_data and isinstance(json_data["model"], list) and json_data["model"]:
        try:
            model_data = json_data["model"][0]
            model_name_seq = model_data.get("name", model_name_seq)
            model_id_seq = model_data.get("id", model_id_seq)
            json_model_type = model_data.get("type", "Model")
            model_type_str = f"uml:{json_model_type}"
            if model_id_seq not in elements_by_id_seq:
                 elements_by_id_seq[model_id_seq] = model_data
        except Exception as e:
            print(f"警告：解析模型信息时出错: {e}。使用默认值。")
    
    # 构建 children_by_parent_seq 映射
    for elem_id, elem_data in elements_by_id_seq.items():
        parent_id = elem_data.get("parentId")
        # 模型本身没有parentId，其子元素直接以模型ID为父
        actual_parent_id = parent_id if parent_id is not None else (model_id_seq if elem_id != model_id_seq else None)
        
        if actual_parent_id:
            if actual_parent_id not in children_by_parent_seq:
                children_by_parent_seq[actual_parent_id] = []
            children_by_parent_seq[actual_parent_id].append(elem_id)
            
    return True, model_id_seq, model_name_seq, model_type_str


def create_xml_element_seq(elem_id, parent_xml_node):
    """
    递归创建XMI元素，并将其附加到parent_xml_node。
    返回创建的ET.Element节点，如果创建失败则返回None。
    """
    if elem_id not in elements_by_id_seq:
        print(f"警告: 尝试创建的元素ID '{elem_id}' 在JSON数据中未找到。")
        return None
    
    elem_data = elements_by_id_seq[elem_id]
    elem_type_json = elem_data["type"]
    elem_name = elem_data.get("name")
    
    attrs = {"xmi:id": elem_id}
    if elem_name: attrs["name"] = elem_name
    
    xml_tag = "packagedElement" 
    xmi_type_val = f"uml:{elem_type_json}"
    
    parent_id = elem_data.get("parentId")
    parent_data = elements_by_id_seq.get(parent_id) if parent_id else None
    parent_type_json = parent_data.get("type") if parent_data else None

    if elem_type_json == "Block": xmi_type_val = "uml:Class"
    elif elem_type_json == "Event" and "kind" not in elem_data: xmi_type_val = "uml:AnyReceiveEvent"
    elif elem_type_json == "Interaction":
        if parent_type_json in ["Class", "Block", "Operation"]: xml_tag = "ownedBehavior"
    elif elem_type_json == "Property":
        if parent_type_json in ["Class", "Block", "Interaction"]: xml_tag = "ownedAttribute"
        elif parent_type_json == "Association": xml_tag = "ownedEnd"
    elif elem_type_json == "Operation": xml_tag = "ownedOperation"
    elif elem_type_json == "Parameter": xml_tag = "ownedParameter"
    elif elem_type_json == "Lifeline": xml_tag = "lifeline"
    elif elem_type_json == "Message": xml_tag = "message"
    elif elem_type_json in ["MessageOccurrenceSpecification", "DestructionOccurrenceSpecification", "CombinedFragment"]: xml_tag = "fragment"
    elif elem_type_json == "InteractionOperand": xml_tag = "operand"
    elif elem_type_json == "InteractionConstraint": xml_tag = "guard"

    attrs["xmi:type"] = xmi_type_val
    if "visibility" in elem_data: attrs["visibility"] = elem_data["visibility"]
    elif xml_tag not in ["packagedElement", "ownedEnd"] and elem_type_json not in ["Model", "Package", "Association"]: # 许多UML元素默认为public
        attrs["visibility"] = "public"

    current_xml_elem = ET.SubElement(parent_xml_node, xml_tag, attrs)
    xml_node_created_map[elem_id] = current_xml_elem # 存储创建的节点

    # --- 填充特定属性和直接子元素（非递归部分） ---
    if elem_type_json in ["Class", "Block"] and elem_data.get("classifierBehaviorId"):
        current_xml_elem.set("classifierBehavior", elem_data["classifierBehaviorId"])
    elif elem_type_json == "Property":
        if elem_data.get("typeId"): current_xml_elem.set("type", elem_data["typeId"])
        elif elem_data.get("typeHref"): ET.SubElement(current_xml_elem, "type", {"href": elem_data["typeHref"]})
        if elem_data.get("aggregation"): current_xml_elem.set("aggregation", elem_data["aggregation"])
        if elem_data.get("associationId"): current_xml_elem.set("association", elem_data["associationId"])
    elif elem_type_json == "Parameter":
        if elem_data.get("direction"): current_xml_elem.set("direction", elem_data["direction"])
        type_node = ET.SubElement(current_xml_elem, "type") # Type是子元素
        if elem_data.get("typeId"): type_node.set("xmi:idref", elem_data["typeId"])
        elif elem_data.get("typeHref"): type_node.set("href", elem_data["typeHref"])
    elif elem_type_json == "Association":
        for end_id in elem_data.get("memberEndIds", []): ET.SubElement(current_xml_elem, "memberEnd", {"xmi:idref": end_id})
        for end_id in elem_data.get("navigableOwnedEndIds", []): ET.SubElement(current_xml_elem, "navigableOwnedEnd", {"xmi:idref": end_id})
    elif elem_type_json == "Lifeline" and elem_data.get("representsId"):
        current_xml_elem.set("represents", elem_data["representsId"])
    elif elem_type_json == "Message":
        if elem_data.get("sendEventId"): current_xml_elem.set("sendEvent", elem_data["sendEventId"])
        if elem_data.get("receiveEventId"): current_xml_elem.set("receiveEvent", elem_data["receiveEventId"])
        if elem_data.get("signatureId"): current_xml_elem.set("signature", elem_data["signatureId"])
        if elem_data.get("messageSort"): current_xml_elem.set("messageSort", elem_data["messageSort"])
        for arg_data in elem_data.get("arguments", []):
            arg_attrs = {"xmi:type": "uml:OpaqueExpression"}
            if "id" in arg_data: arg_attrs["xmi:id"] = arg_data["id"] # Argument的OpaqueExpression可以有ID
            arg_elem = ET.SubElement(current_xml_elem, "argument", arg_attrs)
            if "body" in arg_data: ET.SubElement(arg_elem, "body").text = str(arg_data["body"])
            if "language" in arg_data: ET.SubElement(arg_elem, "language").text = arg_data["language"]
    elif elem_type_json in ["MessageOccurrenceSpecification", "DestructionOccurrenceSpecification"]:
        if elem_data.get("coveredId"): current_xml_elem.set("covered", elem_data["coveredId"])
        if elem_type_json == "MessageOccurrenceSpecification" and elem_data.get("messageId"):
            current_xml_elem.set("message", elem_data["messageId"])
    elif elem_type_json == "CombinedFragment":
        if elem_data.get("interactionOperator"): current_xml_elem.set("interactionOperator", elem_data["interactionOperator"])
        for ll_id in elem_data.get("coveredLifelineIds", []): ET.SubElement(current_xml_elem, "covered", {"xmi:idref": ll_id})
    elif elem_type_json == "InteractionConstraint": # 作为Operand的guard
        spec_data = elem_data.get("specification")
        if isinstance(spec_data, dict):
            spec_attrs = {"xmi:type": "uml:OpaqueExpression"}
            if "id" in spec_data: spec_attrs["xmi:id"] = spec_data["id"]
            spec_elem = ET.SubElement(current_xml_elem, "specification", spec_attrs)
            if "body" in spec_data: ET.SubElement(spec_elem, "body").text = spec_data["body"]
            if "language" in spec_data: ET.SubElement(spec_elem, "language").text = spec_data["language"]
    elif elem_type_json == "SignalEvent" and elem_data.get("signalId"):
        current_xml_elem.set("signal", elem_data["signalId"])

    # --- 递归处理子元素 (基于 children_by_parent_seq 映射) ---
    if elem_id in children_by_parent_seq:
        for child_id in children_by_parent_seq[elem_id]:
            create_xml_element_seq(child_id, current_xml_elem)
            
    return current_xml_elem


def json_to_sequence_xmi(json_data_str):
    global elements_by_id_seq, children_by_parent_seq, stereotypes_to_apply_seq, xml_node_created_map

    try:
        json_data = json.loads(json_data_str)
    except json.JSONDecodeError as e:
        print(f"错误: JSON 解析失败 - {e}")
        return None

    success, model_id, model_name, model_type_str = preprocess_seq_data(json_data)
    if not success: return None

    xmi_root = ET.Element("xmi:XMI")
    for ns_attr, ns_uri in NAMESPACES_SEQ_DICT.items():
        xmi_root.set(ns_attr, ns_uri)

    model_tag_name = model_type_str.split(':')[-1]
    model_xml_attrs = {"xmi:type": model_type_str, "xmi:id": model_id}
    if model_name: model_xml_attrs["name"] = model_name
    model_xml_node = ET.SubElement(xmi_root, model_tag_name, attrib=model_xml_attrs)
    xml_node_created_map[model_id] = model_xml_node # 存储模型节点

    # 从模型的直接子元素开始递归构建
    if model_id in children_by_parent_seq:
        for child_id_of_model in children_by_parent_seq[model_id]:
            create_xml_element_seq(child_id_of_model, model_xml_node)
            
    # 后期处理: 添加 <coveredBy> 到 Lifelines
    for elem_id_frag, frag_data in elements_by_id_seq.items():
        frag_type = frag_data.get("type")
        if frag_type in ["MessageOccurrenceSpecification", "DestructionOccurrenceSpecification", "CombinedFragment"]:
            covered_ids_for_frag = []
            if "coveredId" in frag_data: # For MOS, DOS
                covered_ids_for_frag.append(frag_data["coveredId"])
            elif "coveredLifelineIds" in frag_data: # For CF
                covered_ids_for_frag.extend(frag_data["coveredLifelineIds"])
            
            for ll_id_covered in covered_ids_for_frag:
                if ll_id_covered in xml_node_created_map:
                    lifeline_xml_node_ref = xml_node_created_map[ll_id_covered]
                    # 确保Lifeline节点存在且类型正确
                    if lifeline_xml_node_ref is not None and lifeline_xml_node_ref.get("xmi:type") == "uml:Lifeline":
                        ET.SubElement(lifeline_xml_node_ref, "coveredBy", {"xmi:idref": elem_id_frag})
                else:
                    print(f"警告: 为fragment '{elem_id_frag}' 添加coveredBy时未找到lifeline '{ll_id_covered}' 的XML节点。")


    # 应用构造型
    for stereo_app_data in stereotypes_to_apply_seq:
        stereo_qname = stereo_app_data["stereotype_qname"]
        base_elem_id = stereo_app_data["base_element_id"]
        stereo_id_suffix = stereo_app_data["stereotype_id_suffix"]
        
        base_elem_data = elements_by_id_seq.get(base_elem_id)
        if not base_elem_data: continue
        
        base_attr_name = None
        json_base_type = base_elem_data.get("type")

        if stereo_qname == "sysml:Block" and json_base_type in ["Class", "Block"]: base_attr_name = "base_Class"
        elif stereo_qname == "MD_Customization_for_SysML__additional_stereotypes:PartProperty" and json_base_type == "Property": base_attr_name = "base_Property"
        # ... (添加更多构造型映射规则) ...

        if base_attr_name:
            stereo_attrs = {"xmi:id": generate_stereotype_id_seq(base_elem_id, stereo_id_suffix), base_attr_name: base_elem_id}
            ET.SubElement(xmi_root, stereo_qname, attrib=stereo_attrs)
        else:
            print(f"警告: 未能应用构造型 '{stereo_qname}' 到元素 ID '{base_elem_id}' (JSON 类型: '{json_base_type}')")

    try:
        rough_string = ET.tostring(xmi_root, encoding='utf-8', method='xml')
        reparsed = minidom.parseString(rough_string)
        pretty_xml_str = reparsed.toprettyxml(indent="    ", encoding="utf-8").decode('utf-8')
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        if pretty_xml_str.startswith('<?xml'):
            pretty_xml_str = pretty_xml_str.split('\n', 1)[1] if len(pretty_xml_str.split('\n', 1)) > 1 else ''
        lines = [line for line in pretty_xml_str.splitlines() if line.strip()]
        final_xml_string = xml_declaration + "\n".join(lines)
        return final_xml_string
    except Exception as e:
        print(f"\n错误：在最终XML解析/美化打印期间出错： {e}")
        traceback.print_exc()
        raw_output_fallback = ET.tostring(xmi_root, encoding='unicode', method='xml')
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + raw_output_fallback

# --- 使用您的ATM序列图JSON ---
actual_atm_interaction_json_str = """
{
  "model": [
    {
      "id": "model-atm-sys-uuid",
      "type": "Model",
      "name": "ATM系统模型"
    }
  ],
  "elements": [
    {
      "id": "pkg-banksvc-uuid",
      "type": "Package",
      "name": "银行服务",
      "parentId": "model-atm-sys-uuid"
    },
    {
      "id": "actor-customer-uuid",
      "type": "Actor",
      "name": "客户",
      "parentId": "pkg-banksvc-uuid"
    },
    {
      "id": "cls-atm-uuid",
      "type": "Class",
      "name": "ATM",
      "parentId": "pkg-banksvc-uuid",
      "classifierBehaviorId": "interaction-withdraw-uuid",
      "ownedOperationIds": ["op-execwithdraw-uuid"],
      "ownedAttributeIds": ["prop-atm-instance-uuid", "prop-db-connector-uuid"]
    },
    {
      "id": "cls-db-uuid",
      "type": "Class",
      "name": "后端数据库",
      "parentId": "pkg-banksvc-uuid",
      "ownedOperationIds": ["op-querybal-uuid"],
      "ownedAttributeIds": []
    },
    {
      "id": "prop-interaction-customer-uuid",
      "type": "Property",
      "name": "p_customer",
      "parentId": "interaction-withdraw-uuid",
      "typeId": "actor-customer-uuid",
      "aggregation": "none",
      "visibility": "public"
    },
    {
      "id": "prop-atm-instance-uuid",
      "type": "Property",
      "name": "atm_instance",
      "parentId": "cls-atm-uuid",
      "typeId": "cls-atm-uuid",
      "aggregation": "none",
      "visibility": "public"
    },
    {
      "id": "prop-db-connector-uuid",
      "type": "Property",
      "name": "db_connector",
      "parentId": "cls-atm-uuid",
      "typeId": "cls-db-uuid",
      "aggregation": "none",
      "visibility": "public"
    },
    {
      "id": "op-execwithdraw-uuid",
      "type": "Operation",
      "name": "执行取款",
      "parentId": "cls-atm-uuid",
      "parameterIds": [],
      "visibility": "public"
    },
    {
      "id": "op-querybal-uuid",
      "type": "Operation",
      "name": "查询余额",
      "parentId": "cls-db-uuid",
      "parameterIds": ["param-accountid-uuid"],
      "visibility": "public"
    },
    {
      "id": "param-accountid-uuid",
      "type": "Parameter",
      "name": "账户ID",
      "parentId": "op-querybal-uuid",
      "direction": "in",
      "typeId": null,
      "visibility": "public"
    },
    {
      "id": "interaction-withdraw-uuid",
      "type": "Interaction",
      "name": "客户取钱",
      "parentId": "cls-atm-uuid",
      "lifelineIds": [
        "ll-customer-uuid",
        "ll-atm-uuid",
        "ll-db-uuid"
      ],
      "messageIds": [
        "msg-reqwithdraw-uuid",
        "msg-verifybal-uuid",
        "msg-balinfo-uuid",
        "msg-dispense-uuid",
        "msg-insufficient-uuid"
      ],
      "fragmentIds": [
        "fragment-send-reqwithdraw-uuid",
        "fragment-recv-reqwithdraw-uuid",
        "fragment-send-verifybal-uuid",
        "fragment-recv-verifybal-uuid",
        "fragment-send-balinfo-uuid",
        "fragment-recv-balinfo-uuid",
        "cf-balancecheck-alt-uuid",
        "fragment-destroy-db-uuid"
      ],
      "ownedAttributeIds": [
        "prop-interaction-customer-uuid"
      ]
    },
    {
      "id": "ll-customer-uuid",
      "type": "Lifeline",
      "name": "L1",
      "parentId": "interaction-withdraw-uuid",
      "representsId": "prop-interaction-customer-uuid",
      "visibility": "public"
    },
    {
      "id": "ll-atm-uuid",
      "type": "Lifeline",
      "name": "L2",
      "parentId": "interaction-withdraw-uuid",
      "representsId": "prop-atm-instance-uuid",
      "visibility": "public"
    },
    {
      "id": "ll-db-uuid",
      "type": "Lifeline",
      "name": "L3",
      "parentId": "interaction-withdraw-uuid",
      "representsId": "prop-db-connector-uuid",
      "visibility": "public"
    },
    {
      "id": "msg-reqwithdraw-uuid",
      "type": "Message",
      "name": "取款请求",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-reqwithdraw-uuid",
      "receiveEventId": "fragment-recv-reqwithdraw-uuid",
      "messageSort": "synchCall",
      "signatureId": "op-execwithdraw-uuid",
      "arguments": [],
      "visibility": "public"
    },
    {
      "id": "msg-verifybal-uuid",
      "type": "Message",
      "name": "验证余额",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-verifybal-uuid",
      "receiveEventId": "fragment-recv-verifybal-uuid",
      "messageSort": "synchCall",
      "signatureId": "op-querybal-uuid",
      "arguments": [
        {
          "id": "arg-acctid-for-verifybal-uuid",
          "body": "账户ID",
          "language": "natural"
        }
      ],
      "visibility": "public"
    },
    {
      "id": "msg-balinfo-uuid",
      "type": "Message",
      "name": "余额信息",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-balinfo-uuid",
      "receiveEventId": "fragment-recv-balinfo-uuid",
      "messageSort": "reply",
      "visibility": "public"
    },
    {
      "id": "msg-dispense-uuid",
      "type": "Message",
      "name": "出钞",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-dispense-uuid",
      "receiveEventId": "fragment-recv-dispense-uuid",
      "messageSort": "reply",
      "visibility": "public"
    },
    {
      "id": "msg-insufficient-uuid",
      "type": "Message",
      "name": "余额不足",
      "parentId": "interaction-withdraw-uuid",
      "sendEventId": "fragment-send-insufficient-uuid",
      "receiveEventId": "fragment-recv-insufficient-uuid",
      "messageSort": "reply",
      "visibility": "public"
    },
    {
      "id": "fragment-send-reqwithdraw-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-customer-uuid",
      "messageId": "msg-reqwithdraw-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-reqwithdraw-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-reqwithdraw-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-send-verifybal-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-verifybal-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-verifybal-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-db-uuid",
      "messageId": "msg-verifybal-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-send-balinfo-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-db-uuid",
      "messageId": "msg-balinfo-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-balinfo-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-balinfo-uuid",
      "visibility": "public"
    },
    {
      "id": "cf-balancecheck-alt-uuid",
      "type": "CombinedFragment",
      "name": "AltFragment",
      "parentId": "interaction-withdraw-uuid",
      "interactionOperator": "alt",
      "coveredLifelineIds": [
        "ll-atm-uuid",
        "ll-customer-uuid"
      ],
      "operandIds": [
        "operand-sufficient-uuid",
        "operand-insufficient-uuid"
      ],
      "visibility": "public"
    },
    {
      "id": "operand-sufficient-uuid",
      "type": "InteractionOperand",
      "parentId": "cf-balancecheck-alt-uuid",
      "guardId": "guard-sufficient-uuid",
      "fragmentIds": [
        "fragment-send-dispense-uuid",
        "fragment-recv-dispense-uuid"
      ],
      "visibility": "public"
    },
    {
      "id": "operand-insufficient-uuid",
      "type": "InteractionOperand",
      "parentId": "cf-balancecheck-alt-uuid",
      "guardId": null,
      "fragmentIds": [
        "fragment-send-insufficient-uuid",
        "fragment-recv-insufficient-uuid"
      ],
      "visibility": "public"
    },
    {
      "id": "guard-sufficient-uuid",
      "type": "InteractionConstraint",
      "parentId": "operand-sufficient-uuid",
      "specification": {
        "id": "spec-guard-sufficient-uuid",
        "body": "余额充足",
        "language": "natural"
      },
      "visibility": "public"
    },
    {
      "id": "fragment-send-dispense-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-sufficient-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-dispense-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-dispense-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-sufficient-uuid",
      "coveredId": "ll-customer-uuid",
      "messageId": "msg-dispense-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-send-insufficient-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-insufficient-uuid",
      "coveredId": "ll-atm-uuid",
      "messageId": "msg-insufficient-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-recv-insufficient-uuid",
      "type": "MessageOccurrenceSpecification",
      "parentId": "operand-insufficient-uuid",
      "coveredId": "ll-customer-uuid",
      "messageId": "msg-insufficient-uuid",
      "visibility": "public"
    },
    {
      "id": "fragment-destroy-db-uuid",
      "type": "DestructionOccurrenceSpecification",
      "parentId": "interaction-withdraw-uuid",
      "coveredId": "ll-db-uuid",
      "visibility": "public"
    }
  ]
}
"""

if __name__ == '__main__':
    print("--- 正在使用ATM序列图JSON生成XMI ---")
    generated_seq_xmi = json_to_sequence_xmi(actual_atm_interaction_json_str)

    if generated_seq_xmi:
        print("\n--- 生成的序列图 XMI: ---")
        print(generated_seq_xmi)
        try:
            with open("atm_sequence_diagram_generated.xmi", "w", encoding="utf-8") as f:
                f.write(generated_seq_xmi)
            print("\n--- XMI 已保存到 atm_sequence_diagram_generated.xmi ---")
        except Exception as e_save:
            print(f"\n错误：保存XMI到文件时出错: {e_save}")
    else:
        print("--- 序列图 XMI 生成失败 ---")