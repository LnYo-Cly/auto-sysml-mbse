import xml.etree.ElementTree as ET
from collections import defaultdict

# 命名空间定义（保持不变）
namespaces = {
    'xmi': 'http://www.omg.org/XMI',
    'uml': 'http://www.omg.org/spec/UML/20090901',
    'sysml': 'http://www.omg.org/spec/SysML/20131001',
    'MD_Customization_for_SysML__additional_stereotypes': 'http://www.magicdraw.com/spec/Customization/190/SysML'
}

for prefix, uri in namespaces.items():
    ET.register_namespace(prefix, uri)

# 辅助函数（保持不变）
def add_diagram_extension(block_id, diagram_name="参数图"):
    diagram_id = f"_{block_id}_diagram"
    rep_id = f"_{block_id}_rep"
    contents_id = f"_{block_id}_contents"
    extension = ET.Element('xmi:Extension', {'extender': 'MagicDraw UML 2021x'})
    model_extension = ET.SubElement(extension, 'modelExtension')
    owned_diagram = ET.SubElement(model_extension, 'ownedDiagram', {
        'xmi:type': 'uml:Diagram', 'xmi:id': diagram_id, 'name': diagram_name,
        'visibility': 'public', 'context': block_id, 'ownerOfDiagram': block_id
    })
    inner_extension = ET.SubElement(owned_diagram, 'xmi:Extension', {'extender': 'MagicDraw UML 2021x'})
    diagram_rep = ET.SubElement(inner_extension, 'diagramRepresentation')
    diagram_obj = ET.SubElement(diagram_rep, 'diagram:DiagramRepresentationObject', {
        'ID': rep_id, 'initialFrameSizeSet': 'true',
        'requiredFeature': 'com.nomagic.magicdraw.plugins.impl.sysml#SysML;MD_customization_for_SysML.mdzip;UML_Standard_Profile.mdzip',
        'type': 'SysML Parametric Diagram', 'umlType': 'Composite Structure Diagram',
        'xmi:id': f"_{block_id}_xmi", 'xmi:version': '2.0',
        'xmlns:binary': 'http://www.nomagic.com/ns/cameo/client/binary/1.0',
        'xmlns:diagram': 'http://www.nomagic.com/ns/magicdraw/core/diagram/1.0',
        'xmlns:xmi': 'http://www.omg.org/XMI',
        'xmlns:xsi': 'http://www.w3.org/2001/XMLSchema-instance'
    })
    ET.SubElement(diagram_obj, 'diagramContents', {
        'contentHash': 'bfe6e2a79c6e668f74eded668a40a36b357487c5',
        'exporterName': 'MagicDraw UML', 'exporterVersion': '2021x', 'xmi:id': contents_id
    })
    return extension

def create_element(tag, attrs={}, parent=None, text=None):
    elem = ET.Element(tag, attrs)
    if parent is not None:
        parent.append(elem)
    if text is not None:
        elem.text = text
    return elem

# --- 重构后的核心生成函数 (已修正) ---
def generate_xml(json_data):
    # 1. 初始化XML根和基本结构
    root_attrs = {
        'xmi:version': '2.5',
        'xmlns:xmi': namespaces['xmi'],
        'xmlns:uml': namespaces['uml'],
        'xmlns:sysml': namespaces['sysml'],
        'xmlns:MD_Customization_for_SysML__additional_stereotypes': namespaces['MD_Customization_for_SysML__additional_stereotypes']
    }
    root = ET.Element('xmi:XMI', root_attrs)
    
    xml_elements = {}

    # 2. 预处理JSON数据
    elements_by_id = {elem['id']: elem for elem in json_data['elements']}
    children_by_parent_id = defaultdict(list)
    for elem in json_data['elements']:
        children_by_parent_id[elem['parentId']].append(elem)

    # 3. 创建顶层Model元素
    model_data = json_data['model'][0]
    model = create_element('uml:Model', {
        'xmi:type': 'uml:Model',
        'xmi:id': model_data['id'],
        'name': model_data['name']
    }, root)
    xml_elements[model_data['id']] = model

    # 4. 递归构建UML元素树
    def build_tree(parent_id, parent_xml_element):
        if parent_id not in children_by_parent_id:
            return

        for elem_data in children_by_parent_id[parent_id]:
            elem_id = elem_data['id']
            elem_type = elem_data['type']
            elem_name = elem_data.get('name', '')
            
            if elem_type in ["Package", "Block", "ConstraintBlock", "ValueType"]:
                tag = 'packagedElement'
                if elem_type == "Package": uml_type = "uml:Package"
                elif elem_type == "ValueType": uml_type = "uml:DataType"
                else: uml_type = "uml:Class"

                elem = create_element(tag, {'xmi:type': uml_type, 'xmi:id': elem_id, 'name': elem_name}, parent_xml_element)
                xml_elements[elem_id] = elem

                if elem_type == "Block": elem.append(add_diagram_extension(elem_id))
                if elem_type == "ConstraintBlock" and 'specification' in elem_data:
                    spec_data = elem_data['specification']
                    owned_rule = create_element('ownedRule', {'xmi:type': 'uml:Constraint', 'xmi:id': f"{elem_id}_constraint"}, elem)
                    create_element('constrainedElement', {'xmi:idref': elem_id}, owned_rule)
                    spec = create_element('specification', {'xmi:type': 'uml:OpaqueExpression', 'xmi:id': f"{elem_id}_spec"}, owned_rule)
                    create_element('body', {}, spec, text=spec_data.get('expression', ''))
                    create_element('language', {}, spec, text=spec_data.get('language', ''))

            elif elem_type == "Property":
                prop_kind = elem_data['propertyKind']
                type_id = elem_data['typeId']
                attrs = {
                    'xmi:type': 'uml:Property', 'xmi:id': elem_id, 'name': elem_name,
                    'aggregation': 'composite'
                }
                if prop_kind == "constraint":
                    attrs['visibility'] = 'private'
                
                elem = create_element('ownedAttribute', attrs, parent_xml_element)
                xml_elements[elem_id] = elem

                # +++ 关键修正点 +++
                if type_id.startswith('http'):
                    create_element('type', {'href': type_id}, elem)
                else:
                    elem.set('type', type_id)

            elif elem_type == "ConstraintParameter":
                type_id = elem_data['typeId']
                elem = create_element('ownedAttribute', {
                    'xmi:type': 'uml:Port', 'xmi:id': elem_id, 'name': elem_name,
                    'visibility': 'private', 'aggregation': 'composite'
                }, parent_xml_element)
                # 同样应用修正逻辑，尽管之前可能已部分正确
                if type_id.startswith('http'):
                    create_element('type', {'href': type_id}, elem)
                else:
                    elem.set('type', type_id)
                xml_elements[elem_id] = elem
                
            elif elem_type == "BindingConnector":
                elem = create_element('ownedConnector', {'xmi:type': 'uml:Connector', 'xmi:id': elem_id, 'visibility': 'public'}, parent_xml_element)
                xml_elements[elem_id] = elem
                
                end1_data, end2_data = elem_data['end1'], elem_data['end2']
                end1_id, end2_id = f"{elem_id}_end1", f"{elem_id}_end2"
                create_element('end', {'xmi:type': 'uml:ConnectorEnd', 'xmi:id': end1_id, 'role': end1_data['propertyRefId']}, elem)
                create_element('end', {'xmi:type': 'uml:ConnectorEnd', 'xmi:id': end2_id, 'partWithPort': end2_data['partRefId'], 'role': end2_data['portRefId']}, elem)
                xml_elements[end1_id] = elem.find('end[1]')
                xml_elements[end2_id] = elem.find('end[2]')

            build_tree(elem_id, xml_elements.get(elem_id))

    build_tree(model_data['id'], model)

    # 5. 添加所有构造型（Stereotypes）
    nested_connector_ends = []
    for elem_id, elem_data in elements_by_id.items():
        elem_type = elem_data['type']
        if elem_type == "Block": create_element('sysml:Block', {'xmi:id': f"{elem_id}_stereotype", 'base_Class': elem_id}, root)
        elif elem_type == "ConstraintBlock": create_element('sysml:ConstraintBlock', {'xmi:id': f"{elem_id}_stereotype", 'base_Class': elem_id}, root)
        elif elem_type == "ValueType": create_element('sysml:ValueType', {'xmi:id': f"{elem_id}_stereotype", 'base_DataType': elem_id}, root)
        elif elem_type == "Property":
            if elem_data['propertyKind'] == 'value': create_element('MD_Customization_for_SysML__additional_stereotypes:ValueProperty', {'xmi:id': f"{elem_id}_stereotype", 'base_Property': elem_id}, root)
            elif elem_data['propertyKind'] == 'constraint': create_element('MD_Customization_for_SysML__additional_stereotypes:ConstraintProperty', {'xmi:id': f"{elem_id}_stereotype", 'base_Property': elem_id}, root)
        elif elem_type == "ConstraintParameter": create_element('MD_Customization_for_SysML__additional_stereotypes:ConstraintParameter', {'xmi:id': f"{elem_id}_stereotype", 'base_Port': elem_id}, root)
        elif elem_type == "BindingConnector":
            create_element('sysml:BindingConnector', {'xmi:id': f"{elem_id}_stereotype", 'base_Connector': elem_id}, root)
            nested_connector_ends.append((f"{elem_id}_end2", elem_data['end2']['partRefId']))

    for end_id, prop_path_id in nested_connector_ends:
        create_element('sysml:NestedConnectorEnd', {'xmi:id': f"{end_id}_stereotype", 'base_ConnectorEnd': end_id, 'propertyPath': prop_path_id}, root)

    return ET.tostring(root, encoding='unicode', method='xml')


# --- 示例使用 (保持不变) ---
new_json_data = {
  "model": [
    {
      "id": "model-cycling",
      "name": "CyclingModel"
    }
  ],
  "elements": [
    {
      "id": "pkg-cycling",
      "type": "Package",
      "name": "ParametricDiagram",
      "parentId": "model-cycling"
    },
    {
      "id": "block1",
      "type": "Block",
      "name": "CyclingSystem",
      "parentId": "pkg-cycling"
    },
    { "id": "prop1", "type": "Property", "name": "v", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop2", "type": "Property", "name": "d", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop3", "type": "Property", "name": "t", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop4", "type": "Property", "name": "C", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop5", "type": "Property", "name": "C0", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop6", "type": "Property", "name": "C_used", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop7", "type": "Property", "name": "P", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop8", "type": "Property", "name": "F", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop9", "type": "Property", "name": "gear", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop10", "type": "Property", "name": "T", "propertyKind": "value", "parentId": "block1","typeId": "Real" },
    { "id": "prop11", "type": "Property", "name": "k", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop12", "type": "Property", "name": "D", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },
    { "id": "prop13", "type": "Property", "name": "E", "propertyKind": "value", "parentId": "block1", "typeId": "Real" },

    {
      "id": "cb1",
      "type": "ConstraintBlock",
      "name": "VelocityEquation",
      "parentId": "pkg-cycling",
      "specification": {
        "expression": "v = d / t",
        "language": "English"
      }
    },
    { "id": "param1", "type": "ConstraintParameter", "name": "v", "parentId": "cb1", "typeId": "Real" },
    { "id": "param2", "type": "ConstraintParameter", "name": "d", "parentId": "cb1", "typeId": "Real" },
    { "id": "param3", "type": "ConstraintParameter", "name": "t", "parentId": "cb1", "typeId": "Real" },

    {
      "id": "cb2",
      "type": "ConstraintBlock",
      "name": "BatteryCapacityEquation",
      "parentId": "pkg-cycling",
      "specification": {
        "expression": "C = C0 - C_used",
        "language": "English"
      }
    },
    { "id": "param4", "type": "ConstraintParameter", "name": "C", "parentId": "cb2", "typeId": "Real" },
    { "id": "param5", "type": "ConstraintParameter", "name": "C0", "parentId": "cb2", "typeId": "Real" },
    { "id": "param6", "type": "ConstraintParameter", "name": "C_used", "parentId": "cb2", "typeId": "Real" },

    {
      "id": "cb3",
      "type": "ConstraintBlock",
      "name": "PowerEquation",
      "parentId": "pkg-cycling",
      "specification": {
        "expression": "P = F * v",
        "language": "English"
      }
    },
    { "id": "param7", "type": "ConstraintParameter", "name": "P", "parentId": "cb3", "typeId": "Real" },
    { "id": "param8", "type": "ConstraintParameter", "name": "F", "parentId": "cb3", "typeId": "Real" },
    { "id": "param9", "type": "ConstraintParameter", "name": "v", "parentId": "cb3", "typeId": "Real" },

    {
      "id": "cb4",
      "type": "ConstraintBlock",
      "name": "TorqueEquation",
      "parentId": "pkg-cycling",
      "specification": {
        "expression": "T = k * gear",
        "language": "English"
      }
    },
    { "id": "param10", "type": "ConstraintParameter", "name": "T", "parentId": "cb4", "typeId": "Real" },
    { "id": "param11", "type": "ConstraintParameter", "name": "k", "parentId": "cb4", "typeId": "Real" },
 { "id": "param12", "type": "ConstraintParameter", "name": "gear", "parentId": "cb4", "typeId": "Real" },

    {
      "id": "cb5",
      "type": "ConstraintBlock",
      "name": "RangeEquation",
      "parentId": "pkg-cycling",
      "specification": {
        "expression": "D = C / E",
        "language": "English"
      }
    },
    { "id": "param13", "type": "ConstraintParameter", "name": "D", "parentId": "cb5", "typeId": "Real" },
    { "id": "param14", "type": "ConstraintParameter", "name": "C", "parentId": "cb5", "typeId": "Real" },
    { "id": "param15", "type": "ConstraintParameter", "name": "E", "parentId": "cb5", "typeId": "Real" },

    { "id": "constraint1", "type": "Property", "name": "VelocityEquation", "propertyKind": "constraint", "parentId": "block1", "typeId": "cb1" },
    { "id": "constraint2", "type": "Property", "name": "BatteryCapacityEquation", "propertyKind": "constraint", "parentId": "block1", "typeId": "cb2" },
    { "id": "constraint3", "type": "Property", "name": "PowerEquation", "propertyKind": "constraint", "parentId": "block1", "typeId": "cb3" },
    { "id": "constraint4", "type": "Property", "name": "TorqueEquation", "propertyKind": "constraint", "parentId": "block1", "typeId": "cb4" },
    { "id": "constraint5", "type": "Property", "name": "RangeEquation", "propertyKind": "constraint", "parentId": "block1", "typeId": "cb5" },

    {
      "id": "conn1",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop1" },
      "end2": { "partRefId": "constraint1", "portRefId": "param1" }
    },
    {
      "id": "conn2",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop2" },
      "end2": { "partRefId": "constraint1", "portRefId": "param2" }
    },
    {
      "id": "conn3",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop3" },
      "end2": { "partRefId": "constraint1", "portRefId": "param3" }
    },
    {
      "id": "conn4",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop4" },
      "end2": { "partRefId": "constraint2", "portRefId": "param4" }
    },
    {
      "id": "conn5",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop5" },
      "end2": { "partRefId": "constraint2", "portRefId": "param5" }
    },
    {
      "id": "conn6",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop6" },
      "end2": { "partRefId": "constraint2", "portRefId": "param6" }
    },
    {
      "id": "conn7",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop7" },
      "end2": { "partRefId": "constraint3", "portRefId": "param7" }
    },
    {
      "id": "conn8",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop8" },
      "end2": { "partRefId": "constraint3", "portRefId": "param8" }
    },
    {
      "id": "conn9",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop1" },
      "end2": { "partRefId": "constraint3", "portRefId": "param9" }
    },
    {
      "id": "conn10",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop10" },
      "end2": { "partRefId": "constraint4", "portRefId": "param10" }
    },
    {
      "id": "conn11",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop11" },
      "end2": { "partRefId": "constraint4", "portRefId": "param11" }
    },
    {
      "id": "conn12",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop9" },
      "end2": { "partRefId": "constraint4", "portRefId": "param12" }
    },
    {
      "id": "conn13",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop12" },
      "end2": { "partRefId": "constraint5", "portRefId": "param13" }
    },
    {
      "id": "conn14",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop4" },
      "end2": { "partRefId": "constraint5", "portRefId": "param14" }
    },
    {
      "id": "conn15",
      "type": "BindingConnector",
      "parentId": "block1",
      "end1": { "propertyRefId": "prop13" },
      "end2": { "partRefId": "constraint5", "portRefId": "param15" }
    }
  ]
}

xml_output = generate_xml(new_json_data)
# 验证输出
print(xml_output)

