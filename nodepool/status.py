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

import time

from nodepool import nodedb

from prettytable import PrettyTable


def age(timestamp):
    now = time.time()
    dt = now - timestamp
    m, s = divmod(dt, 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    return '%02d:%02d:%02d:%02d' % (d, h, m, s)


def node_list(db, node_id=None):
    t = PrettyTable(["ID", "Provider", "AZ", "Label", "Target",
                     "Manager", "Hostname", "NodeName", "Server ID",
                     "IP", "State", "Age", "Comment"])
    t.align = 'l'
    with db.getSession() as session:
        for node in session.getNodes():
            if node_id and node.id != node_id:
                continue
            t.add_row([node.id, node.provider_name, node.az,
                       node.label_name, node.target_name,
                       node.manager_name, node.hostname,
                       node.nodename, node.external_id, node.ip,
                       nodedb.STATE_NAMES[node.state],
                       age(node.state_time), node.comment])
    return str(t)


def dib_image_list(zk):
    t = PrettyTable(["ID", "Image", "Builder", "Formats",
                     "State", "Age"])
    t.align = 'l'
    for image_name in zk.getImageNames():
        for build_no in zk.getBuildNumbers(image_name):
            build = zk.getBuild(image_name, build_no)
            t.add_row([build_no, image_name,
                       build['builder'], build['formats'],
                       build['state'], age(build['state_time'])])
    return str(t)


def image_list(zk):
    t = PrettyTable(["ID", "Provider", "Image", "Provider Image Name",
                     "Provider Image ID", "State", "Age"])
    t.align = 'l'
    for image_name in zk.getImageNames():
        for build_no in zk.getBuildNumbers(image_name):
            for provider in zk.getBuildProviders(image_name, build_no):
                for upload_no in zk.getImageUploadNumbers(
                        image_name, build_no, provider):
                    upload = zk.getImageUpload(image_name, build_no,
                                               provider, upload_no)
                    t.add_row([upload_no, provider, image_name,
                               upload['external_name'],
                               upload['external_id'],
                               upload['state'],
                               age(upload['state_time'])])
    return str(t)
