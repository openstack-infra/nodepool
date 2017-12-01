# Copyright 2017 Red Hat
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

import logging

from nodepool import zk
from nodepool.driver import NodeRequestHandler


class TestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.test.TestHandler")

    def run_handler(self):
        self._setFromPoolWorker()
        node = zk.Node()
        node.state = zk.READY
        node.external_id = "test-%s" % self.request.id
        node.provider = self.provider.name
        node.launcher = self.launcher_id
        node.allocated_to = self.request.id
        node.type = self.request.node_types[0]
        self.nodeset.append(node)
        self.zk.storeNode(node)
