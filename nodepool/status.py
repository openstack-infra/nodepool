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


def dib_image_list(db):
    t = PrettyTable(["ID", "Image", "Filename", "Version",
                     "State", "Age"])
    t.align = 'l'
    with db.getSession() as session:
        for image in session.getDibImages():
            t.add_row([image.id, image.image_name,
                       image.filename, image.version,
                       nodedb.STATE_NAMES[image.state],
                       age(image.state_time)])
    return str(t)


def image_list(db):
    t = PrettyTable(["ID", "Provider", "Image", "Hostname", "Version",
                     "Image ID", "Server ID", "State", "Age"])
    t.align = 'l'
    with db.getSession() as session:
        for image in session.getSnapshotImages():
            t.add_row([image.id, image.provider_name, image.image_name,
                       image.hostname, image.version,
                       image.external_id, image.server_external_id,
                       nodedb.STATE_NAMES[image.state],
                       age(image.state_time)])
    return str(t)
