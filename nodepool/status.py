#!/usr/bin/env python
#
# Copyright 2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from collections import OrderedDict
import json
import time

from prettytable import PrettyTable


def age(timestamp):
    now = time.time()
    dt = now - timestamp
    m, s = divmod(dt, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return '%02d:%02d:%02d:%02d' % (d, h, m, s)


def _to_pretty_table(objs, headers_table):
    headers = headers_table.values()
    t = PrettyTable(headers)
    t.align = 'l'
    for obj in objs:
        values = []
        for k in headers_table:
            if k == 'age':
                try:
                    obj_age = age(int(obj[k]))
                except ValueError:
                    # already converted
                    obj_age = obj[k]
                values.append(obj_age)
            else:
                if isinstance(obj[k], list):
                    values.append(','.join(obj[k]))
                else:
                    values.append(obj[k])
        t.add_row(values)
    return t


def node_list(zk, node_id=None, detail=False, format='pretty'):
    headers_table = [
        ("id", "ID"),
        ("provider", "Provider"),
        ("label", "Label"),
        ("server_id", "Server ID"),
        ("public_ipv4", "Public IPv4"),
        ("ipv6", "IPv6"),
        ("state", "State"),
        ("age", "Age"),
        ("locked", "Locked")
    ]
    detail_headers_table = [
        ("hostname", "Hostname"),
        ("private_ipv4", "Private IPv4"),
        ("AZ", "AZ"),
        ("connection_port", "Port"),
        ("launcher", "Launcher"),
        ("allocated_to", "Allocated To"),
        ("hold_job", "Hold Job"),
        ("comment", "Comment")
    ]
    if detail:
        headers_table += detail_headers_table
    headers_table = OrderedDict(headers_table)

    def _get_node_values(node):
        locked = "unlocked"
        try:
            zk.lockNode(node, blocking=False)
        except Exception:
            locked = "locked"
        else:
            zk.unlockNode(node)

        values = [
            node.id,
            node.provider,
            node.type,
            node.external_id,
            node.public_ipv4,
            node.public_ipv6,
            node.state,
            age(node.state_time),
            locked
        ]
        if detail:
            values += [
                node.hostname,
                node.private_ipv4,
                node.az,
                node.connection_port,
                node.launcher,
                node.allocated_to,
                node.hold_job,
                node.comment
            ]
        return values

    objs = []
    if node_id:
        node = zk.getNode(node_id)
        if node:
            values = _get_node_values(node)
            objs.append(dict(zip(headers_table.keys(),
                                 values)))
    else:
        for node in zk.nodeIterator():
            values = _get_node_values(node)

            objs.append(dict(zip(headers_table.keys(),
                                 values)))

    if format == 'pretty':
        t = _to_pretty_table(objs, headers_table)
        return str(t)
    elif format == 'json':
        return json.dumps(objs)
    else:
        raise ValueError('Unknown format "%s"' % format)


def label_list(zk, format='pretty'):
    labels = set()
    for node in zk.nodeIterator():
        labels.add(node.type)
    if format == 'pretty':
        t = PrettyTable(["Label", ])
        t.align = 'l'
        for label in labels:
            t.add_row(label)
        return str(t)
    elif format == 'json':
        return json.dumps(list(labels))
    else:
        raise ValueError('Unknown format "%s"' % format)


def dib_image_list(zk, format='pretty'):
    headers_table = OrderedDict([
        ("id", "ID"),
        ("image", "Image"),
        ("builder", "Builder"),
        ("formats", "Formats"),
        ("state", "State"),
        ("age", "Age")])
    objs = []
    for image_name in zk.getImageNames():
        for build_no in zk.getBuildNumbers(image_name):
            build = zk.getBuild(image_name, build_no)
            objs.append({'id': '-'.join([image_name, build_no]),
                         'image': image_name,
                         'builder': build.builder,
                         'formats': build.formats,
                         'state': build.state,
                         'age': int(build.state_time)
                         })
    if format == 'pretty':
        t = _to_pretty_table(objs, headers_table)
        return str(t)
    elif format == 'json':
        return json.dumps(objs)
    else:
        raise ValueError('Unknown format "%s"' % format)


def image_list(zk, format='pretty'):
    headers_table = OrderedDict([
        ("id", "Build ID"),
        ("upload_id", "Upload ID"),
        ("provider", "Provider"),
        ("image", "Image"),
        ("external_name", "Provider Image Name"),
        ("external_id", "Provider Image ID"),
        ("state", "State"),
        ("age", "Age")])
    objs = []
    for image_name in zk.getImageNames():
        for build_no in zk.getBuildNumbers(image_name):
            for provider in zk.getBuildProviders(image_name, build_no):
                for upload_no in zk.getImageUploadNumbers(
                        image_name, build_no, provider):
                    upload = zk.getImageUpload(image_name, build_no,
                                               provider, upload_no)
                    values = [build_no, upload_no, provider, image_name,
                              upload.external_name,
                              upload.external_id,
                              upload.state,
                              int(upload.state_time)]
                    objs.append(dict(zip(headers_table.keys(),
                                         values)))
    if format == 'pretty':
        t = _to_pretty_table(objs, headers_table)
        return str(t)
    elif format == 'json':
        return json.dumps(objs)
    else:
        raise ValueError('Unknown format "%s"' % format)


def request_list(zk, format='pretty'):
    headers_table = OrderedDict([
        ("id", "Request ID"),
        ("state", "State"),
        ("requestor", "Requestor"),
        ("node_types", "Node Types"),
        ("nodes", "Nodes"),
        ("declined_by", "Declined By")])
    objs = []
    for req in zk.nodeRequestIterator():
        values = [req.id, req.state, req.requestor,
                  req.node_types,
                  req.nodes,
                  req.declined_by]
        objs.append(dict(zip(headers_table.keys(),
                             values)))
    if format == 'pretty':
        t = _to_pretty_table(objs, headers_table)
        return str(t)
    elif format == 'json':
        return json.dumps(objs)
    else:
        raise ValueError('Unknown format "%s"' % format)
