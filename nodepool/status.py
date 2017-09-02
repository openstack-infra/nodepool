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


def node_list(zk, node_id=None, detail=False):
    headers = [
        "ID",
        "Provider",
        "Label",
        "Server ID",
        "Public IPv4",
        "IPv6",
        "State",
        "Age",
        "Locked"
    ]
    detail_headers = [
        "Hostname",
        "Private IPv4",
        "AZ",
        "Port",
        "Launcher",
        "Allocated To",
        "Hold Job",
        "Comment"
    ]
    if detail:
        headers += detail_headers

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

    t = PrettyTable(headers)
    t.align = 'l'

    if node_id:
        node = zk.getNode(node_id)
        if node:
            values = _get_node_values(node)
            t.add_row(values)
    else:
        for node in zk.nodeIterator():
            values = _get_node_values(node)
            t.add_row(values)
    return str(t)


def node_list_json(zk):
    return json.dumps([node.toDict() for node in zk.nodeIterator()])


def label_list(zk):
    labels = set()
    for node in zk.nodeIterator():
        labels.add(node.type)
    t = PrettyTable(["Label"])
    for label in sorted(labels.keys()):
        t.add_row((label,))
    return str(t)


def label_list_json(zk):
    labels = set()
    for node in zk.nodeIterator():
        labels.add(node.type)
    return json.dumps(labels)


def dib_image_list(zk):
    t = PrettyTable(["ID", "Image", "Builder", "Formats",
                     "State", "Age"])
    t.align = 'l'
    for image_name in zk.getImageNames():
        for build_no in zk.getBuildNumbers(image_name):
            build = zk.getBuild(image_name, build_no)
            t.add_row(['-'.join([image_name, build_no]), image_name,
                       build.builder, ','.join(build.formats),
                       build.state, age(build.state_time)])
    return str(t)


def dib_image_list_json(zk):
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
    return json.dumps(objs)


def image_list(zk):
    t = PrettyTable(["Build ID", "Upload ID", "Provider", "Image",
                     "Provider Image Name", "Provider Image ID", "State",
                     "Age"])
    t.align = 'l'
    for image_name in zk.getImageNames():
        for build_no in zk.getBuildNumbers(image_name):
            for provider in zk.getBuildProviders(image_name, build_no):
                for upload_no in zk.getImageUploadNumbers(
                        image_name, build_no, provider):
                    upload = zk.getImageUpload(image_name, build_no,
                                               provider, upload_no)
                    t.add_row([build_no, upload_no, provider, image_name,
                               upload.external_name,
                               upload.external_id,
                               upload.state,
                               age(upload.state_time)])
    return str(t)


def request_list(zk):
    t = PrettyTable(["Request ID", "State", "Requestor", "Node Types", "Nodes",
                     "Declined By"])
    t.align = 'l'
    for req in zk.nodeRequestIterator():
        t.add_row([req.id, req.state, req.requestor,
                   ','.join(req.node_types),
                   ','.join(req.nodes),
                   ','.join(req.declined_by)])
    return str(t)
