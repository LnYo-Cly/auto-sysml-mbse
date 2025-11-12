#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
import_fan_model.py

将 fan 系统 JSON（fan_model.json）导入到 Neo4j，
节点标签和关系设计覆盖 JSON 中所有元素及其关系，
并保证可逆向导出相同结构。
"""
import json
from neo4j import GraphDatabase

# ==== Neo4j 连接配置 ====
URI = "bolt://localhost:7687"
USER = "neo4j"
PASSWORD = "123456789"


def load_json_stripping_comments(path: str) -> dict:

    return  {
  "model": {
    "id": "model-fan-uuid",
    "name": "FanSystemModel",
    "diagrams": [
      "diag-fan-bdd",
      "diag-fan-ibd"
    ]
  },
  "elements": [
    {
      "id": "pkg-fan-uuid",
      "type": "Package",
      "name": "FanSystemPackage",
      "parentId": "model-fan-uuid"
    },
    {
      "id": "unit-percent-uuid",
      "type": "Unit",
      "name": "%",
      "parentId": "pkg-fan-uuid",
      "symbol": "%"
    },
    {
      "id": "vt-percentage-uuid",
      "type": "ValueType",
      "name": "Percentage",
      "parentId": "pkg-fan-uuid",
      "baseType": "Real",
      "unitId": "unit-percent-uuid"
    },
    {
      "id": "enum-fanspeed-uuid",
      "type": "Enumeration",
      "name": "FanSpeedLevel",
      "parentId": "pkg-fan-uuid",
      "literals": [
        "lit-fs-off",
        "lit-fs-low",
        "lit-fs-med",
        "lit-fs-high"
      ]
    },
    {
      "id": "enum-ircmdtype-uuid",
      "type": "Enumeration",
      "name": "IRCommandType",
      "parentId": "pkg-fan-uuid",
      "literals": [
        "lit-ircmd-pwt",
        "lit-ircmd-sup",
        "lit-ircmd-sdn"
      ]
    },
    {
      "id": "sig-ircommand-uuid",
      "type": "Signal",
      "name": "IRCommand",
      "parentId": "pkg-fan-uuid",
      "properties": []
    },
    {
      "id": "if-statusdisp-uuid",
      "type": "InterfaceBlock",
      "name": "StatusDisplayInterface",
      "parentId": "pkg-fan-uuid",
      "isAbstract": True
    },
    {
      "id": "blk-acpower-uuid",
      "type": "Block",
      "name": "ACPowerBlock",
      "parentId": "pkg-fan-uuid",
      "isAbstract": False
    },
    {
      "id": "blk-motor-uuid",
      "type": "Block",
      "name": "Motor",
      "parentId": "pkg-fan-uuid",
      "isAbstract": False,
      "properties": [
        "prop-motor-rpm",
        "prop-motor-fan"
      ],
      "ports": [
        "port-motor-pwrin",
        "port-motor-ctrlin"
      ]
    },
    {
      "id": "blk-irrecv-uuid",
      "type": "Block",
      "name": "IRReceiver",
      "parentId": "pkg-fan-uuid",
      "isAbstract": False,
      "properties": [
        "prop-irrecv-fan"
      ],
      "ports": [
        "port-irrecv-cmdout"
      ]
    },
    {
      "id": "blk-fan-uuid",
      "type": "Block",
      "name": "Fan",
      "parentId": "pkg-fan-uuid",
      "isAbstract": False,
      "properties": [
        "prop-fan-motor",
        "prop-fan-recv",
        "prop-fan-speed",
        "prop-fan-remote"
      ],
      "ports": [
        "port-fan-powerin",
        "port-fan-statusdisp"
      ],
      "operations": [
        "op-fan-setspeed"
      ],
      "receptions": [
        "recp-fan-handlesig"
      ],
      "connectors": [
        "conn-fan-recv-motor",
        "conn-fan-pwr-motor",
        "conn-fan-bind-status"
      ],
      "ownedDiagrams": [
        "diag-fan-bdd",
        "diag-fan-ibd"
      ]
    },
    {
      "id": "blk-remote-uuid",
      "type": "Block",
      "name": "RemoteControl",
      "parentId": "pkg-fan-uuid",
      "isAbstract": False,
      "properties": [
        "prop-remote-battery",
        "prop-remote-fanlink"
      ],
      "operations": [
        "op-remote-sendcmd"
      ]
    },
    {
      "id": "prop-fan-motor",
      "type": "Property",
      "name": "motor",
      "parentId": "blk-fan-uuid",
      "visibility": "public",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-motor-uuid",
      "associationId": "assoc-fan-motor",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-fan-recv",
      "type": "Property",
      "name": "receiver",
      "parentId": "blk-fan-uuid",
      "visibility": "public",
      "propertyKind": "part",
      "aggregation": "composite",
      "typeId": "blk-irrecv-uuid",
      "associationId": "assoc-fan-recv",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-fan-speed",
      "type": "Property",
      "name": "currentSpeedLevel",
      "parentId": "blk-fan-uuid",
      "visibility": "public",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "enum-fanspeed-uuid",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-fan-remote",
      "type": "Property",
      "name": "_remote",
      "parentId": "blk-fan-uuid",
      "visibility": "private",
      "propertyKind": "reference",
      "typeId": "blk-remote-uuid",
      "associationId": "assoc-remote-fan",
      "aggregation": "none"
    },
    {
      "id": "prop-remote-battery",
      "type": "Property",
      "name": "batteryLevel",
      "parentId": "blk-remote-uuid",
      "visibility": "public",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "vt-percentage-uuid",
      "multiplicity": "[1..1]"
    },
    {
      "id": "prop-remote-fanlink",
      "type": "Property",
      "name": "pairedFan",
      "parentId": "blk-remote-uuid",
      "visibility": "public",
      "propertyKind": "reference",
      "aggregation": "none",
      "typeId": "blk-fan-uuid",
      "associationId": "assoc-remote-fan",
      "multiplicity": "[0..1]"
    },
    {
      "id": "prop-motor-rpm",
      "type": "Property",
      "name": "targetRPM",
      "parentId": "blk-motor-uuid",
      "visibility": "private",
      "propertyKind": "value",
      "aggregation": "none",
      "typeId": "Integer"
    },
    {
      "id": "prop-motor-fan",
      "type": "Property",
      "name": "_fan_motor",
      "parentId": "blk-motor-uuid",
      "visibility": "private",
      "propertyKind": "reference",
      "typeId": "blk-fan-uuid",
      "associationId": "assoc-fan-motor",
      "aggregation": "none"
    },
    {
      "id": "prop-irrecv-fan",
      "type": "Property",
      "name": "_fan_recv",
      "parentId": "blk-irrecv-uuid",
      "visibility": "private",
      "propertyKind": "reference",
      "typeId": "blk-fan-uuid",
      "associationId": "assoc-fan-recv",
      "aggregation": "none"
    },
    {
      "id": "port-fan-powerin",
      "type": "FullPort",
      "name": "powerIn",
      "parentId": "blk-fan-uuid",
      "visibility": "public",
      "typeId": "blk-acpower-uuid",
      "isBehavior": False
    },
    {
      "id": "port-fan-statusdisp",
      "type": "ProxyPort",
      "name": "statusDisplay",
      "parentId": "blk-fan-uuid",
      "visibility": "public",
      "typeId": "if-statusdisp-uuid",
      "isBehavior": True
    },
    {
      "id": "port-motor-pwrin",
      "type": "FullPort",
      "name": "motorPowerIn",
      "parentId": "blk-motor-uuid",
      "visibility": "public",
      "typeId": "blk-acpower-uuid",
      "isBehavior": False
    },
    {
      "id": "port-motor-ctrlin",
      "type": "ProxyPort",
      "name": "controlIn",
      "parentId": "blk-motor-uuid",
      "visibility": "public",
      "typeId": "sig-ircommand-uuid",
      "isBehavior": True
    },
    {
      "id": "port-irrecv-cmdout",
      "type": "ProxyPort",
      "name": "commandOut",
      "parentId": "blk-irrecv-uuid",
      "visibility": "public",
      "typeId": "sig-ircommand-uuid",
      "isBehavior": True
    },
    {
      "id": "op-fan-setspeed",
      "type": "Operation",
      "name": "setSpeedLevel",
      "parentId": "blk-fan-uuid",
      "visibility": "public",
      "parameters": [
        {
          "id": "p-fss-lvl",
          "name": "level",
          "typeId": "enum-fanspeed-uuid",
          "direction": "in"
        }
      ]
    },
    {
      "id": "op-remote-sendcmd",
      "type": "Operation",
      "name": "sendCommand",
      "parentId": "blk-remote-uuid",
      "visibility": "public",
      "parameters": [
        {
          "id": "p-rsc-cmd",
          "name": "command",
          "typeId": "enum-ircmdtype-uuid",
          "direction": "in"
        }
      ]
    },
    {
      "id": "recp-fan-handlesig",
      "type": "Reception",
      "name": "handleIRCommand",
      "parentId": "blk-fan-uuid",
      "visibility": "public",
      "signalId": "sig-ircommand-uuid"
    },
    {
      "id": "lit-fs-off",
      "type": "EnumerationLiteral",
      "name": "Off",
      "parentId": "enum-fanspeed-uuid"
    },
    {
      "id": "lit-fs-low",
      "type": "EnumerationLiteral",
      "name": "Low",
      "parentId": "enum-fanspeed-uuid"
    },
    {
      "id": "lit-fs-med",
      "type": "EnumerationLiteral",
      "name": "Medium",
      "parentId": "enum-fanspeed-uuid"
    },
    {
      "id": "lit-fs-high",
      "type": "EnumerationLiteral",
      "name": "High",
      "parentId": "enum-fanspeed-uuid"
    },
    {
      "id": "lit-ircmd-pwt",
      "type": "EnumerationLiteral",
      "name": "PowerToggle",
      "parentId": "enum-ircmdtype-uuid"
    },
    {
      "id": "lit-ircmd-sup",
      "type": "EnumerationLiteral",
      "name": "SpeedUp",
      "parentId": "enum-ircmdtype-uuid"
    },
    {
      "id": "lit-irccmd-sdn",
      "type": "EnumerationLiteral",
      "name": "SpeedDown",
      "parentId": "enum-ircmdtype-uuid"
    },
    {
      "id": "assoc-fan-motor",
      "type": "Association",
      "parentId": "pkg-fan-uuid",
      "memberEndIds": [
        "prop-fan-motor",
        "prop-motor-fan"
      ]
    },
    {
      "id": "assoc-fan-recv",
      "type": "Association",
      "parentId": "pkg-fan-uuid",
      "memberEndIds": [
        "prop-fan-recv",
        "prop-irrecv-fan"
      ]
    },
    {
      "id": "assoc-remote-fan",
      "type": "Association",
      "parentId": "pkg-fan-uuid",
      "memberEndIds": [
        "prop-remote-fanlink",
        "prop-fan-remote"
      ]
    },
    {
      "id": "conn-fan-recv-motor",
      "type": "AssemblyConnector",
      "parentId": "blk-fan-uuid",
      "kind": "assembly",
      "end1": {
        "id": "cEnd-frcm-1",
        "partRefId": "prop-fan-recv",
        "portRefId": "port-irrecv-cmdout"
      },
      "end2": {
        "id": "cEnd-frcm-2",
        "partRefId": "prop-fan-motor",
        "portRefId": "port-motor-ctrlin"
      }
    },
    {
      "id": "conn-fan-pwr-motor",
      "type": "AssemblyConnector",
      "parentId": "blk-fan-uuid",
      "kind": "assembly",
      "end1": {
        "id": "cEnd-fpwm-1",
        "partRefId": None,
        "portRefId": "port-fan-powerin"
      },
      "end2": {
        "id": "cEnd-fpwm-2",
        "partRefId": "prop-fan-motor",
        "portRefId": "port-motor-pwrin"
      }
    },
    {
      "id": "conn-fan-bind-status",
      "type": "BindingConnector",
      "parentId": "blk-fan-uuid",
      "kind": "binding",
      "end1": {
        "id": "cEnd-fbs-1",
        "partRefId": None,
        "propertyRefId": "prop-fan-speed"
      },
      "end2": {
        "id": "cEnd-fbs-2",
        "partRefId": None,
        "portRefId": "port-fan-statusdisp"
      }
    },
    {
      "id": "diag-fan-bdd",
      "type": "Diagram",
      "name": "风扇系统 BDD",
      "parentId": "pkg-fan-uuid",
      "diagramType": "BDD",
      "contextId": "pkg-fan-uuid"
    },
    {
      "id": "diag-fan-ibd",
      "type": "Diagram",
      "name": "风扇 IBD",
      "parentId": "blk-fan-uuid",
      "diagramType": "IBD",
      "contextId": "blk-fan-uuid"
    }
  ]
}


class FanModelImporter:
    def __init__(self, uri, user, password):
        self.driver = GraphDatabase.driver(uri, auth=(user, password))

    def close(self):
        self.driver.close()

    def import_model(self, json_path: str):
        data = load_json_stripping_comments(json_path)
        with self.driver.session() as session:
            # 创建 Model 节点 和 后续所有节点统一使用 _create_node
            session.execute_write(self._create_node, data['model'])
            for elem in data['elements']:
                session.execute_write(self._create_node, elem)
            # 建立关系
            for elem in data['elements']:
                session.execute_write(self._create_relationships, elem)

    @staticmethod
    def _create_node(tx, elem: dict):
        # 如果没有 type 字段，则为 Model
        if 'type' not in elem:
            tx.run(
                "MERGE (m:Model {id:$id}) SET m.name=$name",
                id=elem['id'], name=elem.get('name')
            )
            return
        # 普通元素节点
        labels = [elem['type']]
        if elem['type'] in ('FullPort', 'ProxyPort'):
            labels.append('Port')
        if elem['type'].endswith('Connector'):
            labels.append('Connector')
        label_str = ':'.join(labels)
        # 剔除嵌套字段
        skip = {'properties','ports','operations','receptions','connectors','ownedDiagrams','parameters','end1','end2'}
        props = {k:v for k,v in elem.items() if k not in skip}
        tx.run(
            f"MERGE (n:{label_str} {{id:$id}}) SET n += $props",
            id=elem['id'], props=props
        )

    @staticmethod
    def _create_relationships(tx, elem: dict):
        eid = elem['id']; typ = elem.get('type')
        # 父子
        if elem.get('parentId'):
            tx.run(
                "MATCH (p {id:$pid}), (c {id:$cid}) MERGE (p)-[:HAS_CHILD]->(c)",
                pid=elem['parentId'], cid=eid
            )
        # ValueType -> Unit/BaseType
        if typ=='ValueType':
            if elem.get('unitId'):
                tx.run(
                    "MATCH (vt:ValueType{id:$vid}), (u:Unit{id:$uid}) MERGE (vt)-[:HAS_UNIT]->(u)",
                    vid=eid, uid=elem['unitId']
                )
            bt = elem.get('baseType')
            if bt and bt not in ('Integer','Real','String'):
                tx.run(
                    "MATCH (vt:ValueType{id:$vid}), (bt {id:$btid}) MERGE (vt)-[:BASE_TYPE]->(bt)",
                    vid=eid, btid=bt
                )
        # Block 子元素
        if typ=='Block':
            for key, rel in (('properties','HAS_PROPERTY'),('ports','HAS_PORT'),
                              ('operations','HAS_OPERATION'),('receptions','HAS_RECEPTION'),
                              ('ownedDiagrams','HAS_DIAGRAM')):
                for cid in elem.get(key, []):
                    tx.run(
                        f"MATCH (b:Block{{id:$bid}}),(x{{id:$cid}}) MERGE (b)-[:{rel}]->(x)",
                        bid=eid, cid=cid
                    )
        # Property -> Type
        if typ=='Property' and elem.get('typeId'):
            tx.run(
                "MATCH (p:Property{id:$pid}),(t {id:$tid}) MERGE (p)-[:HAS_TYPE]->(t)",
                pid=eid, tid=elem['typeId']
            )
        # Port -> Type
        if typ in ('FullPort','ProxyPort') and elem.get('typeId'):
            tx.run(
                "MATCH (p:Port{id:$pid}),(t {id:$tid}) MERGE (p)-[:PORT_TYPE]->(t)",
                pid=eid, tid=elem['typeId']
            )
        # Operation -> Parameter -> ParamType
        if typ=='Operation':
            for param in elem.get('parameters', []):
                tx.run(
                    "MERGE (pr:Parameter{id:$pid}) SET pr.name=$pname, pr.direction=$dir",
                    pid=param['id'], pname=param['name'], dir=param['direction']
                )
                tx.run(
                    "MATCH (o:Operation{id:$oid}),(pr:Parameter{id:$pid}) MERGE (o)-[:HAS_PARAMETER]->(pr)",
                    oid=eid, pid=param['id']
                )
                if param.get('typeId'):
                    tx.run(
                        "MATCH (pr:Parameter{id:$pid}),(t {id:$tid}) MERGE (pr)-[:PARAM_TYPE]->(t)",
                        pid=param['id'], tid=param['typeId']
                    )
        # Reception -> Signal
        if typ=='Reception' and elem.get('signalId'):
            tx.run(
                "MATCH (r:Reception{id:$rid}),(s:Signal{id:$sid}) MERGE (r)-[:RECEPTION_SIGNAL]->(s)",
                rid=eid, sid=elem['signalId']
            )
        # Association -> Properties
        if typ=='Association':
            for mid in elem.get('memberEndIds', []):
                tx.run(
                    "MATCH (a:Association{id:$aid}),(p:Property{id:$pid}) MERGE (a)-[:ASSOC_MEMBER]->(p)",
                    aid=eid, pid=mid
                )
        # Connector -> 端点关系
        if typ and typ.endswith('Connector'):
            for end in ('end1','end2'):
                for ref in ('partRefId','propertyRefId','portRefId'):
                    rid = elem.get(end, {}).get(ref)
                    if rid:
                        tx.run(
                            "MATCH (c:Connector{id:$cid}), (x {id:$refid}) MERGE (c)-[:CONNECT_END {role:$role,ref_field:$ref}]->(x)",
                            cid=eid, refid=rid, role=end, ref=ref
                        )
        # Diagram -> Context
        if typ=='Diagram' and elem.get('contextId'):
            tx.run(
                "MATCH (d:Diagram{id:$did}),(x {id:$cid}) MERGE (d)-[:DIAGRAM_CONTEXT]->(x)",
                did=eid, cid=elem['contextId']
            )

if __name__ == '__main__':
    importer = FanModelImporter(URI, USER, PASSWORD)
    try:
        importer.import_model('fan_model.json')
        print('✅ 模型成功导入 Neo4j')
    finally:
        importer.close()