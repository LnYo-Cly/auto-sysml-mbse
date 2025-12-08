import json
import sys
import os
import xml.etree.ElementTree as ET
from xml.dom import minidom
from collections import defaultdict

# 添加 src 目录到 Python 路径，以便导入 exports 模块
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from exports.repair_orphan_references import repair_json_data

# --- 1. 统一的命名空间定义 ---
NAMESPACES = {
    "xmlns:xmi": "http://www.omg.org/spec/XMI/20131001",
    "xmlns:uml": "http://www.omg.org/spec/UML/20131001",
    "xmlns:sysml": "http://www.omg.org/spec/SysML/20181001/SysML",
    "xmlns:MD_Customization_for_SysML__additional_stereotypes": "http://www.magicdraw.com/spec/Customization/180/SysML",
    "xmlns:DSL_Customization": "http://www.magicdraw.com/schemas/DSL_Customization.xmi",
    "xmlns:StandardProfile": "http://www.omg.org/spec/UML/20131001/StandardProfile",
    "xmlns:MagicDraw_Profile": "http://www.omg.org/spec/UML/20131001/MagicDrawProfile",
    "xmlns:MD_Customization_for_Requirements__additional_stereotypes": "http://www.magicdraw.com/spec/Customization/180/Requirements",
    "xmlns:Validation_Profile": "http://www.magicdraw.com/schemas/Validation_Profile.xmi"
}

PRIMITIVE_HREFS = {
    'Boolean': 'http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.Boolean',
    'Real': 'http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.Real',
    'Integer': 'http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.Integer',
    'String': 'http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.String',
}

# --- 2. 全局数据容器 ---
elements_by_id = {}
children_by_parent_id = defaultdict(list)
xml_elements_by_id = {}
stereotypes_to_apply = defaultdict(list)
associations_to_process = []
pins_by_parent_action = defaultdict(list)
activity_elements = defaultdict(lambda: {"nodes": [], "edges": [], "groups": []})
processed_elements = set()

# --- 3. 辅助函数 ---

def generate_unique_id(base_id, suffix):
    """通用唯一ID生成器"""
    clean_base = str(base_id).replace("-", "_")
    clean_suffix = str(suffix).replace("-", "_")
    return f"_{clean_base}_{clean_suffix}"

def create_element(tag, attrs={}, parent=None, text=None):
    """通用XML元素创建助手"""
    valid_attrs = {k: str(v) for k, v in attrs.items() if v is not None}
    elem = ET.Element(tag, valid_attrs)
    if parent is not None:
        parent.append(elem)
    if text is not None:
        elem.text = str(text)
    return elem

def add_multiplicity(element_data, parent_xml_element):
    """为属性或端口添加多重性上下界"""
    multiplicity = element_data.get('multiplicity')
    if multiplicity is None: return
    
    lower_val, upper_val = '1', '1'
    if isinstance(multiplicity, str) and multiplicity.startswith('[') and multiplicity.endswith(']'):
        parts = multiplicity[1:-1].split('..')
        if len(parts) == 2:
            lower_val, upper_val = parts[0].strip(), parts[1].strip()
        elif len(parts) == 1:
            lower_val = upper_val = parts[0].strip()

    parent_id_key = '{' + NAMESPACES['xmlns:xmi'] + '}id'
    parent_id = parent_xml_element.get('xmi:id')
    create_element('lowerValue', {'xmi:type': 'uml:LiteralInteger', 'xmi:id': f"{parent_id}_lower", 'value': lower_val}, parent=parent_xml_element)
    upper_attrs = {'xmi:id': f"{parent_id}_upper", 'value': upper_val, 'xmi:type': 'uml:LiteralUnlimitedNatural'}
    create_element('upperValue', upper_attrs, parent=parent_xml_element)


def create_behavior_activity_xml(parent_xml_element, behavior_type_tag, behavior_json_data, owner_name=""):
    """为State或Transition创建内联行为(Activity)"""
    if not behavior_json_data or "wrapperActivityId" not in behavior_json_data: return None
    wrapper_activity_id = behavior_json_data["wrapperActivityId"]
    called_behavior_id = behavior_json_data.get("calledBehaviorId")
    activity_name = ""
    if called_behavior_id and called_behavior_id in elements_by_id:
        activity_name = elements_by_id[called_behavior_id].get("name", "")
    activity_attrs = {"xmi:type": "uml:Activity", "xmi:id": wrapper_activity_id, "name": activity_name or None}
    activity_elem = create_element(behavior_type_tag, activity_attrs, parent_xml_element)
    if called_behavior_id:
        initial_node = create_element("node", {"xmi:type": "uml:InitialNode", "xmi:id": f"{wrapper_activity_id}_initial"}, activity_elem)
        cba_attrs = {"xmi:type": "uml:CallBehaviorAction", "xmi:id": f"{wrapper_activity_id}_cba", "behavior": called_behavior_id, "name": f"Call_{activity_name}" if activity_name else None}
        cba_node = create_element("node", cba_attrs, activity_elem)
        final_node = create_element("node", {"xmi:type": "uml:ActivityFinalNode", "xmi:id": f"{wrapper_activity_id}_final"}, activity_elem)
        create_element("edge", {"xmi:type": "uml:ControlFlow", "xmi:id": f"{wrapper_activity_id}_cf1", "source": initial_node.get('xmi:id'), "target": cba_node.get('xmi:id')}, activity_elem)
        create_element("edge", {"xmi:type": "uml:ControlFlow", "xmi:id": f"{wrapper_activity_id}_cf2", "source": cba_node.get('xmi:id'), "target": final_node.get('xmi:id')}, activity_elem)
    return activity_elem

# --- 4. 核心构建逻辑 ---

def process_and_build_activity_content(activity_xml_elem, activity_id):
    """处理活动图内部的节点、边、分区等复杂结构"""
    global elements_by_id, processed_elements
    if activity_id not in activity_elements: return
    content = activity_elements[activity_id]
    
    placeholder_elements = {}
    node_ids, edge_ids, group_ids = content.get("nodes", []), content.get("edges", []), content.get("groups", [])
    
    for edge_id in edge_ids:
        edge_data = elements_by_id.get(edge_id)
        if edge_data:
            placeholder_elements[generate_unique_id(edge_id, "weight")] = {"id": generate_unique_id(edge_id, "weight"), "type": "LiteralUnlimitedNatural", "parentId": edge_id}
            if edge_data.get("guard") and edge_data.get("type") == "ControlFlow":
                guard_id = generate_unique_id(edge_id, "guard-expr")
                placeholder_elements[guard_id] = {"id": guard_id, "type": "OpaqueExpression", "parentId": edge_id, "body": edge_data["guard"]}
                placeholder_elements[generate_unique_id(guard_id, "body")] = {"id": generate_unique_id(guard_id, "body"), "type": "BodyText", "parentId": guard_id}
                placeholder_elements[generate_unique_id(guard_id, "lang")] = {"id": generate_unique_id(guard_id, "lang"), "type": "LanguageSpec", "parentId": guard_id}
    
    for group_id in group_ids:
        placeholder_elements[generate_unique_id(activity_id, group_id + "_partRef")] = {"id": group_id, "type": "PartitionRef", "parentId": activity_id}
        group_data = elements_by_id.get(group_id)
        if group_data:
            for node_edge_id in group_data.get("nodeIds", []):
                node_edge_data = elements_by_id.get(node_edge_id)
                if node_edge_data:
                    ref_type = 'GroupNodeRef' if node_edge_data["type"] not in ["ControlFlow", "ObjectFlow"] else 'GroupEdgeRef'
                    placeholder_elements[generate_unique_id(group_id, node_edge_id + "_grpRef")] = {"id": node_edge_id, "type": ref_type, "parentId": group_id}
    
    activity_partitions_in_activity = [g for g in group_ids if elements_by_id.get(g, {}).get('type') == 'ActivityPartition']
    for node_id in node_ids:
        node_data = elements_by_id.get(node_id)
        if node_data and node_data.get('type') not in ['InputPin', 'OutputPin']:
            for part_id in activity_partitions_in_activity:
                part_data = elements_by_id[part_id]
                if node_id in part_data.get("nodeIds", []):
                    placeholder_elements[generate_unique_id(node_id, "inPartRef")] = {"id": part_id, "type": "InPartitionRef", "parentId": node_id}
                    break
    
    elements_by_id.update(placeholder_elements)
    
    for group_id in group_ids: create_activity_specific_element(activity_xml_elem, generate_unique_id(activity_id, group_id + "_partRef"))
    for edge_id in edge_ids: create_activity_specific_element(activity_xml_elem, edge_id)
    for node_id in node_ids:
        if elements_by_id.get(node_id, {}).get('type') not in ['InputPin', 'OutputPin']:
            create_activity_specific_element(activity_xml_elem, node_id)
    for group_id in group_ids: create_activity_specific_element(activity_xml_elem, group_id)


def create_activity_specific_element(parent_xml_element, elem_id):
    """为活动图创建具体的内部元素（节点、边、引脚等）"""
    if elem_id in processed_elements or elem_id not in elements_by_id: return
    elem_data, elem_type, elem_name = elements_by_id[elem_id], elements_by_id[elem_id]["type"], elements_by_id[elem_id].get("name")
    
    tag_map = {
        "ControlFlow": 'edge', "ObjectFlow": 'edge', "ActivityPartition": 'group', "InputPin": 'argument', "OutputPin": 'result',
        "LiteralUnlimitedNatural": 'weight', "OpaqueExpression": 'guard', "BodyText": 'body', "LanguageSpec": 'language',
        "InPartitionRef": 'inPartition', "PartitionRef": 'partition', "GroupNodeRef": 'node', "GroupEdgeRef": 'edge', "InitialNode": 'node', 
        "ActivityFinalNode": 'node', "FlowFinalNode": 'node', "DecisionNode": 'node', "MergeNode": 'node', "ForkNode": 'node', 
        "JoinNode": 'node', "CallBehaviorAction": 'node', "ActivityParameterNode": 'node', "CentralBufferNode": 'node'
    }
    element_tag = tag_map.get(elem_type)
    if not element_tag: return

    attrs = {}
    if "Ref" in elem_type: attrs["xmi:idref"] = elem_data["id"]
    else:
        attrs["xmi:id"] = elem_id
        if elem_name: attrs["name"] = elem_name
        if elem_type in ["Package", "Activity", "Class", "ControlFlow", "ObjectFlow", "InitialNode", "ActivityFinalNode", "FlowFinalNode", "DecisionNode", "MergeNode", "ForkNode", "JoinNode", "CallBehaviorAction", "ActivityParameterNode", "InputPin", "OutputPin", "ActivityPartition", "LiteralUnlimitedNatural", "OpaqueExpression", "CentralBufferNode"]:
            attrs["xmi:type"] = f"uml:{elem_type}"
        if element_tag in ['edge', 'node', 'argument', 'result', 'group']: attrs["visibility"] = "public"
        if elem_type in ["ControlFlow", "ObjectFlow"]:
            source_id, target_id = elem_data["sourceId"], elem_data["targetId"]
            def get_valid_node_id(ref_id):
                ref_elem = elements_by_id.get(ref_id)
                if ref_elem and ref_elem.get("type") in ["Class", "Block", "InterfaceBlock", "Actor", "Component"]:
                    act_id = parent_xml_element.get('xmi:id') or "unknown_act"
                    proxy_id = f"proxy_{act_id}_{ref_id}"
                    if proxy_id not in processed_elements:
                        node_attrs = {"xmi:type": "uml:CentralBufferNode", "xmi:id": proxy_id, "name": (ref_elem.get("name") or "") + "_Proxy", "type": ref_id, "visibility": "public"}
                        create_element("node", node_attrs, parent_xml_element)
                        processed_elements.add(proxy_id)
                    return proxy_id
                return ref_id
            attrs.update({"source": get_valid_node_id(source_id), "target": get_valid_node_id(target_id)})
        elif elem_type == "CallBehaviorAction" and "behavior" in elem_data: attrs["behavior"] = elem_data["behavior"]
        elif elem_type in ["InputPin", "OutputPin", "ActivityParameterNode", "CentralBufferNode"] and "typeId" in elem_data: attrs["type"] = elem_data["typeId"]
        elif elem_type == "ActivityPartition" and "representsId" in elem_data: attrs["represents"] = elem_data["representsId"]
        elif elem_type == "LiteralUnlimitedNatural": attrs["value"] = "1"
    
    xml_elem = None
    if elem_type == "BodyText": xml_elem = create_element('body', {}, parent_xml_element, text=elements_by_id.get(elem_data.get('parentId'), {}).get('body', ''))
    elif elem_type == "LanguageSpec": xml_elem = create_element('language', {}, parent_xml_element, text="English")
    else: xml_elem = create_element(element_tag, attrs, parent_xml_element)
    
    processed_elements.add(elem_id)
    
    if elem_type in ["ControlFlow", "ObjectFlow"]:
        create_activity_specific_element(xml_elem, generate_unique_id(elem_id, "weight"))
        if elem_data.get("guard") and elem_type == "ControlFlow": create_activity_specific_element(xml_elem, generate_unique_id(elem_id, "guard-expr"))
    elif elem_type == "OpaqueExpression":
        create_activity_specific_element(xml_elem, generate_unique_id(elem_id, "body")); create_activity_specific_element(xml_elem, generate_unique_id(elem_id, "lang"))
    elif elem_type == "CallBehaviorAction":
        for pin_id in pins_by_parent_action.get(elem_id, []): create_activity_specific_element(xml_elem, pin_id)
    elif element_tag == 'node' and "Ref" not in elem_type: create_activity_specific_element(xml_elem, generate_unique_id(elem_id, "inPartRef"))
    elif elem_type == "ActivityPartition":
        for node_edge_id in elem_data.get("nodeIds", []):
            if elements_by_id.get(node_edge_id, {}).get("type") not in ['ForkNode', 'JoinNode']:
                create_activity_specific_element(xml_elem, generate_unique_id(elem_id, node_edge_id + "_grpRef"))

def build_element_tree(parent_id, parent_xml_element):
    """统一的递归构建函数，用于创建所有类型的UML元素"""
    if parent_id not in children_by_parent_id: return

    for elem_id in children_by_parent_id[parent_id]:
        if elem_id in processed_elements: continue
        
        elem_data = elements_by_id[elem_id]
        elem_type = elem_data['type']
        elem_name = elem_data.get('name')
        
        xml_elem = None
        
        parent_type = elements_by_id.get(elem_data.get("parentId"), {}).get("type")

        if elem_type in ["Property", "FullPort", "ProxyPort", "FlowPort", "ConstraintParameter"]:
            tag = "ownedAttribute"
            if parent_type in ["Class", "Block", "Interaction"]: tag = "ownedAttribute"
            elif parent_type == "Association": tag = "ownedEnd"
            
            attrs = {
                'xmi:id': elem_id, 'name': elem_name,
                'xmi:type': 'uml:Port' if "Port" in elem_type or elem_type == "ConstraintParameter" else 'uml:Property',
                'visibility': elem_data.get('visibility', 'public'),
                'aggregation': 'composite' if "Port" in elem_type else elem_data.get('aggregation', 'none'),
                'association': elem_data.get('associationId')
            }
            xml_elem = create_element(tag, attrs, parent_xml_element)
            type_id = elem_data.get('typeId')
            if type_id:
                href = PRIMITIVE_HREFS.get(type_id)
                if href: create_element('type', {'href': href}, xml_elem)
                else: xml_elem.set('type', type_id)
            add_multiplicity(elem_data, xml_elem)

        elif elem_type == "Operation":
            attrs = {'xmi:id': elem_id, 'name': elem_name, 'xmi:type': 'uml:Operation'}
            xml_elem = create_element("ownedOperation", attrs, parent_xml_element)
        
        elif elem_type == "Parameter":
             attrs = {
                 'xmi:id': elem_id, 'name': elem_name,
                 'xmi:type': 'uml:Parameter', 'direction': elem_data.get('direction', 'in')
             }
             xml_elem = create_element("ownedParameter", attrs, parent_xml_element)
             if elem_data.get("typeId"):
                type_node = create_element("type", {}, parent=xml_elem)
                type_node.set("xmi:idref", elem_data["typeId"])
            
            # if elem_data.get("typeId"): type_node.set("xmi:idref", elem_data["typeId"])
             #elif elem_data.get("typeHref"): type_node.set("href", #elem_data["typeHref"])

        elif elem_type == "EnumerationLiteral":
            attrs = {'xmi:id': elem_id, 'name': elem_name, 'xmi:type': 'uml:EnumerationLiteral'}
            xml_elem = create_element("ownedLiteral", attrs, parent_xml_element)
        
        elif elem_type == "Generalization":
            source_element = xml_elements_by_id.get(parent_id)
            if source_element:
                attrs = {
                    'xmi:id': elem_id, 'name': elem_name,
                    'xmi:type': 'uml:Generalization', 'general': elem_data.get('targetId')
                }
                xml_elem = create_element('generalization', attrs, source_element)

        elif elem_type == 'Include':
            source_element = xml_elements_by_id.get(elem_data.get('sourceId'))
            if source_element:
                attrs = {
                    'xmi:id': elem_id, 'name': elem_name,
                    'xmi:type': 'uml:Include', 'addition': elem_data.get('targetId')
                }
                xml_elem = create_element('include', attrs, source_element)

        elif elem_type == 'Extend':
            source_element = xml_elements_by_id.get(elem_data.get('sourceId'))
            if source_element:
                attrs = {
                    'xmi:id': elem_id, 'name': elem_name,
                    'xmi:type': 'uml:Extend', 'extendedCase': elem_data.get('targetId')
                }
                xml_elem = create_element('extend', attrs, source_element)

        elif elem_type in ["AssemblyConnector", "BindingConnector"]:
            attrs = {'xmi:id': elem_id, 'name': elem_name, 'xmi:type': 'uml:Connector'}
            xml_elem = create_element("ownedConnector", attrs, parent_xml_element)
            ends = elem_data.get('end', [])
            if 'end1' in elem_data: ends = [elem_data['end1'], elem_data['end2']]
            for i, end_data in enumerate(ends):
                end_id = end_data.get('id') or generate_unique_id(elem_id, f"end_{i+1}")
                end_attrs = {'xmi:id': end_id, 'xmi:type': 'uml:ConnectorEnd'}
                end_attrs['role'] = end_data.get('portRefId') or end_data.get('propertyRefId')
                end_attrs['partWithPort'] = end_data.get('partRefId')
                create_element('end', end_attrs, parent=xml_elem)
        
        elif elem_type == "Interaction":
            tag = "ownedBehavior" if parent_type in ["Class", "Block", "Operation"] else "packagedElement"
            attrs = {'xmi:id': elem_id, 'name': elem_name, 'xmi:type': 'uml:Interaction'}
            xml_elem = create_element(tag, attrs, parent_xml_element)
        
        elif elem_type == "Lifeline":
            attrs = {
                'xmi:id': elem_id, 'name': elem_name,
                'xmi:type': 'uml:Lifeline', 'represents': elem_data.get('representsId')
            }
            xml_elem = create_element("lifeline", attrs, parent_xml_element)
            
        elif elem_type == "Message":
            attrs = {
                'xmi:id': elem_id, 'name': elem_name, 'xmi:type': 'uml:Message',
                'sendEvent': elem_data.get('sendEventId'),
                'receiveEvent': elem_data.get('receiveEventId'),
                'signature': elem_data.get('signatureId'),
                'messageSort': elem_data.get('messageSort')
            }
            xml_elem = create_element("message", attrs, parent_xml_element)
            for arg_data in elem_data.get("arguments", []):
                arg_attrs = {"xmi:type": "uml:OpaqueExpression", "xmi:id": arg_data.get("id")}
                arg_elem = create_element("argument", arg_attrs, parent=xml_elem)
                if "body" in arg_data: create_element("body", text=str(arg_data["body"]), parent=arg_elem)
                if "language" in arg_data: create_element("language", text=arg_data["language"], parent=arg_elem)

        elif elem_type in ["MessageOccurrenceSpecification", "DestructionOccurrenceSpecification", "CombinedFragment"]:
            attrs = {
                'xmi:id': elem_id, 'name': elem_name,
                'xmi:type': f"uml:{elem_type}"
            }
            if elem_type in ["MessageOccurrenceSpecification", "DestructionOccurrenceSpecification"]:
                attrs['covered'] = elem_data.get("coveredId")
                if elem_type == "MessageOccurrenceSpecification":
                    attrs['message'] = elem_data.get("messageId")
            elif elem_type == "CombinedFragment":
                attrs['interactionOperator'] = elem_data.get("interactionOperator")
            
            xml_elem = create_element("fragment", attrs, parent_xml_element)
            if elem_type == "CombinedFragment":
                for ll_id in elem_data.get("coveredLifelineIds", []):
                    create_element("covered", {"xmi:idref": ll_id}, parent=xml_elem)

        elif elem_type == "InteractionOperand":
            attrs = {'xmi:id': elem_id, 'name': elem_name, 'xmi:type': 'uml:InteractionOperand'}
            xml_elem = create_element("operand", attrs, parent_xml_element)

        elif elem_type == "InteractionConstraint":
            attrs = {'xmi:id': elem_id, 'name': elem_name, 'xmi:type': 'uml:InteractionConstraint'}
            xml_elem = create_element("guard", attrs, parent_xml_element)
            spec_data = elem_data.get("specification")
            if isinstance(spec_data, dict):
                spec_attrs = {"xmi:type": "uml:OpaqueExpression", "xmi:id": spec_data.get("id")}
                spec_elem = create_element("specification", spec_attrs, parent=xml_elem)
                if "body" in spec_data: create_element("body", text=spec_data["body"], parent=spec_elem)
                if "language" in spec_data: create_element("language", text=spec_data["language"], parent=spec_elem)
        
        else:
            base_attrs = {'xmi:id': elem_id, 'name': elem_name}
            
            packaged_element_types = {
                "Package": "uml:Package", "Block": "uml:Class", "InterfaceBlock": "uml:Class", "Class": "uml:Class",
                "Requirement": "uml:Class", "ConstraintBlock": "uml:Class", "ValueType": "uml:DataType",
                "Enumeration": "uml:Enumeration", "Signal": "uml:Signal", "SignalEvent": "uml:SignalEvent",
                "Event": "uml:ReceiveEvent", "Actor": "uml:Actor", "UseCase": "uml:UseCase"
            }
            if elem_type in packaged_element_types:
                base_attrs['xmi:type'] = packaged_element_types[elem_type]
                if elem_type == "SignalEvent":
                    base_attrs['signal'] = elem_data.get('signalId')
                
                classifier_behavior_id = elem_data.get('classifierBehaviorId') if elem_type in ["Block", "Class"] else None

                xml_elem = create_element("packagedElement", base_attrs, parent_xml_element)
                
                if classifier_behavior_id:
                    create_element('classifierBehavior', {'xmi:idref': classifier_behavior_id}, xml_elem)

                if elem_type == "ConstraintBlock" and 'specification' in elem_data:
                    spec_data = elem_data['specification']
                    rule = create_element('ownedRule', {'xmi:type': 'uml:Constraint', 'xmi:id': f"{elem_id}_cr"}, xml_elem)
                    spec = create_element('specification', {'xmi:type': 'uml:OpaqueExpression', 'xmi:id': f"{elem_id}_spec"}, rule)
                    create_element('body', text=spec_data.get('expression', ''), parent=spec); create_element('language', text=spec_data.get('language', ''), parent=spec)

            elif elem_type == "Activity" or elem_type == "TestCase":
                base_attrs['xmi:type'] = 'uml:Activity'
                xml_elem = create_element("packagedElement", base_attrs, parent_xml_element)
                if elem_type == "Activity": process_and_build_activity_content(xml_elem, elem_id)
                elif elem_type == "TestCase":
                    param_id = generate_unique_id(elem_id, "verdict_param")
                    param = create_element("ownedParameter", {"xmi:type": "uml:Parameter", "xmi:id": param_id, "name": "verdict", "direction": "return"}, xml_elem)
                    create_element("type", {"href": "http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.VerdictKind"}, param)
                    node = create_element("node", {"xmi:type": "uml:ActivityParameterNode", "xmi:id": generate_unique_id(elem_id, "verdict_node"), "name": "verdict", "parameter": param_id}, xml_elem)
                    create_element("type", {"href": "http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.VerdictKind"}, node)

            elif elem_type in ['DeriveReqt', 'Satisfy', 'Verify']:
                base_attrs['xmi:type'] = 'uml:Abstraction'
                xml_elem = create_element("packagedElement", base_attrs, parent_xml_element)
                client_supplier_map = {'DeriveReqt': ('derivedRequirementId', 'sourceRequirementId'), 'Satisfy': ('blockId', 'requirementId'), 'Verify': ('testCaseId', 'requirementId')}
                client_key, supplier_key = client_supplier_map[elem_type]
                if elem_data.get(client_key): create_element('client', {'xmi:idref': elem_data[client_key]}, xml_elem)
                if elem_data.get(supplier_key): create_element('supplier', {'xmi:idref': elem_data[supplier_key]}, xml_elem)

            elif elem_type == "StateMachine":
                tag = 'ownedBehavior' if parent_type == "Block" else 'packagedElement'
                base_attrs['xmi:type'] = 'uml:StateMachine'
                xml_elem = create_element(tag, base_attrs, parent_xml_element)
            elif elem_type == "Region":
                base_attrs['xmi:type'] = 'uml:Region'
                xml_elem = create_element('region', base_attrs, parent_xml_element)
            elif elem_type == "State":
                base_attrs['xmi:type'] = 'uml:State'
                xml_elem = create_element('subvertex', base_attrs, parent_xml_element)
                owner_name = elem_name or elem_id
                if "entry" in elem_data: create_behavior_activity_xml(xml_elem, "entry", elem_data["entry"], owner_name)
                if "exit" in elem_data: create_behavior_activity_xml(xml_elem, "exit", elem_data["exit"], owner_name)
                if "doActivity" in elem_data: create_behavior_activity_xml(xml_elem, "doActivity", elem_data["doActivity"], owner_name)
            elif elem_type == "Pseudostate":
                kind = elem_data.get("kind", "")
                base_attrs['xmi:type'] = "uml:FinalState" if kind in ["finalState", "final"] else "uml:Pseudostate"
                if kind not in ["initial", "finalState", "final"]: base_attrs["kind"] = kind
                xml_elem = create_element('subvertex', base_attrs, parent_xml_element)
            elif elem_type == "Transition":
                base_attrs.update({'xmi:type': 'uml:Transition', 'source': elem_data['sourceId'], 'target': elem_data['targetId']})
                xml_elem = create_element('transition', base_attrs, parent_xml_element)
                if "triggerIds" in elem_data:
                    for i, event_id in enumerate(elem_data["triggerIds"]):
                        trigger = create_element('trigger', {'xmi:type': 'uml:Trigger', 'xmi:id': f"{elem_id}_tr_{i}"}, xml_elem); create_element('event', {'xmi:idref': event_id}, trigger)
                if 'guard' in elem_data and 'expression' in elem_data['guard']:
                    guard_data = elem_data['guard']
                    guard = create_element('guard', {'xmi:type': 'uml:Constraint', 'xmi:id': f"{elem_id}_guard"}, xml_elem)
                    spec = create_element('specification', {'xmi:type': 'uml:OpaqueExpression', 'xmi:id': f"{elem_id}_guard_spec"}, guard)
                    create_element('body', text=guard_data['expression'], parent=spec); create_element('language', text=guard_data['language'], parent=spec)
                if "effect" in elem_data: create_behavior_activity_xml(xml_elem, "effect", elem_data["effect"], elem_name or elem_id)
        
        if xml_elem is not None:
            processed_elements.add(elem_id)
            xml_elements_by_id[elem_id] = xml_elem
            build_element_tree(elem_id, xml_elem)

def process_associations():
    """后处理步骤: 验证并创建所有类型的Association。"""
    for assoc_data in associations_to_process:
        assoc_id = assoc_data['id']
        parent_id = assoc_data.get('parentId')
        parent_xml = xml_elements_by_id.get(parent_id)
        if not parent_xml:
            print(f"警告: 关联 '{assoc_id}' 的父级ID '{parent_id}' 未找到，已跳过。")
            continue

        if 'memberEndIds' in assoc_data:
            valid_ends = [end_id for end_id in assoc_data['memberEndIds'] if end_id in xml_elements_by_id]
            if len(valid_ends) >= 2:
                assoc_attrs = {'xmi:id': assoc_id, 'xmi:type': 'uml:Association', 'name': assoc_data.get('name')}
                assoc_xml = create_element('packagedElement', assoc_attrs, parent=parent_xml)
                for end_id in valid_ends:
                    create_element('memberEnd', {'xmi:idref': end_id}, parent=assoc_xml)
                for end_id in assoc_data.get("navigableOwnedEndIds", []):
                    create_element("navigableOwnedEnd", {"xmi:idref": end_id}, parent=assoc_xml)
                processed_elements.add(assoc_id)
                xml_elements_by_id[assoc_id] = assoc_xml
            else:
                print(f"警告: 关联 '{assoc_id}' 因成员端不完整而被跳过。")
        
        elif 'sourceId' in assoc_data and 'targetId' in assoc_data:
            source_id = assoc_data['sourceId']
            target_id = assoc_data['targetId']
            if source_id in xml_elements_by_id and target_id in xml_elements_by_id:
                assoc_attrs = {'xmi:id': assoc_id, 'xmi:type': 'uml:Association', 'name': assoc_data.get('name')}
                assoc_xml = create_element('packagedElement', assoc_attrs, parent=parent_xml)
                
                end1_id = generate_unique_id(assoc_id, "source_end")
                end2_id = generate_unique_id(assoc_id, "target_end")
                
                create_element('ownedEnd', {'xmi:type': 'uml:Property', 'xmi:id': end1_id, 'type': source_id, 'association': assoc_id}, parent=assoc_xml)
                create_element('ownedEnd', {'xmi:type': 'uml:Property', 'xmi:id': end2_id, 'type': target_id, 'association': assoc_id}, parent=assoc_xml)
                
                create_element('memberEnd', {'xmi:idref': end1_id}, parent=assoc_xml)
                create_element('memberEnd', {'xmi:idref': end2_id}, parent=assoc_xml)
                
                processed_elements.add(assoc_id)
                xml_elements_by_id[assoc_id] = assoc_xml
            else:
                 print(f"警告: 关联 '{assoc_id}' 因源或目标元素未找到而被跳过。")

# --- 已修复 ---
def process_sequence_covered_by():
    """后处理步骤: 为Lifeline添加coveredBy引用。"""
    for elem_id, elem_data in elements_by_id.items():
        elem_type = elem_data.get("type")
        if elem_type in ["MessageOccurrenceSpecification", "DestructionOccurrenceSpecification", "CombinedFragment"]:
            
            lifeline_ids_to_cover = []
            if "coveredId" in elem_data:
                lifeline_ids_to_cover.append(elem_data["coveredId"])
            elif "coveredLifelineIds" in elem_data:
                lifeline_ids_to_cover.extend(elem_data["coveredLifelineIds"])
            
            for ll_id in lifeline_ids_to_cover:
                lifeline_xml_node = xml_elements_by_id.get(ll_id)
                if lifeline_xml_node is not None:
                    # 使用 'xmi:type' 字符串字面量来获取属性，与创建时保持一致
                    if lifeline_xml_node.tag == "lifeline" and lifeline_xml_node.get('xmi:type') == "uml:Lifeline":
                         create_element("coveredBy", {"xmi:idref": elem_id}, parent=lifeline_xml_node)
                    else:
                         print(f"警告: 为 fragment '{elem_id}' 添加 coveredBy 时, 找到的元素 '{ll_id}' 不是一个 lifeline。 Tag: {lifeline_xml_node.tag}, Type: {lifeline_xml_node.get('xmi:type')}")
                else:
                    print(f"警告: 为 fragment '{elem_id}' 添加 coveredBy 时未找到 lifeline '{ll_id}' 的XML节点。")

def validate_and_clean_model(root_element):
    """
    全面检查并清理无效引用的元素。
    采用迭代方式，直到没有新元素被删除为止（处理级联删除）。
    """
    max_passes = 10
    print("开始执行模型完整性检查与清理...")
    
    for pass_num in range(max_passes):
        removed_count = 0
        existing_ids = set()
        # 1. 收集当前所有ID
        for elem in root_element.iter():
            xmi_id = elem.get('xmi:id')
            if xmi_id:
                existing_ids.add(xmi_id)
        
        # 1.1 收集所有被边引用的节点ID（source/target）
        referenced_node_ids = set()
        for elem in root_element.iter():
            if elem.tag == 'edge':
                s, t = elem.get('source'), elem.get('target')
                if s: referenced_node_ids.add(s)
                if t: referenced_node_ids.add(t)
        
        elements_to_remove = []
        
        # 2. 检查引用完整性
        for parent in root_element.iter():
            for child in list(parent):
                should_remove = False
                reason = ""
                
                # 2.0 检查孤立的 CentralBufferNode（没有被任何边引用）
                if child.tag == 'node' and child.get('xmi:type') == 'uml:CentralBufferNode':
                    node_id = child.get('xmi:id')
                    if node_id and node_id not in referenced_node_ids:
                        should_remove, reason = True, f"CentralBufferNode '{node_id}' not referenced by any edge"
                
                # 2.1 检查 source/target (Edge, Transition)
                if child.tag in ['edge', 'transition']:
                    s, t = child.get('source'), child.get('target')
                    if s and s not in existing_ids: should_remove, reason = True, f"source '{s}' missing"
                    elif t and t not in existing_ids: should_remove, reason = True, f"target '{t}' missing"
                
                # 2.2 检查 message (MessageOccurrenceSpecification)
                elif child.get('xmi:type') == 'uml:MessageOccurrenceSpecification':
                    m = child.get('message')
                    if m and m not in existing_ids: should_remove, reason = True, f"message '{m}' missing"
                    
                    c = child.get('covered')
                    if c and c not in existing_ids: should_remove, reason = True, f"covered lifeline '{c}' missing"

                # 2.3 检查 sendEvent/receiveEvent (Message)
                elif child.tag == 'message':
                    se, re = child.get('sendEvent'), child.get('receiveEvent')
                    if se and se not in existing_ids: should_remove, reason = True, f"sendEvent '{se}' missing"
                    elif re and re not in existing_ids: should_remove, reason = True, f"receiveEvent '{re}' missing"
                
                # 2.4 检查 coveredBy (Lifeline children)
                elif child.tag == 'coveredBy':
                    ref = child.get('xmi:idref')
                    if ref and ref not in existing_ids: should_remove, reason = True, f"coveredBy ref '{ref}' missing"

                # 2.5 检查 Stereotypes (base_X)
                else:
                    for attr, val in child.attrib.items():
                        if attr.startswith('base_') and val not in existing_ids:
                            should_remove, reason = True, f"base element '{val}' for stereotype missing"
                            break
                    
                    # 2.6 检查 Abstraction (client/supplier)
                    if not should_remove and child.get('xmi:type') == 'uml:Abstraction':
                        for sub in child:
                            if sub.tag in ['client', 'supplier']:
                                ref = sub.get('xmi:idref')
                                if ref and ref not in existing_ids:
                                    should_remove, reason = True, f"abstraction {sub.tag} '{ref}' missing"
                                    break
                
                if should_remove:
                    elements_to_remove.append((parent, child, reason))
        
        if not elements_to_remove:
            break
            
        # 3. 执行删除
        for parent, child, reason in elements_to_remove:
            try:
                parent.remove(child)
                removed_count += 1
            except ValueError:
                pass 
        
        print(f"  清理轮次 {pass_num+1}: 移除了 {removed_count} 个无效元素。")

def apply_stereotypes(root_element):
    """在XML根级别应用所有收集到的构造型"""
    stereotype_map = {
        "Requirement": ("sysml:Requirement", "base_Class"), "Block": ("sysml:Block", "base_Class"), "InterfaceBlock": ("sysml:InterfaceBlock", "base_Class"),
        "ConstraintBlock": ("sysml:ConstraintBlock", "base_Class"), "ValueType": ("sysml:ValueType", "base_DataType"), "TestCase": ("sysml:TestCase", "base_Behavior"),
        "FullPort": ("sysml:FullPort", "base_Port"), "ProxyPort": ("sysml:ProxyPort", "base_Port"), "FlowPort": ("sysml:FlowPort", "base_Port"),
        "ValueProperty": ("MD_Customization_for_SysML__additional_stereotypes:ValueProperty", "base_Property"),
        "ConstraintProperty": ("MD_Customization_for_SysML__additional_stereotypes:ConstraintProperty", "base_Property"),
        "ConstraintParameter": ("MD_Customization_for_SysML__additional_stereotypes:ConstraintParameter", "base_Port"),
        "BindingConnector": ("sysml:BindingConnector", "base_Connector"), "AllocateActivityPartition": ("sysml:AllocateActivityPartition", "base_ActivityPartition"),
        "DeriveReqt": ("sysml:DeriveReqt", "base_Abstraction"), "Satisfy": ("sysml:Satisfy", "base_Abstraction"), "Verify": ("sysml:Verify", "base_Abstraction"),
        "PartProperty": ("MD_Customization_for_SysML__additional_stereotypes:PartProperty", "base_Property"),
    }
    for stereotype_type, elements in stereotypes_to_apply.items():
        for elem_data in elements:
            elem_id = elem_data['id']
            if elem_id not in processed_elements: continue
            
            if stereotype_type in stereotype_map:
                tag, base_attr = stereotype_map[stereotype_type]
                attrs = {'xmi:id': generate_unique_id(elem_id, "app"), base_attr: elem_id}
                if stereotype_type == "Requirement":
                    attrs["Id"] = elem_data.get("reqId", "")
                    attrs["Text"] = elem_data.get("text", "")
                create_element(tag, attrs, root_element)

# --- 5. 主流程编排函数 ---

def generate_unified_xmi(json_data):
    """主函数，接收JSON数据，生成统一的、包含所有图类型的SysML/UML XMI"""
    global elements_by_id, children_by_parent_id, xml_elements_by_id, stereotypes_to_apply, associations_to_process, pins_by_parent_action, activity_elements, processed_elements
    elements_by_id.clear(); xml_elements_by_id.clear(); stereotypes_to_apply.clear();
    pins_by_parent_action.clear(); activity_elements.clear(); children_by_parent_id.clear()
    associations_to_process.clear(); processed_elements.clear()
    
    if isinstance(json_data, str):
        try:
            json_data = json.loads(json_data)
        except json.JSONDecodeError as e:
            print(f"错误: JSON 解析失败 - {e}")
            return None
            
    if "elements" not in json_data:
        print("错误: JSON数据缺少 'elements' 键。")
        return None
    
    model_data = json_data.get("model", [{}])[0]
    model_id = model_data.get("id", "model-root-uuid")
    elements_by_id = {elem["id"]: elem for elem in json_data.get("elements", [])}
    
    top_level_types = [
        'Package', 'Block', 'Class', 'Activity', 'Requirement', 'TestCase', 'DeriveReqt', 'Satisfy', 'Verify', 
        'Association', 'InterfaceBlock', 'ValueType', 'Enumeration', 'Signal', 'Actor', 'UseCase',
        'Interaction', 'SignalEvent', 'Event'
    ]
    
    for elem_id, elem_data in elements_by_id.items():
        elem_type = elem_data.get('type', '')
        parent_id = elem_data.get('parentId')
        
        if elem_type in ['Generalization', 'Include', 'Extend']:
            parent_id = elem_data.get('sourceId')
        
        if parent_id is None and elem_type in top_level_types:
            parent_id = model_id
        
        if parent_id:
            children_by_parent_id[parent_id].append(elem_id)

        stereotype_key_map = {
            "Block": "Block", "InterfaceBlock": "InterfaceBlock", "ConstraintBlock": "ConstraintBlock", "ValueType": "ValueType",
            "BindingConnector": "BindingConnector", "ActivityPartition": "AllocateActivityPartition", "Requirement": "Requirement",
            "TestCase": "TestCase", "DeriveReqt": "DeriveReqt", "Satisfy": "Satisfy", "Verify": "Verify",
            "FullPort": "FullPort", "ProxyPort": "ProxyPort", "FlowPort": "FlowPort", "Actor": "Actor", "UseCase": "UseCase"
        }
        if elem_type in stereotype_key_map: stereotypes_to_apply[stereotype_key_map[elem_type]].append(elem_data)
        elif elem_type == "Property" and 'propertyKind' in elem_data:
            kind = elem_data['propertyKind']
            if kind == 'value': stereotypes_to_apply["ValueProperty"].append(elem_data)
            elif kind == 'constraint': stereotypes_to_apply["ConstraintProperty"].append(elem_data)
            elif kind == 'part': stereotypes_to_apply["PartProperty"].append(elem_data)
        elif elem_type == "ConstraintParameter": stereotypes_to_apply["ConstraintParameter"].append(elem_data)
        
        if elem_type == "Association": associations_to_process.append(elem_data)
        
        parent_of_activity_content = elements_by_id.get(elem_data.get('parentId'), {})
        if parent_of_activity_content and parent_of_activity_content.get('type') == 'Activity':
            act_id = elem_data['parentId']
            if elem_type in ["InitialNode", "ActivityFinalNode", "FlowFinalNode", "DecisionNode", "MergeNode", "ForkNode", "JoinNode", "CallBehaviorAction", "ActivityParameterNode", "CentralBufferNode"]: activity_elements[act_id]["nodes"].append(elem_id)
            elif elem_type in ["ControlFlow", "ObjectFlow"]: activity_elements[act_id]["edges"].append(elem_id)
            elif elem_type == "ActivityPartition": activity_elements[act_id]["groups"].append(elem_id)
        if elem_type in ["InputPin", "OutputPin"]: pins_by_parent_action[elem_data.get('parentId')].append(elem_id)

    # 2. 初始化XML
    xmi_root = ET.Element("xmi:XMI", {"xmi:version": "2.5"})
    for prefix, uri in NAMESPACES.items():
        xmi_root.set(prefix, uri)
    
    # 3. 创建模型及ProfileApplication
    model_attrs = {'xmi:type': 'uml:Model', 'xmi:id': model_id, 'name': model_data.get('name', 'DefaultUnifiedModel')}
    model_element = create_element('uml:Model', model_attrs, xmi_root)
    xml_elements_by_id[model_id] = model_element
    processed_elements.add(model_id)
    
    pa_elem = create_element("profileApplication", {"xmi:type": "uml:ProfileApplication", "xmi:id": "SysML_Profile_App"}, model_element)
    create_element("appliedProfile", {"href": "http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML"}, pa_elem)

    # 4. 递归构建UML树
    build_element_tree(model_id, model_element)
    
    # 5. 后处理
    process_associations()
    process_sequence_covered_by()
    
    # 5.5 验证并清理无效的边连接
    validate_and_clean_model(xmi_root)
    
    # 6. 应用所有构造型
    apply_stereotypes(xmi_root)
    
    # 6.5 再次清理（因为构造型可能引用了已删除的元素，或者构造型本身无效）
    validate_and_clean_model(xmi_root)

    # 7. 美化并返回XML字符串
    rough_string = ET.tostring(xmi_root, encoding='unicode', method='xml')
    reparsed = minidom.parseString(rough_string)
    pretty_xml = reparsed.toprettyxml(indent="  ")
    xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if pretty_xml.startswith('<?xml'): pretty_xml = pretty_xml.split('\n', 1)[1]
    return xml_declaration + pretty_xml

# --- Main Execution Block ---
if __name__ == "__main__":
    json_file_path = '../data/output/fusion/fused_model_20251128_114546.json'
    output_xmi_file_path = 'output.xmi'

    try:
        # 步骤 1: 读取JSON文件
        with open(json_file_path, 'r', encoding='utf-8') as f:
            print(f"正在读取JSON文件: {json_file_path}")
            json_data = json.load(f)
        # 使用智能修复工具清理数据（规则修复 + 删除无法修复的节点）
        cleaned_data = repair_json_data(json_data, verbose=True)

        # 步骤 2: 调用主生成函数
        print("JSON文件读取成功，开始生成XMI...")
        unified_xmi_output = generate_unified_xmi(cleaned_data)
        
        # 步骤 3: 打印结果到控制台
        print("\n--- 生成的 XMI 内容 ---")
        print(unified_xmi_output)
        print("------------------------\n")

        # 步骤 4: 将结果写入输出文件
        with open(output_xmi_file_path, 'w', encoding='utf-8') as f:
            f.write(unified_xmi_output)
        print(f"XMI内容已成功写入文件: {output_xmi_file_path}")

    except FileNotFoundError:
        print(f"错误: JSON文件未找到。请确保 '{json_file_path}' 文件存在于当前目录。")
    except json.JSONDecodeError as e:
        print(f"错误: 解析JSON文件失败。请检查文件格式是否正确。错误详情: {e}")
    except KeyError as e:
        print(f"错误: JSON数据缺少必须的键。请检查 'model' 或 'elements' 字段是否存在。错误详情: {e.with_traceback()}")
    except Exception as e:
        print(f"发生未知错误: {e.with_traceback()}")