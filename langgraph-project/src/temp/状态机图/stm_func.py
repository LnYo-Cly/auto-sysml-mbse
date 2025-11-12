import json
import xml.etree.ElementTree as ET
from xml.dom import minidom
import traceback

# --- 定义命名空间字典 ---
NAMESPACES_SM_DICT = {
    "xmlns:xmi": "http://www.omg.org/spec/XMI/20131001",
    "xmlns:uml": "http://www.omg.org/spec/UML/20131001",
    "xmlns:sysml": "http://www.omg.org/spec/SysML/20181001/SysML",
}

# --- 全局字典 ---
elements_by_id_sm = {} # Stores all elements from JSON by their ID
# Stereotype application lists
blocks_data_list_sm = []
activities_data_list_sm = [] # For TestCases or other stereotyped activities
# No need for processed_elements_sm if we build top-down carefully

def generate_stereotype_id(base_id, suffix="application"):
    clean_base = str(base_id).replace("-", "_")
    clean_suffix = str(suffix).replace("-", "_")
    if base_id.startswith("_") and suffix == "application":
        return f"{base_id}_application"
    elif base_id.startswith("_") and suffix.endswith("_"):
         return f"{base_id}{suffix}"
    return f"_{clean_base}_{clean_suffix}"

def preprocess_sm_data(json_data):
    global elements_by_id_sm, blocks_data_list_sm, activities_data_list_sm

    elements_by_id_sm.clear()
    blocks_data_list_sm.clear()
    activities_data_list_sm.clear()

    if "elements" not in json_data:
        print("错误：JSON数据必须包含 'elements' 键。")
        return False, None, None

    elements_list = json_data["elements"]
    try:
        for elem in elements_list:
            elements_by_id_sm[elem["id"]] = elem
    except KeyError as e:
        print(f"错误：JSON元素缺少 'id' 字段：{e}")
        return False, None, None
    except TypeError as e:
        print(f"错误：迭代JSON元素列表时出现问题：{e}")
        return False, None, None

    model_id_sm = "model-root-sm-uuid"
    model_name_sm = "DefaultStateMachineModel"
    model_type_sm = "uml:Model" # Default, can be overridden by JSON
    if "model" in json_data and json_data["model"]:
        try:
            model_data = json_data["model"][0]
            model_name_sm = model_data.get("name", model_name_sm)
            model_id_sm = model_data.get("id", model_id_sm)
            # Store model also in elements_by_id_sm if it has an ID for parentId referencing
            if "id" in model_data:
                 elements_by_id_sm[model_data["id"]] = model_data 
            if model_data.get("type") == "Model": # or any other type like Profile
                model_type_sm = "uml:" + model_data["type"]


        except (IndexError, AttributeError, KeyError):
            print("警告：无法从JSON读取模型名称或ID，使用默认值。")
    
    # Collect data for stereotypes
    for elem_id, elem_data in elements_by_id_sm.items():
        elem_type = elem_data.get("type")
        if elem_type == "Block":
            blocks_data_list_sm.append(elem_data)
        elif elem_type == "Activity" and elem_data.get("stereotype") == "TestCase":
            activities_data_list_sm.append(elem_data)
            
    return True, model_id_sm, model_name_sm, model_type_sm


def create_behavior_activity_xml(parent_xml_element, behavior_type_tag, behavior_json_data, owner_name=""):
    """
    Creates entry, exit, doActivity, or effect behavior (an Activity wrapper).
    behavior_type_tag: "entry", "exit", "doActivity", "effect"
    behavior_json_data: The JSON object for this behavior, e.g., {"wrapperActivityId": "...", "calledBehaviorId": "..."}
    owner_name: Name of the state or transition owning this behavior, for naming the wrapper activity.
    """
    if not behavior_json_data or "wrapperActivityId" not in behavior_json_data:
        print(f"警告: 行为 {behavior_type_tag} for {owner_name} 缺少 wrapperActivityId 或数据。")
        return None

    wrapper_activity_id = behavior_json_data["wrapperActivityId"]
    called_behavior_id = behavior_json_data.get("calledBehaviorId")

    activity_attrs = {"xmi:type": "uml:Activity", "xmi:id": wrapper_activity_id}
    
    # --- MODIFICATION START ---
    # Set the name of the wrapper activity to the name of the called behavior, if it exists
    activity_name_to_set = None
    if called_behavior_id and called_behavior_id in elements_by_id_sm:
        called_behavior_data = elements_by_id_sm[called_behavior_id]
        activity_name_to_set = called_behavior_data.get("name")
    
    if activity_name_to_set:
        activity_attrs["name"] = activity_name_to_set
    # else:
        # Fallback name if called behavior has no name or no called behavior
        # activity_attrs["name"] = f"{owner_name}_{behavior_type_tag}_Action" if owner_name else f"{behavior_type_tag}_Action"
    # --- MODIFICATION END ---
        
    activity_elem = ET.SubElement(parent_xml_element, behavior_type_tag, attrib=activity_attrs)

    if called_behavior_id:
        initial_node_id = wrapper_activity_id + "_initial"
        cba_id = wrapper_activity_id + "_cba"
        final_node_id = wrapper_activity_id + "_final"
        cf1_id = wrapper_activity_id + "_cf1"
        cf2_id = wrapper_activity_id + "_cf2"

        ET.SubElement(activity_elem, "node", {"xmi:type": "uml:InitialNode", "xmi:id": initial_node_id, "visibility": "public"})
        
        # The CallBehaviorAction can also have a name, often mirroring the called behavior
        cba_attrs = {"xmi:type": "uml:CallBehaviorAction", "xmi:id": cba_id, "behavior": called_behavior_id, "visibility": "public"}
        if activity_name_to_set: # Optionally, name the CallBehaviorAction too
            cba_attrs["name"] = f"Call_{activity_name_to_set}"
        ET.SubElement(activity_elem, "node", attrib=cba_attrs)
        
        ET.SubElement(activity_elem, "node", {"xmi:type": "uml:ActivityFinalNode", "xmi:id": final_node_id, "visibility": "public"})
        ET.SubElement(activity_elem, "edge", {"xmi:type": "uml:ControlFlow", "xmi:id": cf1_id, "source": initial_node_id, "target": cba_id, "visibility": "public"})
        ET.SubElement(activity_elem, "edge", {"xmi:type": "uml:ControlFlow", "xmi:id": cf2_id, "source": cba_id, "target": final_node_id, "visibility": "public"})
    return activity_elem

def create_element_xml(elem_id, parent_xml_element):
    """
    Creates an XML element based on JSON data. This is the main recursive dispatcher.
    This function will be called for children of Package, Model, Region, State (for its regions).
    """
    if elem_id not in elements_by_id_sm:
        print(f"错误: 尝试创建未在JSON中定义的元素: {elem_id}")
        return None
    
    elem_data = elements_by_id_sm[elem_id]
    elem_type = elem_data["type"]
    elem_name = elem_data.get("name")
    elem_xmi_id = elem_data["id"] # Should be same as elem_id

    xml_elem = None
    processed_children = False # Flag to avoid double processing of children

    # --- Package ---
    if elem_type == "Package":
        attrs = {"xmi:type": "uml:Package", "xmi:id": elem_xmi_id}
        if elem_name: attrs["name"] = elem_name
        xml_elem = ET.SubElement(parent_xml_element, 'packagedElement', attrib=attrs)
        # Process children of the package
        for child_id in find_children_ids(elem_xmi_id):
            create_element_xml(child_id, xml_elem)
        processed_children = True

    # --- Block (as uml:Class) ---
    elif elem_type == "Block":
        attrs = {"xmi:type": "uml:Class", "xmi:id": elem_xmi_id}
        if elem_name: attrs["name"] = elem_name
        xml_elem = ET.SubElement(parent_xml_element, 'packagedElement', attrib=attrs)
        
        classifier_behavior_id = elem_data.get("classifierBehaviorId")
        if classifier_behavior_id:
            ET.SubElement(xml_elem, "classifierBehavior", {"xmi:idref": classifier_behavior_id})
            # The StateMachine itself will be created when its ID is encountered as a child
            # of this block or its containing package.
            # We need to ensure the classifierBehavior SM is created *under* the block as ownedBehavior.

        # Process children of the block (e.g., an owned StateMachine)
        for child_id in find_children_ids(elem_xmi_id):
            create_element_xml(child_id, xml_elem) # StateMachine will be handled as ownedBehavior
        processed_children = True
        
    # --- StateMachine ---
    elif elem_type == "StateMachine":
        attrs = {"xmi:type": "uml:StateMachine", "xmi:id": elem_xmi_id}
        if elem_name: attrs["name"] = elem_name
        
        # Determine if it's ownedBehavior (e.g., by a Block) or packagedElement
        parent_id = elem_data.get("parentId")
        parent_data = elements_by_id_sm.get(parent_id) if parent_id else None
        
        tag_name = 'packagedElement'
        if parent_data and parent_data.get("type") == "Block":
            tag_name = 'ownedBehavior'
            
        xml_elem = ET.SubElement(parent_xml_element, tag_name, attrib=attrs)
        
        # StateMachine must have at least one region from JSON
        for child_id in find_children_ids(elem_xmi_id):
            if elements_by_id_sm.get(child_id, {}).get("type") == "Region":
                create_element_xml(child_id, xml_elem) # Pass StateMachine xml_elem as parent for Region
        processed_children = True # Children (Regions) are handled

    # --- Region ---
    elif elem_type == "Region":
        attrs = {"xmi:type": "uml:Region", "xmi:id": elem_xmi_id, "visibility": "public"}
        if elem_name: attrs["name"] = elem_name
        xml_elem = ET.SubElement(parent_xml_element, 'region', attrib=attrs)
        
        # Process children of the region (Subvertices: State, Pseudostate; and Transitions)
        # Create subvertices first
        for child_id in find_children_ids(elem_xmi_id):
            child_elem_data = elements_by_id_sm.get(child_id)
            if child_elem_data and child_elem_data.get("type") in ["State", "Pseudostate"]:
                create_element_xml(child_id, xml_elem)
        # Then create transitions
        for child_id in find_children_ids(elem_xmi_id):
            child_elem_data = elements_by_id_sm.get(child_id)
            if child_elem_data and child_elem_data.get("type") == "Transition":
                create_element_xml(child_id, xml_elem)
        processed_children = True


    # --- State ---
    elif elem_type == "State":
        attrs = {"xmi:type": "uml:State", "xmi:id": elem_xmi_id, "visibility": "public"}
        if elem_name: attrs["name"] = elem_name # elem_name is the name of the State itself
        
        xml_elem = ET.SubElement(parent_xml_element, 'subvertex', attrib=attrs)

        # --- MODIFICATION: Pass the state's name (elem_name) as owner_name ---
        # Entry, Exit, DoActivity
        current_owner_name = elem_name if elem_name else elem_xmi_id # Use state name or ID for behavior naming context

        if "entry" in elem_data:
            create_behavior_activity_xml(xml_elem, "entry", elem_data["entry"], current_owner_name)
        if "exit" in elem_data:
            create_behavior_activity_xml(xml_elem, "exit", elem_data["exit"], current_owner_name)
        if "doActivity" in elem_data:
            create_behavior_activity_xml(xml_elem, "doActivity", elem_data["doActivity"], current_owner_name)

        # Connection Points (Pseudostates that are children of this State)
        if "connectionPoints" in elem_data:
            for cp_id in elem_data["connectionPoints"]:
                # The Pseudostate itself will be created when its ID is encountered,
                # but we need to ensure it's created as a <connectionPoint> under the State.
                # This requires the Pseudostate creation logic to check its parent.
                # The create_element_xml for Pseudostate will handle this.
                if cp_id in elements_by_id_sm and elements_by_id_sm[cp_id].get("parentId") == elem_xmi_id:
                     create_element_xml(cp_id, xml_elem) # Pass State as parent for connectionPoint

        # Regions (for composite states)
        if "regions" in elem_data:
            for region_id in elem_data["regions"]:
                 if region_id in elements_by_id_sm and elements_by_id_sm[region_id].get("parentId") == elem_xmi_id:
                    create_element_xml(region_id, xml_elem) # Pass State as parent for its Region
        processed_children = True # ConnectionPoints and Regions handled

    # --- Pseudostate ---
    elif elem_type == "Pseudostate":
        pseudo_kind = elem_data.get("kind")
        attrs = {"xmi:id": elem_xmi_id, "visibility": "public"}
        if elem_name: attrs["name"] = elem_name

        tag_name = 'subvertex' # Default for Region's Pseudostates
        # Check if this Pseudostate is a connectionPoint of a State
        parent_id = elem_data.get("parentId")
        parent_data = elements_by_id_sm.get(parent_id) if parent_id else None
        if parent_data and parent_data.get("type") == "State" and pseudo_kind in ["entryPoint", "exitPoint"]:
            tag_name = 'connectionPoint'
        
        if pseudo_kind == "initial":
            attrs["xmi:type"] = "uml:Pseudostate" # kind="initial" is often implicit
        elif pseudo_kind == "finalState" or pseudo_kind == "final": # Accept "final" from JSON
            attrs["xmi:type"] = "uml:FinalState"
        elif pseudo_kind in ["choice", "junction", "entryPoint", "exitPoint", "fork", "join", "deepHistory", "shallowHistory"]:
            attrs["xmi:type"] = "uml:Pseudostate"
            attrs["kind"] = pseudo_kind
        else:
            print(f"警告: 未知 Pseudostate kind '{pseudo_kind}' for {elem_xmi_id}. Using generic Pseudostate.")
            attrs["xmi:type"] = "uml:Pseudostate"
            if pseudo_kind: attrs["kind"] = pseudo_kind
        
        xml_elem = ET.SubElement(parent_xml_element, tag_name, attrib=attrs)
        # Pseudostates typically don't have further children in this context.
        processed_children = True


    # --- Transition ---
    elif elem_type == "Transition":
        attrs = {"xmi:type": "uml:Transition", "xmi:id": elem_xmi_id, "visibility": "public"}
        if elem_name: attrs["name"] = elem_name
        
        source_id = elem_data.get("sourceId")
        target_id = elem_data.get("targetId")
        if not source_id or not target_id:
            print(f"警告: Transition {elem_xmi_id} 缺少 sourceId 或 targetId。跳过。")
            return None
        attrs["source"] = source_id
        attrs["target"] = target_id
        
        xml_elem = ET.SubElement(parent_xml_element, 'transition', attrib=attrs)

        # Triggers
        if "triggerIds" in elem_data:
            for i, event_id_ref in enumerate(elem_data["triggerIds"]):
                # The actual Event element (SignalEvent, AnyReceiveEvent etc.) should be defined elsewhere
                # and referenced here by its ID.
                # A trigger references an event.
                trigger_id = f"{elem_xmi_id}_trigger_{i}"
                trigger_name = elements_by_id_sm.get(event_id_ref, {}).get("name", f"trigger_for_{event_id_ref}")
                # If event_id_ref itself is the trigger name if it's not an ID of an event element
                
                trigger_attrs = {"xmi:type": "uml:Trigger", "xmi:id": trigger_id, "visibility": "public"}
                # Name of trigger is usually based on the event it refers to.
                # trigger_attrs["name"] = trigger_name # Optional for trigger itself
                
                trigger_sub_elem = ET.SubElement(xml_elem, "trigger", trigger_attrs)
                ET.SubElement(trigger_sub_elem, "event", {"xmi:idref": event_id_ref})


        # Guard
        guard_json = elem_data.get("guard")
        if guard_json and "expression" in guard_json:
            guard_id = f"{elem_xmi_id}_guard"
            spec_id = f"{elem_xmi_id}_guard_spec"
            guard_elem = ET.SubElement(xml_elem, "guard", {"xmi:type": "uml:Constraint", "xmi:id": guard_id, "visibility": "public"})
            spec_elem = ET.SubElement(guard_elem, "specification", {"xmi:type": "uml:OpaqueExpression", "xmi:id": spec_id})
            ET.SubElement(spec_elem, "body").text = guard_json["expression"]
            if "language" in guard_json: # Though 'language' for OpaqueExpression is usually an attribute of OpaqueExpression
                 # spec_elem.set("language", guard_json["language"]) # Not standard for OpaqueExpression body, but for the expression itself
                 ET.SubElement(spec_elem, "language").text = guard_json["language"]


        # Effect
        if "effect" in elem_data:
            # --- MODIFICATION: Pass the transition's name (elem_name) as owner_name ---
            current_owner_name = elem_name if elem_name else elem_xmi_id # Use transition name or ID
            create_behavior_activity_xml(xml_elem, "effect", elem_data["effect"], current_owner_name)
        processed_children = True

    # --- Activity (standalone, referenced by behaviors) ---
    elif elem_type == "Activity":
        attrs = {"xmi:type": "uml:Activity", "xmi:id": elem_xmi_id}
        if elem_name: attrs["name"] = elem_name
        xml_elem = ET.SubElement(parent_xml_element, 'packagedElement', attrib=attrs)
        # Internal structure of these general activities is not detailed in this JSON example
        # unless they are the "wrapper" activities for entry/exit/do/effect.
        processed_children = True

    # --- Signal ---
    elif elem_type == "Signal":
        attrs = {"xmi:type": "uml:Signal", "xmi:id": elem_xmi_id}
        if elem_name: attrs["name"] = elem_name
        xml_elem = ET.SubElement(parent_xml_element, 'packagedElement', attrib=attrs)
        processed_children = True
        
    # --- SignalEvent ---
    elif elem_type == "SignalEvent":
        attrs = {"xmi:type": "uml:SignalEvent", "xmi:id": elem_xmi_id}
        if elem_name: attrs["name"] = elem_name
        signal_id_ref = elem_data.get("signalId")
        if signal_id_ref:
            attrs["signal"] = signal_id_ref # xmi:idref is implicit for references
        xml_elem = ET.SubElement(parent_xml_element, 'packagedElement', attrib=attrs)
        processed_children = True

    # --- Generic Event (e.g., TimeEvent, ChangeEvent, AnyReceiveEvent if not SignalEvent) ---
    # Your JSON uses "Event" for "event-timeout-uuid" and "event-forced-open-uuid"
    # These likely map to specific UML event types or a generic AnyReceiveEvent if not specified.
    # For now, mapping to uml:AnyReceiveEvent as a placeholder if more specific type isn't given.
    elif elem_type == "Event":
        attrs = {"xmi:type": "uml:AnyReceiveEvent", "xmi:id": elem_xmi_id} # Defaulting to AnyReceiveEvent
        # A more specific type (e.g., TimeEvent, ChangeEvent) would need more info in JSON
        # For example, if kind: "TimeEvent", then type="uml:TimeEvent"
        if elem_name: attrs["name"] = elem_name
        xml_elem = ET.SubElement(parent_xml_element, 'packagedElement', attrib=attrs)
        processed_children = True
        
    else:
        print(f"信息：跳过未知或未处理的元素类型 {elem_type} (ID: {elem_xmi_id})")
        return None

    # Fallback for children processing if not handled by specific type logic above
    if not processed_children:
        for child_id in find_children_ids(elem_xmi_id):
            create_element_xml(child_id, xml_elem if xml_elem is not None else parent_xml_element)
            
    return xml_elem

def find_children_ids(parent_id_to_find):
    """Finds all elements in JSON whose parentId matches parent_id_to_find."""
    children = []
    for elem_id, elem_data in elements_by_id_sm.items():
        if elem_data.get("parentId") == parent_id_to_find:
            children.append(elem_id)
    # Sorting might be useful for deterministic output, e.g., by name or ID if available
    # children.sort() 
    return children


def json_to_statemachine_xmi(json_data_str):
    global elements_by_id_sm, blocks_data_list_sm, activities_data_list_sm

    try:
        json_data = json.loads(json_data_str)
    except json.JSONDecodeError as e:
        print(f"错误: JSON 解析失败 - {e}")
        return None

    success, model_id_sm, model_name_sm, model_type_sm_str = preprocess_sm_data(json_data)
    if not success:
        return None

    xmi_root = ET.Element("xmi:XMI")
    for ns_attr, ns_uri in NAMESPACES_SM_DICT.items():
        xmi_root.set(ns_attr, ns_uri)

    # Model element is the direct child of xmi:XMI
    # model_attrib = {"xmi:type": "uml:Model", "xmi:id": model_id_sm, "name": model_name_sm}
    model_attrib = {"xmi:type": model_type_sm_str, "xmi:id": model_id_sm} # Use type from JSON
    if model_name_sm: model_attrib["name"] = model_name_sm
    model_element_xml = ET.SubElement(xmi_root, model_type_sm_str, attrib=model_attrib) # Tag name without "uml:"


    # Process top-level elements (children of the model)
    for elem_id in find_children_ids(model_id_sm):
        create_element_xml(elem_id, model_element_xml)
    
    # Apply Stereotypes (at the root XMI level, after model content)
    for blk_data in blocks_data_list_sm:
        blk_uml_id = blk_data["id"]
        # Ensure the block element exists in the parsed elements (it should)
        if blk_uml_id in elements_by_id_sm: 
            attrs = {
                "xmi:id": generate_stereotype_id(blk_uml_id, "block_stereotype_app"),
                "base_Class": blk_uml_id
            }
            ET.SubElement(xmi_root, "sysml:Block", attrib=attrs)

    for act_data in activities_data_list_sm: # Only for activities marked as TestCase
        act_uml_id = act_data["id"]
        if act_uml_id in elements_by_id_sm and act_data.get("stereotype") == "TestCase":
            tc_attrs = {
                "xmi:id": generate_stereotype_id(act_uml_id, "testcase_stereotype_app"),
                "base_Activity": act_uml_id
            }
            # Add specific TestCase attributes like verdict parameters if needed from JSON
            # (This would require more detail in create_element_xml for Activity or here)
            ET.SubElement(xmi_root, "sysml:TestCase", attrib=tc_attrs)

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

# --- Your Provided JSON ---
correct_json_str = """
{
  "model": [
    {
      "id": "model-door-access-sm-uuid",
      "name": "门禁系统模型",
      "type": "Model"
    }
  ],
  "elements": [
    {
      "id": "pkg-door-controller-uuid",
      "type": "Package",
      "name": "门控制器模块",
      "parentId": "model-door-access-sm-uuid"
    },
    {
      "id": "pkg-door-behaviors-uuid",
      "type": "Package",
      "name": "门禁行为库",
      "parentId": "model-door-access-sm-uuid"
    },
    {
      "id": "blk-door-controller-uuid",
      "type": "Block",
      "name": "门控制器",
      "parentId": "pkg-door-controller-uuid",
      "classifierBehaviorId": "sm-door-access-uuid"
    },
    {
      "id": "sm-door-access-uuid",
      "type": "StateMachine",
      "name": "门禁状态机",
      "parentId": "blk-door-controller-uuid"
    },
    {
      "id": "region-door-main-uuid",
      "type": "Region",
      "name": "主区域",
      "parentId": "sm-door-access-uuid"
    },
    {
      "id": "ps-main-initial-uuid",
      "type": "Pseudostate",
      "kind": "initial",
      "parentId": "region-door-main-uuid"
    },
    {
      "id": "state-locked-uuid",
      "type": "State",
      "name": "锁定",
      "parentId": "region-door-main-uuid",
      "isComposite": true,
      "connectionPoints": [
        "ps-locked-entry-uuid",
        "ps-locked-exit-uuid"
      ],
      "regions": [
        "region-locked-sub-uuid"
      ]
    },
    {
      "id": "ps-locked-entry-uuid",
      "type": "Pseudostate",
      "kind": "entryPoint",
      "name": "ep_lock",
      "parentId": "state-locked-uuid"
    },
    {
      "id": "ps-locked-exit-uuid",
      "type": "Pseudostate",
      "kind": "exitPoint",
      "name": "xp_lock",
      "parentId": "state-locked-uuid"
    },
    {
      "id": "region-locked-sub-uuid",
      "type": "Region",
      "name": "内部安全检查",
      "parentId": "state-locked-uuid"
    },
    {
      "id": "ps-locked-sub-initial-uuid",
      "type": "Pseudostate",
      "kind": "initial",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "state-selfcheck-uuid",
      "type": "State",
      "name": "自检",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "ps-locked-sub-final-uuid",
      "type": "Pseudostate",
      "kind": "final",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "state-unlocking-uuid",
      "type": "State",
      "name": "开锁中",
      "parentId": "region-door-main-uuid",
      "entry": {
        "wrapperActivityId": "wrapper-entry-unlocking-uuid",
        "calledBehaviorId": "act-unlock-bolt-uuid"
      },
      "doActivity": {
        "wrapperActivityId": "wrapper-do-unlocking-uuid",
        "calledBehaviorId": "act-keep-door-open-uuid"
      },
      "exit": {
        "wrapperActivityId": "wrapper-exit-unlocking-uuid",
        "calledBehaviorId": "act-check-door-closed-uuid"
      }
    },
    {
      "id": "state-alarm-uuid",
      "type": "State",
      "name": "报警",
      "parentId": "region-door-main-uuid"
    },
    {
      "id": "trans-initial-to-locked-uuid",
      "type": "Transition",
      "sourceId": "ps-main-initial-uuid",
      "targetId": "ps-locked-entry-uuid",
      "parentId": "region-door-main-uuid"
    },
    {
      "id": "trans-locked-to-unlocking-uuid",
      "type": "Transition",
      "sourceId": "ps-locked-exit-uuid",
      "targetId": "state-unlocking-uuid",
      "parentId": "region-door-main-uuid",
      "triggerIds": [
        "event-valid-unlock-signal-uuid"
      ],
      "guard": {
        "expression": "安全系统已解除 == true",
        "language": "English"
      },
      "effect": {
        "wrapperActivityId": "wrapper-effect-t2-uuid",
        "calledBehaviorId": "act-log-unlock-attempt-uuid"
      }
    },
    {
      "id": "trans-unlocking-to-locked-uuid",
      "type": "Transition",
      "sourceId": "state-unlocking-uuid",
      "targetId": "state-locked-uuid",
      "parentId": "region-door-main-uuid",
      "triggerIds": [
        "event-timeout-uuid"
      ],
      "effect": {
        "wrapperActivityId": "wrapper-effect-t3-uuid",
        "calledBehaviorId": "act-auto-lock-uuid"
      }
    },
    {
      "id": "trans-locked-to-alarm-uuid",
      "type": "Transition",
      "sourceId": "state-locked-uuid",
      "targetId": "state-alarm-uuid",
      "parentId": "region-door-main-uuid",
      "triggerIds": [
        "event-forced-open-uuid"
      ],
      "effect": {
        "wrapperActivityId": "wrapper-effect-t4-uuid",
        "calledBehaviorId": "act-sound-alarm-uuid"
      }
    },
    {
      "id": "trans-entrypoint-to-subinitial-uuid",
      "type": "Transition",
      "sourceId": "ps-locked-entry-uuid",
      "targetId": "ps-locked-sub-initial-uuid",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "trans-subinitial-to-selfcheck-uuid",
      "type": "Transition",
      "sourceId": "ps-locked-sub-initial-uuid",
      "targetId": "state-selfcheck-uuid",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "trans-selfcheck-to-subfinal-uuid",
      "type": "Transition",
      "sourceId": "state-selfcheck-uuid",
      "targetId": "ps-locked-sub-final-uuid",
      "parentId": "region-locked-sub-uuid"
    },
    {
      "id": "sig-valid-unlock-uuid",
      "type": "Signal",
      "name": "有效开锁信号",
      "parentId": "pkg-door-controller-uuid"
    },
    {
      "id": "event-valid-unlock-signal-uuid",
      "type": "SignalEvent",
      "name": "有效开锁信号事件",
      "signalId": "sig-valid-unlock-uuid",
      "parentId": "pkg-door-controller-uuid"
    },
    {
      "id": "event-timeout-uuid",
      "type": "Event", 
      "name": "超时事件",
      "parentId": "pkg-door-controller-uuid"
    },
    {
      "id": "event-forced-open-uuid",
      "type": "Event",
      "name": "强制开门事件",
      "parentId": "pkg-door-controller-uuid"
    },
    {
      "id": "act-log-unlock-attempt-uuid",
      "type": "Activity",
      "name": "记录开锁尝试",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-unlock-bolt-uuid",
      "type": "Activity",
      "name": "解锁门闩",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-keep-door-open-uuid",
      "type": "Activity",
      "name": "保持门锁打开",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-check-door-closed-uuid",
      "type": "Activity",
      "name": "检查门是否已关闭",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-auto-lock-uuid",
      "type": "Activity",
      "name": "自动上锁",
      "parentId": "pkg-door-behaviors-uuid"
    },
    {
      "id": "act-sound-alarm-uuid",
      "type": "Activity",
      "name": "鸣响警报",
      "parentId": "pkg-door-behaviors-uuid"
    }
  ]
}
"""

if __name__ == '__main__':
    print("--- 正在使用 Corrected JSON 生成状态机图XMI ---")
    generated_sm_xmi = json_to_statemachine_xmi(correct_json_str)

    if generated_sm_xmi:
        print("\n--- 生成的状态机 XMI (Corrected): ---")
        print(generated_sm_xmi)
        with open("statemachine_diagram_corrected.xmi", "w", encoding="utf-8") as f:
            f.write(generated_sm_xmi)
        print("\n--- XMI 已保存到 statemachine_diagram_corrected.xmi ---")
    else:
        print("--- 状态机 XMI (Corrected) 生成失败 ---")