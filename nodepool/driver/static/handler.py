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
import random

from nodepool import exceptions
from nodepool import nodeutils
from nodepool import zk
from nodepool.driver import NodeRequestHandler


class StaticNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeRequestHandler")

    def _invalidNodeTypes(self):
        '''
        Return any node types that are invalid for this provider.

        :returns: A list of node type names that are invalid, or an empty
            list if all are valid.
        '''
        invalid = []
        for ntype in self.request.node_types:
            if ntype not in self.pool.labels:
                invalid.append(ntype)
        return invalid

    def checkConcurrency(self, static_node):
        access_count = 0
        for node in self.zk.nodeIterator():
            if node.hostname != static_node["name"]:
                continue
            if node.state in ('ready', 'in-use'):
                access_count += 1
        if access_count >= static_node["max-parallel-jobs"]:
            self.log.info("%s: max concurrency reached (%d)" % (
                static_node["name"], access_count))
            return False
        return True

    def _waitForNodeSet(self):
        '''
        Fill node set for the request.

        '''
        needed_types = self.request.node_types
        static_nodes = []
        unavailable_nodes = []
        ready_nodes = self.zk.getReadyNodesOfTypes(needed_types)

        for ntype in needed_types:
            # First try to grab from the list of already available nodes.
            got_a_node = False
            if self.request.reuse and ntype in ready_nodes:
                for node in ready_nodes[ntype]:
                    # Only interested in nodes from this provider and
                    # pool
                    if node.provider != self.provider.name:
                        continue
                    if node.pool != self.pool.name:
                        continue

                    try:
                        self.zk.lockNode(node, blocking=False)
                    except exceptions.ZKLockException:
                        # It's already locked so skip it.
                        continue
                    else:
                        if self.paused:
                            self.log.debug("Unpaused request %s", self.request)
                            self.paused = False

                        self.log.debug(
                            "Locked existing node %s for request %s",
                            node.id, self.request.id)
                        got_a_node = True
                        node.allocated_to = self.request.id
                        self.zk.storeNode(node)
                        self.nodeset.append(node)
                        break
            # Could not grab an existing node, so assign a new one.
            if not got_a_node:
                for node in self.available_nodes:
                    if ntype in node["labels"]:
                        max_concurrency = not self.checkConcurrency(node)
                        if max_concurrency:
                            continue
                        static_nodes.append((ntype, node))
                        break
                if max_concurrency:
                    unavailable_nodes.append(ntype)

        if unavailable_nodes:
            self.log.debug("%s: static nodes %s are at capacity" % (
                self.request.id, unavailable_nodes))
            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
            self.done = True
            return

        for node_type, static_node in static_nodes:
            self.log.debug("%s: Assigning static_node %s" % (
                self.request.id, static_node))
            node = zk.Node()
            node.state = zk.READY
            node.external_id = "static-%s" % self.request.id
            node.hostname = static_node["name"]
            node.username = static_node["username"]
            node.interface_ip = static_node["name"]
            node.connection_port = static_node["connection-port"]
            node.connection_type = static_node["connection-type"]
            nodeutils.set_node_ip(node)
            node.host_keys = self.manager.nodes_keys[static_node["name"]]
            node.provider = self.provider.name
            node.pool = self.pool.name
            node.launcher = self.launcher_id
            node.allocated_to = self.request.id
            node.type = node_type
            self.nodeset.append(node)
            self.zk.storeNode(node)

    def run_handler(self):
        '''
        Main body for the StaticNodeRequestHandler.
        '''
        self._setFromPoolWorker()

        # We have the launcher_id attr after _setFromPoolWorker() is called.
        self.log = logging.getLogger(
            "nodepool.driver.static.StaticNodeRequestHandler[%s]" %
            self.launcher_id)

        declined_reasons = []
        invalid_types = self._invalidNodeTypes()
        if invalid_types:
            declined_reasons.append('node type(s) [%s] not available' %
                                    ','.join(invalid_types))

        self.available_nodes = self.manager.listNodes()
        # Randomize static nodes order
        random.shuffle(self.available_nodes)

        if len(self.request.node_types) > len(self.available_nodes):
            declined_reasons.append('it would exceed quota')

        if declined_reasons:
            self.log.debug("Declining node request %s because %s",
                           self.request.id, ', '.join(declined_reasons))
            self.decline_request()
            self.unlockNodeSet(clear_allocation=True)

            # If conditions have changed for a paused request to now cause us
            # to decline it, we need to unpause so we don't keep trying it
            if self.paused:
                self.paused = False

            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
            self.done = True
            return

        if self.paused:
            self.log.debug("Retrying node request %s", self.request.id)
        else:
            self.log.debug("Accepting node request %s", self.request.id)
            self.request.state = zk.PENDING
            self.zk.storeNodeRequest(self.request)

        self._waitForNodeSet()
