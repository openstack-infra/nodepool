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

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.nodeutils import keyscan


class StaticNodeError(Exception):
    pass


class StaticNodeProvider(Provider):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.pools = {}
        self.static_nodes = {}
        self.nodes_keys = {}

    def checkHost(self, node):
        # Check node is reachable
        try:
            keys = keyscan(node["name"],
                           port=node["ssh-port"],
                           timeout=node["timeout"])
        except exceptions.SSHTimeoutException:
            raise StaticNodeError("%s: SSHTimeoutException" % node["name"])

        # Check node host-key
        if set(node["host-key"]).issubset(set(keys)):
            return keys

        self.log.debug("%s: Registered key '%s' not in %s" % (
            node["name"], node["host-key"], keys
        ))
        raise StaticNodeError("%s: host key mismatches (%s)" %
                              (node["name"], keys))

    def start(self):
        for pool in self.provider.pools.values():
            self.pools[pool.name] = {}
            for node in pool.nodes:
                node_name = "%s-%s" % (pool.name, node["name"])
                self.log.debug("%s: Registering static node" % node_name)
                try:
                    self.nodes_keys[node["name"]] = self.checkHost(node)
                except StaticNodeError as e:
                    self.log.error("Couldn't register static node: %s" % e)
                    continue
                self.static_nodes[node_name] = node

    def stop(self):
        self.log.debug("Stopping")

    def listNodes(self):
        servers = []
        for node in self.static_nodes.values():
            servers.append(node)
        return servers

    def cleanupNode(self, server_id):
        return True

    def waitForNodeCleanup(self, server_id):
        return True

    def labelReady(self, name):
        return True

    def join(self):
        return True

    def cleanupLeakedResources(self):
        pass
