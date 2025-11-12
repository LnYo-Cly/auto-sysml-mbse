import json
import xml.etree.ElementTree as ET

def generate_xmi_from_new_json(data, output_file='output.xmi'):
    """
    Generates an XMI file from the new, structured JSON format with corrected Association logic.
    """
    # Define namespaces
    namespaces = {
        'xmi': "http://www.omg.org/XMI",
        'uml': "http://www.omg.org/spec/UML/20090901"
    }
    
    for prefix, uri in namespaces.items():
        ET.register_namespace(prefix, uri)
    
    # Create root XMI element
    xmi_root = ET.Element('xmi:XMI', attrib={
        'xmi:version': "2.5",
        'xmlns:xmi': namespaces['xmi'],
        'xmlns:uml': namespaces['uml']
    })
    
    # --- Processing Logic ---
    xmi_elements = {}

    # Pass 1: Create structural elements (Model, Package, Actor, UseCase)
    # -----------------------------------------------------------------
    
    model_data = data['model'][0]
    model_id = model_data['id']
    model = ET.SubElement(xmi_root, 'uml:Model', attrib={
        'xmi:type': 'uml:Model',
        'xmi:id': model_id,
        'name': model_data.get('name', 'Model')
    })
    xmi_elements[model_id] = model
    
    for element in data.get('elements', []):
        elem_type = element['type']
        if elem_type in ['Association', 'Include', 'Extend', 'Generalization']:
            continue
            
        elem_id = element['id']
        parent_id = element.get('parentId')
        
        if not parent_id or parent_id not in xmi_elements:
            print(f"Warning: Element '{elem_id}' has a missing or unprocessed parent '{parent_id}'. Skipping.")
            continue
            
        parent_element = xmi_elements[parent_id]
        
        # All elements like Package, Actor, UseCase are packagedElements
        new_xml_element = ET.SubElement(parent_element, 'packagedElement', attrib={
            'xmi:type': f"uml:{elem_type}",
            'xmi:id': elem_id,
            'name': element.get('name', f'Unnamed{elem_type}')
        })
        xmi_elements[elem_id] = new_xml_element

    # Pass 2: Create relationship elements (Association, Include, Extend, Generalization)
    # ----------------------------------------------------------------------------------
    for element in data.get('elements', []):
        elem_type = element['type']
        
        if elem_type not in ['Association', 'Include', 'Extend', 'Generalization']:
            continue
            
        elem_id = element['id']
        parent_id = element.get('parentId')
        source_id = element.get('sourceId')
        target_id = element.get('targetId')

        if not all([parent_id, source_id, target_id,
                    parent_id in xmi_elements, 
                    source_id in xmi_elements, 
                    target_id in xmi_elements]):
            print(f"Skipping relationship '{elem_id}' due to missing parent, source, or target reference.")
            continue
        
        parent_element = xmi_elements[parent_id]
        source_xmi_id = xmi_elements[source_id].get('xmi:id')
        target_xmi_id = xmi_elements[target_id].get('xmi:id')

        if elem_type == 'Association':
            # Create the <packagedElement> wrapper for the Association
            assoc = ET.SubElement(parent_element, 'packagedElement', attrib={
                'xmi:type': 'uml:Association',
                'xmi:id': elem_id
            })

            # Define unique IDs for the property ends
            # Using a consistent naming scheme based on the association ID
            end1_id = f"{elem_id}_end_source"
            end2_id = f"{elem_id}_end_target"
            
            # Create the two 'ownedEnd' properties. This is the critical part.
            # One end points to the source, the other to the target.
            # Following the original code's pattern for consistency.
            ET.SubElement(assoc, 'ownedEnd', attrib={
                'xmi:type': 'uml:Property',
                'xmi:id': end1_id,
                'type': source_xmi_id,
                'association': elem_id
            })
            ET.SubElement(assoc, 'ownedEnd', attrib={
                'xmi:type': 'uml:Property',
                'xmi:id': end2_id,
                'type': target_xmi_id,
                'association': elem_id
            })
            
            # Add 'memberEnd' to reference the 'ownedEnd's by their IDs.
            ET.SubElement(assoc, 'memberEnd', attrib={'xmi:idref': end1_id})
            ET.SubElement(assoc, 'memberEnd', attrib={'xmi:idref': end2_id})

            # Add 'navigableOwnedEnd' as in the original code for better compatibility.
            # It usually indicates which ends are navigable. Bi-directional is common here.
            ET.SubElement(assoc, 'navigableOwnedEnd', attrib={'xmi:idref': end1_id})
            ET.SubElement(assoc, 'navigableOwnedEnd', attrib={'xmi:idref': end2_id})

        elif elem_type == 'Include':
            source_element = xmi_elements[source_id]
            ET.SubElement(source_element, 'include', attrib={
                'xmi:type': 'uml:Include',
                'xmi:id': elem_id,
                'addition': target_xmi_id
            })

        elif elem_type == 'Extend':
            source_element = xmi_elements[source_id]
            ET.SubElement(source_element, 'extend', attrib={
                'xmi:type': 'uml:Extend',
                'xmi:id': elem_id,
                'extendedCase': target_xmi_id
            })

        elif elem_type == 'Generalization':
            source_element = xmi_elements[source_id]
            ET.SubElement(source_element, 'generalization', attrib={
                'xmi:type': 'uml:Generalization',
                'xmi:id': elem_id,
                'general': target_xmi_id
            })

    # Generate XML string and write to file
    tree = ET.ElementTree(xmi_root)
    try:
        ET.indent(tree, space="  ", level=0)
    except AttributeError:
        pass # ET.indent is Python 3.9+
        
    tree.write(output_file, encoding='utf-8', xml_declaration=True)
    print(f"XMI file has been generated and saved as {output_file}")


# Example usage with the new JSON structure for the smart farm
if __name__ == "__main__":
    input_json_str = """
    {
      "model": [
        { "id": "model-smart-farm", "name": "智能农场用例模型" }
      ],
      "elements": [
        { "id": "pkg-main-usecases", "type": "Package", "name": "主要用例", "parentId": "model-smart-farm" },
        { "id": "actor1", "type": "Actor", "name": "农场管理员", "parentId": "pkg-main-usecases" },
        { "id": "useCase1", "type": "UseCase", "name": "调节温室温度", "parentId": "pkg-main-usecases" },
        { "id": "assoc-1", "type": "Association", "sourceId": "actor1", "targetId": "useCase1", "parentId": "pkg-main-usecases" },
        { "id": "useCase4", "type": "UseCase", "name": "监控土壤湿度", "parentId": "pkg-main-usecases" },
        { "id": "rel-include-1", "type": "Include", "sourceId": "useCase1", "targetId": "useCase4", "parentId": "pkg-main-usecases" }
      ]
    }
    """ # Using a smaller subset for clarity in explanation
    
    full_input_json_str = """
    {
      "model": [ { "id": "model-smart-farm", "name": "智能农场用例模型" } ],
      "elements": [
        { "id": "pkg-main-usecases", "type": "Package", "name": "主要用例", "parentId": "model-smart-farm" },
        { "id": "actor1", "type": "Actor", "name": "农场管理员", "parentId": "pkg-main-usecases" }, { "id": "actor2", "type": "Actor", "name": "技术支持团队", "parentId": "pkg-main-usecases" }, { "id": "actor3", "type": "Actor", "name": "自动播种机", "parentId": "pkg-main-usecases" }, { "id": "actor4", "type": "Actor", "name": "采摘机器人", "parentId": "pkg-main-usecases" }, { "id": "actor5", "type": "Actor", "name": "无人机", "parentId": "pkg-main-usecases" },
        { "id": "useCase1", "type": "UseCase", "name": "调节温室温度", "parentId": "pkg-main-usecases" }, { "id": "useCase2", "type": "UseCase", "name": "调节湿度", "parentId": "pkg-main-usecases" }, { "id": "useCase3", "type": "UseCase", "name": "调节光照", "parentId": "pkg-main-usecases" }, { "id": "useCase4", "type": "UseCase", "name": "监控土壤湿度", "parentId": "pkg-main-usecases" }, { "id": "useCase5", "type": "UseCase", "name": "自动灌溉", "parentId": "pkg-main-usecases" }, { "id": "useCase6", "type": "UseCase", "name": "自动施肥", "parentId": "pkg-main-usecases" }, { "id": "useCase7", "type": "UseCase", "name": "自动施药", "parentId": "pkg-main-usecases" }, { "id": "useCase8", "type": "UseCase", "name": "自动操作", "parentId": "pkg-main-usecases" }, { "id": "useCase9", "type": "UseCase", "name": "监测作物健康", "parentId": "pkg-main-usecases" }, { "id": "useCase10", "type": "UseCase", "name": "发出预警", "parentId": "pkg-main-usecases" }, { "id": "useCase11", "type": "UseCase", "name": "检查设备", "parentId": "pkg-main-usecases" }, { "id": "useCase12", "type": "UseCase", "name": "远程修复", "parentId": "pkg-main-usecases" }, { "id": "useCase13", "type": "UseCase", "name": "派人修理", "parentId": "pkg-main-usecases" }, { "id": "useCase14", "type": "UseCase", "name": "推送软件更新", "parentId": "pkg-main-usecases" },
        { "id": "assoc-1", "type": "Association", "sourceId": "actor1", "targetId": "useCase1", "parentId": "pkg-main-usecases" }, { "id": "assoc-2", "type": "Association", "sourceId": "actor1", "targetId": "useCase2", "parentId": "pkg-main-usecases" }, { "id": "assoc-3", "type": "Association", "sourceId": "actor1", "targetId": "useCase3", "parentId": "pkg-main-usecases" }, { "id": "assoc-4", "type": "Association", "sourceId": "actor1", "targetId": "useCase4", "parentId": "pkg-main-usecases" }, { "id": "assoc-5", "type": "Association", "sourceId": "actor1", "targetId": "useCase5", "parentId": "pkg-main-usecases" }, { "id": "assoc-6", "type": "Association", "sourceId": "actor1", "targetId": "useCase6", "parentId": "pkg-main-usecases" }, { "id": "assoc-7", "type": "Association", "sourceId": "actor1", "targetId": "useCase7", "parentId": "pkg-main-usecases" }, { "id": "assoc-8", "type": "Association", "sourceId": "actor1", "targetId": "useCase9", "parentId": "pkg-main-usecases" }, { "id": "assoc-9", "type": "Association", "sourceId": "actor1", "targetId": "useCase10", "parentId": "pkg-main-usecases" }, { "id": "assoc-10", "type": "Association", "sourceId": "actor2", "targetId": "useCase11", "parentId": "pkg-main-usecases" }, { "id": "assoc-11", "type": "Association", "sourceId": "actor2", "targetId": "useCase12", "parentId": "pkg-main-usecases" }, { "id": "assoc-12", "type": "Association", "sourceId": "actor2", "targetId": "useCase13", "parentId": "pkg-main-usecases" }, { "id": "assoc-13", "type": "Association", "sourceId": "actor2", "targetId": "useCase14", "parentId": "pkg-main-usecases" }, { "id": "assoc-14", "type": "Association", "sourceId": "actor3", "targetId": "useCase8", "parentId": "pkg-main-usecases" }, { "id": "assoc-15", "type": "Association", "sourceId": "actor4", "targetId": "useCase8", "parentId": "pkg-main-usecases" }, { "id": "assoc-16", "type": "Association", "sourceId": "actor5", "targetId": "useCase9", "parentId": "pkg-main-usecases" },
        { "id": "rel-include-1", "type": "Include", "sourceId": "useCase1", "targetId": "useCase4", "parentId": "pkg-main-usecases" }, { "id": "rel-include-2", "type": "Include", "sourceId": "useCase2", "targetId": "useCase4", "parentId": "pkg-main-usecases" }, { "id": "rel-include-3", "type": "Include", "sourceId": "useCase3", "targetId": "useCase4", "parentId": "pkg-main-usecases" },
        { "id": "rel-extend-1", "type": "Extend", "sourceId": "useCase4", "targetId": "useCase5", "parentId": "pkg-main-usecases" }, { "id": "rel-extend-2", "type": "Extend", "sourceId": "useCase4", "targetId": "useCase6", "parentId": "pkg-main-usecases" }, { "id": "rel-extend-3", "type": "Extend", "sourceId": "useCase4", "targetId": "useCase7", "parentId": "pkg-main-usecases" }, { "id": "rel-extend-4", "type": "Extend", "sourceId": "useCase8", "targetId": "useCase3", "parentId": "pkg-main-usecases" }, { "id": "rel-extend-5", "type": "Extend", "sourceId": "useCase8", "targetId": "useCase4", "parentId": "pkg-main-usecases" }, { "id": "rel-extend-6", "type": "Extend", "sourceId": "useCase9", "targetId": "useCase10", "parentId": "pkg-main-usecases" }, { "id": "rel-extend-7", "type": "Extend", "sourceId": "useCase11", "targetId": "useCase12", "parentId": "pkg-main-usecases" }, { "id": "rel-extend-8", "type": "Extend", "sourceId": "useCase11", "targetId": "useCase13", "parentId": "pkg-main-usecases" }
      ]
    }
    """

    data = json.loads(full_input_json_str)
    generate_xmi_from_new_json(data, 'smart_farm_output_corrected.xmi')