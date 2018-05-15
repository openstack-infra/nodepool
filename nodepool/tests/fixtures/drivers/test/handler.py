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

    @property
    def alive_thread_count(self):
        return 1

    def imagesAvailable(self):
        return True

    def launchesComplete(self):
        return True

    def launch(self, node):
        node.state = zk.READY
        node.external_id = "test-%s" % self.request.id
        self.zk.storeNode(node)
