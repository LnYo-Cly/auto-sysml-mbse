import xml.etree.ElementTree as ET
import xml.dom.minidom
import uuid
import json # 引入json库

# --- 全局定义 ---
namespaces = {
    'xmi': 'http://www.omg.org/XMI',
    'uml': 'http://www.omg.org/spec/UML/20090901',
    'sysml': 'http://www.omg.org/spec/SysML/20131001',
    'MD_Customization_for_SysML__additional_stereotypes': 'http://www.magicdraw.com/spec/Customization/190/SysML',
    'diagram': 'http://www.nomagic.com/ns/magicdraw/core/diagram/1.0'
}

primitive_hrefs = {
    'Boolean': 'http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.Boolean',
    'Real': 'http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.Real',
    'Integer': 'http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.Integer',
    'String': 'http://www.omg.org/spec/SysML/20181001/SysML.xmi#SysML_dataType.String',
}

# --- 辅助函数 ---
def register_namespaces():
    for prefix, uri in namespaces.items():
        ET.register_namespace(prefix, uri)

def create_element(tag, attrs, parent=None, text=None):
    # 此函数现在仅用于简化元素创建，不再处理命名空间前缀
    elem = ET.Element(tag, {k: str(v) for k, v in attrs.items() if v is not None})
    if parent is not None:
        parent.append(elem)
    if text is not None:
        elem.text = str(text)
    return elem

def add_multiplicity(element_data, parent_xml_element):
    multiplicity = element_data.get('multiplicity')
    if multiplicity is None: return
    # 默认值
    lower_val, upper_val = '1', '1'
    if isinstance(multiplicity, str) and multiplicity.startswith('[') and multiplicity.endswith(']'):
        parts = multiplicity[1:-1].split('..')
        if len(parts) == 2:
            lower_val, upper_val = parts[0].strip(), parts[1].strip()
        elif len(parts) == 1:
            lower_val = upper_val = parts[0].strip()

    create_element('lowerValue', {'xmi:type': 'uml:LiteralInteger', 'xmi:id': f"{parent_xml_element.get('{http://www.omg.org/XMI}id')}_lower", 'value': lower_val}, parent=parent_xml_element)
    upper_attrs = {'xmi:id': f"{parent_xml_element.get('{http://www.omg.org/XMI}id')}_upper", 'value': upper_val}
    upper_attrs['xmi:type'] = 'uml:LiteralUnlimitedNatural'
    create_element('upperValue', upper_attrs, parent=parent_xml_element)

def get_ownership_tag(elem_type, parent_type):
    """根据子元素和父元素的类型决定UML所有权标签"""
    if elem_type in ["Property", "FullPort", "ProxyPort", "FlowPort"]:
        return "ownedAttribute"
    if elem_type in ["AssemblyConnector", "BindingConnector"]:
        return "ownedConnector"
    if elem_type == "Operation":
        return "ownedOperation"
    if elem_type == "Reception":
        return "ownedReception"
    if elem_type == "EnumerationLiteral":
        return "ownedLiteral"
    if elem_type == "Generalization":
        return "generalization"
    # 默认是 packagedElement
    return "packagedElement"

def add_diagram_extension(diagram_data, owner_xml_element, root_xml):
    """将图表信息作为XMI扩展添加到其所有者"""
    diagram_id = diagram_data['id']
    diagram_type_map = {
        'BDD': ('SysML Block Definition Diagram', 'Class Diagram'),
        'IBD': ('SysML Internal Block Diagram', 'Composite Structure Diagram')
    }
    sysml_type, uml_type = diagram_type_map.get(diagram_data['diagramType'], ('', ''))

    # MagicDraw/Cameo需要一个uml:Diagram元素，通常放在xmi:Extension中
    extension = create_element('xmi:Extension', {'extender': 'MagicDraw UML 2021x'}, parent=owner_xml_element)
    
    # 为 create_element 调用添加 attrs={}
    model_ext = create_element('modelExtension', {}, parent=extension)
    
    diagram_attrs = {
        'xmi:type': 'uml:Diagram',
        'xmi:id': diagram_id,
        'name': diagram_data['name'],
    }
    # contextId 可能是父元素ID，也可能是其他
    if 'contextId' in diagram_data:
        diagram_attrs['ownerOfDiagram'] = diagram_data['contextId']

    create_element('ownedDiagram', diagram_attrs, parent=model_ext)
    # 更多关于diagram representation的细节可以按需添加

# --- 主生成函数 ---
def generate_sysml_xml(json_data):
    register_namespaces()
    
    # 根元素 <xmi:XMI>
    root = ET.Element('xmi:XMI', {'xmi:version': '2.5'})
    
    # --- 关键修复点在这里 ---
    # 将Model对象也加入到全局ID映射中
    model_data = json_data['model']
    
    # 确保 model_data 也有 'type' 字段，以便后续逻辑统一处理
    if 'type' not in model_data:
        model_data['type'] = 'Model' 

    elements_by_id = {elem['id']: elem for elem in json_data['elements']}
    elements_by_id[model_data['id']] = model_data # <--- 将模型本身添加到字典中

    xml_elements_by_id = {}
    
    # Pass 1: 创建 uml:Model
    # (现在 model_data 已经提前获取)
    model_xml = create_element(
        'uml:Model',
        {'xmi:type': 'uml:Model', 'xmi:id': model_data['id'], 'name': model_data['name']},
        parent=root
    )
    xml_elements_by_id[model_data['id']] = model_xml
    
    # 多轮处理，直到所有元素都被创建
    elements_to_process = json_data['elements'][:]
    max_passes = len(elements_to_process) + 1
    for pass_num in range(max_passes):
        remaining_elements = []
        processed_count = 0
        
        for elem_data in elements_to_process:
            elem_id = elem_data['id']
            parent_id = elem_data.get('parentId')
            
            # 依赖检查：父元素必须已经创建
            if parent_id and parent_id not in xml_elements_by_id:
                remaining_elements.append(elem_data)
                continue
            
            # 现在 parent_id 'model-bicycle-uuid' 可以在 elements_by_id 中被找到
            parent_xml = xml_elements_by_id.get(parent_id)
            parent_type = elements_by_id[parent_id]['type'] if parent_id else None
            
            elem_type = elem_data['type']
            
            # --- 创建UML元素 ---
            # ... 后续代码保持不变 ...
            # 检查是否有name属性，如果没有，则不添加
            attrs = {'xmi:id': elem_id}
            if 'name' in elem_data and elem_data['name'] is not None:
                attrs['name'] = elem_data['name']

            tag = get_ownership_tag(elem_type, parent_type)
            
            if elem_type in ["Package", "Signal"]:
                attrs['xmi:type'] = f"uml:{elem_type}"
            elif elem_type in ["Block", "InterfaceBlock"]:
                attrs['xmi:type'] = 'uml:Class'
            elif elem_type == "ValueType":
                attrs['xmi:type'] = 'uml:DataType'
            elif elem_type == "Enumeration":
                attrs['xmi:type'] = 'uml:Enumeration'
            elif elem_type == "EnumerationLiteral":
                attrs['xmi:type'] = 'uml:EnumerationLiteral'
            elif elem_type in ["FullPort", "ProxyPort", "FlowPort"]:
                attrs['xmi:type'] = 'uml:Port'
                attrs['aggregation'] = 'composite'
                type_id = elem_data.get('typeId')
                if type_id and type_id in elements_by_id:
                    attrs['type'] = type_id
            elif elem_type == "Property":
                attrs.update({
                    'xmi:type': 'uml:Property',
                    'visibility': elem_data.get('visibility', 'public'),
                    'aggregation': elem_data.get('aggregation', 'none'),
                })
                type_id = elem_data.get('typeId')
                type_sub_elem = None # 重置type_sub_elem
                if type_id:
                    href = primitive_hrefs.get(type_id)
                    if href:
                        # 创建一个临时的type子元素，在后面附加
                        type_sub_elem = create_element('type', {'href': href})
                    elif type_id in elements_by_id:
                        attrs['type'] = type_id
                if elem_data.get('associationId'):
                    attrs['association'] = elem_data['associationId']

            elif elem_type == "Association":
                attrs['xmi:type'] = 'uml:Association'
                # memberEnd需要在属性创建后添加
                
            elif elem_type in ["AssemblyConnector", "BindingConnector"]:
                attrs['xmi:type'] = 'uml:Connector'
                
            elif elem_type == "Generalization":
                # Generalization是特殊的，它的父级是 "specific" block
                specific_xml = xml_elements_by_id.get(elem_data.get('specificId'))
                if not specific_xml:
                    remaining_elements.append(elem_data)
                    continue
                parent_xml = specific_xml # 覆盖父级
                attrs = {'xmi:id': elem_id, 'xmi:type': 'uml:Generalization', 'general': elem_data['generalId']}

            elif elem_type == "Diagram":
                # 图表作为扩展处理
                if parent_xml: # 确保父XML元素存在
                    add_diagram_extension(elem_data, parent_xml, root)
                processed_count += 1
                continue # 图表不创建标准XML元素，直接跳过
                
            # 创建元素并存储
            xml_elem = create_element(tag, attrs, parent=parent_xml)
            
            # --- 后处理和子元素添加 ---
            if elem_type == "Property":
                add_multiplicity(elem_data, xml_elem)
                if type_sub_elem:
                    xml_elem.append(type_sub_elem)

            if elem_type in ["AssemblyConnector", "BindingConnector"]:
                # 兼容旧的 end1/end2 和新的 end 列表格式
                ends = []
                if 'end1' in elem_data and 'end2' in elem_data:
                    ends = [elem_data['end1'], elem_data['end2']]
                elif 'end' in elem_data and isinstance(elem_data['end'], list):
                    ends = elem_data['end']
                
                for end_data in ends:
                    if not end_data: continue
                    end_attrs = {'xmi:id': end_data['id'], 'xmi:type': 'uml:ConnectorEnd'}
                    role_id = end_data.get('portRefId') or end_data.get('propertyRefId')
                    if role_id: end_attrs['role'] = role_id
                    if end_data.get('partRefId'): end_attrs['partWithPort'] = end_data['partRefId']
                    create_element('end', end_attrs, parent=xml_elem)

            xml_elements_by_id[elem_id] = xml_elem
            processed_count += 1

        elements_to_process = remaining_elements
        if not remaining_elements:
            print(f"XML Generation: All elements processed after {pass_num + 1} passes.")
            break
        if processed_count == 0 and remaining_elements:
            print(f"Error: Stuck in XML generation at pass {pass_num + 1}. Remaining elements:")
            for e in remaining_elements:
                print(f"  - ID: {e['id']}, ParentID: {e.get('parentId')}, Parent Exists: {e.get('parentId') in xml_elements_by_id}")
            break

    # Pass Final: 连接关系 (Association memberEnds)
    for elem_data in json_data['elements']:
        if elem_data['type'] == 'Association':
            assoc_xml = xml_elements_by_id.get(elem_data['id'])
            if assoc_xml is not None:
                for member_end_id in elem_data.get('memberEndIds', []):
                    if member_end_id in xml_elements_by_id:
                        create_element('memberEnd', {'xmi:idref': member_end_id}, parent=assoc_xml)
    
    # Pass Final: 应用SysML Stereotypes
    for elem_data in json_data['elements']:
        elem_id = elem_data['id']
        elem_type = elem_data['type']
        
        # 确保元素在XML中已创建
        if elem_id not in xml_elements_by_id:
            continue

        stereotype_tag, base_attr = None, None
        stereotype_attrs = {'xmi:id': f"{elem_id}_stereotype"}

        if elem_type == "Block":
            stereotype_tag, base_attr = 'sysml:Block', 'base_Class'
        elif elem_type == "ValueType":
            stereotype_tag, base_attr = 'sysml:ValueType', 'base_DataType'
        elif elem_type in ["FullPort", "ProxyPort", "FlowPort"]:
            stereotype_tag, base_attr = f"sysml:{elem_type}", 'base_Port'

        if stereotype_tag and base_attr:
            stereotype_attrs[base_attr] = elem_id
            create_element(stereotype_tag, stereotype_attrs, parent=root)
            
    # 格式化输出
    ET.indent(root, space="  ")
    xml_string = ET.tostring(root, encoding='unicode', method='xml')
    try:
        dom = xml.dom.minidom.parseString(xml_string)
        pretty_xml = dom.toprettyxml(indent="  ")
        # 移除minidom添加的空行
        final_xml = "\n".join(line for line in pretty_xml.split("\n") if line.strip())
        return final_xml
    except Exception as e:
        print(f"Warning: Failed to pretty-print XML: {e}. Returning raw XML.")
        return xml_string

# --- 如何在您的代码中集成 ---

# 1. 您的AI Agent代码保持不变，只是使用新的提示词
# from langchain_openai import ChatOpenAI
# from langchain_core.messages import HumanMessage

# chat = ChatOpenAI(...)

# ... prompt1 和 prompt2 使用上面提供的新版本 ...
# ... question2 是您的输入 ...

# messages1 = [HumanMessage(content=prompt1 + f"\n\n## 具体任务\n输入：\n\"\"\" {question2} \"\"\"\n输出：请你一步一步进行推理思考，按照上述的思考给我过程。")]
# thinking_response = chat.invoke(messages1)
# print("--- 思考过程 ---")
# print(thinking_response.content)

# messages2 = [HumanMessage(content=prompt2 + "\n\n" + thinking_response.content)]
# json_response = chat.invoke(messages2)
# print("\n--- 生成的JSON ---")
# print(json_response.content)

# # 2. 使用新的生成代码处理JSON
# try:
#     generated_json_data = json.loads(json_response.content)
#     final_xml_output = generate_sysml_xml(generated_json_data)
#     print("\n--- 生成的XML ---")
#     print(final_xml_output)
#     # with open("bicycle_system.xmi", "w", encoding="utf-8") as f:
#     #     f.write(final_xml_output)
# except json.JSONDecodeError as e:
#     print(f"\nError: Failed to decode JSON. {e}")
#     print("Raw response:", json_response.content)
# except Exception as e:
#     print(f"\nAn error occurred during XML generation: {e}")


bdd_ibd_lamp_switch_json = bdd_ibd_lamp_switch_json ={
  "model": {
    "id": "model-vehicle-uuid",
    "name": "VehicleModel"
  },
  "elements": [
    {
      "id": "pkg-vehicle-uuid",
      "type": "Package",
      "name": "VehiclePackage",
      "parentId": "model-vehicle-uuid"
    },
    {
      "id": "unit-amp-uuid",
      "type": "Unit",
      "name": "A",
      "parentId": "pkg-vehicle-uuid",
      "symbol": "A"
    },
    {
      "id": "vt-capacity-uuid",
      "type": "ValueType",
      "name": "Capacity",
      "parentId": "pkg-vehicle-uuid",
      "baseType": "Real",
      "unitId": "unit-amp-uuid"
    },
    {
      "id": "vt-diameter-uuid",
      "type": "ValueType",
      "name": "Diameter",
      "parentId": "pkg-vehicle-uuid",
      "baseType": "Real",
      "unitId": "unit-meter-uuid"
    },
    {
      "id": "vt-width-uuid",
      "type": "ValueType",
      "name": "Width",
      "parentId": "pkg-vehicle-uuid",
      "baseType": "Real",
      "unitId": "unit-meter-uuid"
    },
    {
      "id": "vt-speed-uuid",
      "type": "ValueType",
      "name": "Speed",
      "parentId": "pkg-vehicle-uuid",
      "baseType": "Real",
      "unitId": "unit-meterPerSec-uuid"
    },
    {
      "id": "vt-gear-uuid",
      "type": "ValueType",
      "name": "Gear",
      "parentId": "pkg-vehicle-uuid",
      "baseType": "Integer"
    },
    {
      "id": "vt-battery-capacity-uuid",
      "type": "ValueType",
      "name": "BatteryCapacity",
      "parentId": "pkg-vehicle-uuid",
      "baseType": "Real",
      "unitId": "unit-Ah-uuid"
    },
    {
      "id": "enum-gear-uuid",
      "type": "Enumeration",
      "name": "GearPositions",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "enum-battstatus-uuid",
      "type": "Enumeration",
      "name": "BatteryStatus",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "sig-speed-uuid",
      "type": "Signal",
      "name": "SpeedSignal",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "sig-slope-uuid",
      "type": "Signal",
      "name": "SlopeSignal",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "sig-attitude-uuid",
      "type": "Signal",
      "name": "AttitudeSignal",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "if-driveout-uuid",
      "type": "InterfaceBlock",
      "name": "DriveOutputInterface",
      "parentId": "pkg-vehicle-uuid",
      "isAbstract": True
    },
    {
      "id": "if-brakecontrol-uuid",
      "type": "InterfaceBlock",
      "name": "BrakeControlInterface",
      "parentId": "pkg-vehicle-uuid",
      "isAbstract": True
    },
    {
      "id": "if-userinput-uuid",
      "type": "InterfaceBlock",
      "name": "UserInputInterface",
      "parentId": "pkg-vehicle-uuid",
      "isAbstract": True
    },
    {
      "id": "blk-battery-uuid",
      "type": "Block",
      "name": "Battery",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "blk-drivesystem-uuid",
      "type": "Block",
      "name": "DriveSystem",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "blk-brakesystem-uuid",
      "type": "Block",
      "name": "BrakeSystem",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "blk-controlhandle-uuid",
      "type": "Block",
      "name": "ControlHandle",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "blk-sensormodule-uuid",
      "type": "Block",
      "name": "SensorModule",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "blk-display-uuid",
      "type": "Block",
      "name": "Display",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "blk-gear-uuid",
      "type": "Block",
      "name": "Gear",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "blk-wheel-uuid",
      "type": "Block",
      "name": "Wheel",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "blk-vehicle-uuid",
      "type": "Block",
      "name": "Vehicle",
      "parentId": "pkg-vehicle-uuid"
    },
    {
      "id": "prop-vehicle-drivesys",
      "type": "Property",
      "name": "driveSystem",
      "parentId": "blk-vehicle-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-drivesystem-uuid",
      "associationId": "assoc-vehicle-drivesys",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-vehicle-brakesys",
      "type": "Property",
      "name": "brakeSystem",
      "parentId": "blk-vehicle-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-brakesystem-uuid",
      "associationId": "assoc-vehicle-brakesys",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-vehicle-control",
      "type": "Property",
      "name": "controlHandle",
      "parentId": "blk-vehicle-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-controlhandle-uuid",
      "associationId": "assoc-vehicle-control",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-vehicle-battery",
      "type": "Property",
      "name": "battery",
      "parentId": "blk-vehicle-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-battery-uuid",
      "associationId": "assoc-vehicle-battery",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-vehicle-sensor",
      "type": "Property",
      "name": "sensorModule",
      "parentId": "blk-vehicle-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-sensormodule-uuid",
      "associationId": "assoc-vehicle-sensor",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-vehicle-display",
      "type": "Property",
      "name": "display",
      "parentId": "blk-vehicle-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-display-uuid",
      "associationId": "assoc-vehicle-display",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-vehicle-speed",
      "type": "Property",
      "name": "currentSpeed",
      "parentId": "blk-vehicle-uuid",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "vt-speed-uuid",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-vehicle-gear",
      "type": "Property",
      "name": "currentGear",
      "parentId": "blk-vehicle-uuid",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "enum-gear-uuid",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-vehicle-battstatus",
      "type": "Property",
      "name": "batteryStatus",
      "parentId": "blk-vehicle-uuid",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "enum-battstatus-uuid",
      "multiplicity": "[1..1]"
    },
    {
      "id": "port-vehicle-driveout",
      "type": "Port",
      "name": "driveOutput",
      "parentId": "blk-vehicle-uuid",
      "typeId": "if-driveout-uuid"
    },
    {
      "id": "port-vehicle-brakectrl",
      "type": "Port",
      "name": "brakeControl",
      "parentId": "blk-vehicle-uuid",
      "typeId": "if-brakecontrol-uuid"
    },
    {
      "id": "port-vehicle-userinput",
      "type": "Port",
      "name": "userInput",
      "parentId": "blk-vehicle-uuid",
      "typeId": "if-userinput-uuid"
    },
    {
      "id": "port-vehicle-powersupply",
      "type": "Port",
      "name": "powerSupply",
      "parentId": "blk-vehicle-uuid",
      "typeId": "blk-battery-uuid"
    },
    {
      "id": "port-vehicle-speedsensor",
      "type": "Port",
      "name": "speedSensor",
      "parentId": "blk-vehicle-uuid",
      "typeId": "sig-speed-uuid"
    },
    {
      "id": "port-vehicle-slopesensor",
      "type": "Port",
      "name": "slopeSensor",
      "parentId": "blk-vehicle-uuid",
      "typeId": "sig-slope-uuid"
    },
    {
      "id": "port-vehicle-attitudesensor",
      "type": "Port",
      "name": "attitudeSensor",
      "parentId": "blk-vehicle-uuid",
      "typeId": "sig-attitude-uuid"
    },
    {
      "id": "op-vehicle-changgear",
      "type": "Operation",
      "name": "changeGear",
      "parentId": "blk-vehicle-uuid"
    },
    {
      "id": "p-vehicle-gear",
      "type": "Parameter",
      "name": "gear",
      "parentId": "op-vehicle-changgear",
      "typeId": "enum-gear-uuid",
      "direction": "in"
    },
    {
      "id": "op-vehicle-setspeed",
      "type": "Operation",
      "name": "setSpeed",
      "parentId": "blk-vehicle-uuid"
    },
    {
      "id": "p-vehicle-speed",
      "type": "Parameter",
      "name": "speed",
      "parentId": "op-vehicle-setspeed",
      "typeId": "vt-speed-uuid",
      "direction": "in"
    },
    {
      "id": "op-vehicle-brakes",
      "type": "Operation",
      "name": "applyBrakes",
      "parentId": "blk-vehicle-uuid"
    },
    {
      "id": "op-vehicle-stop",
      "type": "Operation",
      "name": "stop",
      "parentId": "blk-vehicle-uuid"
    },
    {
      "id": "prop-drivesys-chain",
      "type": "Property",
      "name": "chain",
      "parentId": "blk-drivesystem-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-chain-uuid",
      "associationId": "assoc-drivesys-chain",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-drivesys-flywheel",
      "type": "Property",
      "name": "flywheel",
      "parentId": "blk-drivesystem-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-flywheel-uuid",
      "associationId": "assoc-drivesys-flywheel",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-drivesys-transmission",
      "type": "Property",
      "name": "transmission",
      "parentId": "blk-drivesystem-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-transmission-uuid",
      "associationId": "assoc-drivesys-transmission",
      "multiplicity": "[1..1]"
    },
    {
      "id": "port-drivesys-powerout",
      "type": "Port",
      "name": "powerOutput",
      "parentId": "blk-drivesystem-uuid",
      "typeId": "if-driveout-uuid"
    },
    {
      "id": "port-drivesys-drivein",
      "type": "Port",
      "name": "driveInput",
      "parentId": "blk-drivesystem-uuid",
      "typeId": "if-driveout-uuid"
    },
    {
      "id": "prop-brakes-handbrake",
      "type": "Property",
      "name": "handBrake",
      "parentId": "blk-brakesystem-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-handle-uuid",
      "associationId": "assoc-brakes-handbrake",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-brakes-brakepad",
      "type": "Property",
      "name": "brakePad",
      "parentId": "blk-brakesystem-uuid",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-brakepad-uuid",
      "associationId": "assoc-brakes-brakepad",
      "multiplicity": "[1..1]"
    },
    {
      "id": "port-brakes-control",
      "type": "Port",
      "name": "brakeControl",
      "parentId": "blk-brakesystem-uuid",
      "typeId": "if-brakecontrol-uuid"
    },
    {
      "id": "op-brakes-apply",
      "type": "Operation",
      "name": "applyBrake",
      "parentId": "blk-brakesystem-uuid"
    },
    {
      "id": "op-brakes-release",
      "type": "Operation",
      "name": "releaseBrake",
      "parentId": "blk-brakesystem-uuid"
    },
    {
      "id": "port-control-speed",
      "type": "Port",
      "name": "speedControl",
      "parentId": "blk-controlhandle-uuid",
      "typeId": "if-userinput-uuid"
    },
    {
      "id": "port-control-brake",
      "type": "Port",
      "name": "brakeControl",
      "parentId": "blk-controlhandle-uuid",
      "typeId": "if-userinput-uuid"
    },
    {
      "id": "op-increase-speed",
      "type": "Operation",
      "name": "increaseSpeed",
      "parentId": "blk-controlhandle-uuid"
    },
    {
      "id": "op-decrease-speed",
      "type": "Operation",
      "name": "decreaseSpeed",
      "parentId": "blk-controlhandle-uuid"
    },
    {
      "id": "op-apply-brake",
      "type": "Operation",
      "name": "applyBrake",
      "parentId": "blk-controlhandle-uuid"
    },
    {
      "id": "prop-battery-capacity",
      "type": "Property",
      "name": "capacity",
      "parentId": "blk-battery-uuid",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "vt-capacity-uuid",
      "multiplicity": "[1..1]"
    },
    {
      "id": "port-sensor-speed",
      "type": "Port",
      "name": "speedSensor",
      "parentId": "blk-sensormodule-uuid",
      "typeId": "sig-speed-uuid"
    },
    {
      "id": "port-sensor-slope",
      "type": "Port",
      "name": "slopeSensor",
      "parentId": "blk-sensormodule-uuid",
      "typeId": "sig-slope-uuid"
    },
    {
      "id": "port-sensor-attitude",
      "type": "Port",
      "name": "attitudeSensor",
      "parentId": "blk-sensormodule-uuid",
      "typeId": "sig-attitude-uuid"
    },
    {
      "id": "port-display-speed",
      "type": "Port",
      "name": "speedDisplay",
      "parentId": "blk-display-uuid",
      "typeId": "sig-speed-uuid"
    },
    {
      "id": "port-display-gear",
      "type": "Port",
      "name": "gearDisplay",
      "parentId": "blk-display-uuid",
      "typeId": "sig-gear-uuid"
    },
    {
      "id": "port-display-batt",
      "type": "Port",
      "name": "batteryStatusDisplay",
      "parentId": "blk-display-uuid",
      "typeId": "sig-battstatus-uuid"
    },
    {
      "id": "prop-gear-position",
      "type": "Property",
      "name": "position",
      "parentId": "blk-gear-uuid",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "enum-gear-uuid",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-wheel-diameter",
      "type": "Property",
      "name": "diameter",
      "parentId": "blk-wheel-uuid",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "vt-diameter-uuid",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-wheel-width",
      "type": "Property",
      "name": "width",
      "parentId": "blk-wheel-uuid",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "vt-width-uuid",
      "multiplicity": "[1..1]"
    },
    {
      "id": "assoc-vehicle-drivesys",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-vehicle-drivesys",
        "prop-drivesys-vehicle"
      ]
    },
    {
      "id": "assoc-vehicle-brakesys",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-vehicle-brakesys",
        "prop-brakesys-vehicle"
      ]
    },
    {
      "id": "assoc-vehicle-control",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-vehicle-control",
        "prop-control-vehicle"
      ]
    },
    {
      "id": "assoc-vehicle-battery",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-vehicle-battery",
        "prop-battery-vehicle"
      ]
    },
    {
      "id": "assoc-vehicle-sensor",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-vehicle-sensor",
        "prop-sensor-vehicle"
      ]
    },
    {
      "id": "assoc-vehicle-display",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-vehicle-display",
        "prop-display-vehicle"
      ]
    },
    {
      "id": "assoc-gear-wheel",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-gear-position",
        "prop-wheel-gear"
      ]
    },
    {
      "id": "assoc-drivesys-chain",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-drivesys-chain",
        "prop-chain-drivesys"
      ]
    },
    {
      "id": "assoc-drivesys-flywheel",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-drivesys-flywheel",
        "prop-flywheel-drivesys"
      ]
    },
    {
      "id": "assoc-drivesys-transmission",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-drivesys-transmission",
        "prop-transmission-drivesys"
      ]
    },
    {
      "id": "assoc-brakes-handbrake",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-brakes-handbrake",
        "prop-handbrake-brakes"
      ]
    },
    {
      "id": "assoc-brakes-brakepad",
      "type": "Association",
      "parentId": "pkg-vehicle-uuid",
      "memberEndIds": [
        "prop-brakes-brakepad",
        "prop-brakepad-brakes"
      ]
    },
    {
      "id": "conn-trans-chain",
      "type": "AssemblyConnector",
      "parentId": "blk-transmission-uuid",
      "kind": "assembly",
      "end1": {
        "partRefId": "prop-transmission",
        "portRefId": "port-transmission-driveout"
      },
      "end2": {
        "partRefId": "prop-chain",
        "portRefId": "port-chain-input"
      }
    },
    {
      "id": "conn-flywheel-chain",
      "type": "AssemblyConnector",
      "parentId": "blk-flywheel-uuid",
      "kind": "assembly",
      "end1": {
        "partRefId": "prop-flywheel",
        "portRefId": "port-flywheel-output"
      },
      "end2": {
        "partRefId": "prop-chain",
        "portRefId": "port-chain-input"
      }
    },
    {
      "id": "conn-chain-flywheel",
      "type": "AssemblyConnector",
      "parentId": "blk-chain-uuid",
      "kind": "assembly",
      "end1": {
        "partRefId": "prop-chain",
        "portRefId": "port-chain-output"
      },
      "end2": {
        "partRefId": "prop-flywheel",
        "portRefId": "port-flywheel-input"
      }
    },
    {
      "id": "conn-chain-transmission",
      "type": "AssemblyConnector",
      "parentId": "blk-chain-uuid",
      "kind": "assembly",
      "end1": {
        "partRefId": "prop-chain",
        "portRefId": "port-chain-output"
      },
      "end2": {
        "partRefId": "prop-transmission",
        "portRefId": "port-transmission-input"
      }
    },
    {
      "id": "conn-ecu-sensor-display",
      "type": "AssemblyConnector",
      "parentId": "blk-ecu-uuid",
      "kind": "assembly",
      "end1": {
        "partRefId": "prop-ecu-sensor",
        "portRefId": "port-ecu-sensor-out"
      },
      "end2": {
        "partRefId": "prop-display",
        "portRefId": "port-display-input"
      }
    }
  ]
}

xml_output = generate_sysml_xml(bdd_ibd_lamp_switch_json) # Use the new JSON
print(xml_output)