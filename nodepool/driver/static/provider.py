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

from collections import Counter

from nodepool import exceptions
from nodepool import nodeutils
from nodepool import zk
from nodepool.driver import Provider
from nodepool.driver.static.handler import StaticNodeRequestHandler


class StaticNodeError(Exception):
    pass


class StaticNodeProvider(Provider):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeProvider")

    def __init__(self, provider, *args):
        self.provider = provider
        self.static_nodes = {}

    def checkHost(self, node):
        # Check node is reachable
        if node["connection-type"] != "ssh":
            return
        try:
            keys = nodeutils.nodescan(node["name"],
                                      port=node["connection-port"],
                                      timeout=node["timeout"])
        except exceptions.ConnectionTimeoutException:
            raise StaticNodeError(
                "%s:%s: ConnectionTimeoutException" % (
                    node["name"], node["connection-port"]))

        # Check node host-key
        if set(node["host-key"]).issubset(set(keys)):
            return keys

        self.log.debug("%s: Registered key '%s' not in %s" % (
            node["name"], node["host-key"], keys
        ))
        raise StaticNodeError("%s: host key mismatches (%s)" %
                              (node["name"], keys))

    def getRegisteredReadyNodes(self, hostname):
        '''
        Get all registered nodes with the given hostname that are READY.

        :param str hostname: Hostname of the node (maps to static node name).
        :returns: A list of matching Node objects.
        '''
        nodes = []
        for node in self.zk.nodeIterator():
            if (node.provider != self.provider.name or
                node.state != zk.READY or
                node.hostname != hostname
            ):
                continue
            nodes.append(node)
        return nodes

    def getRegisteredNodeHostnames(self):
        '''
        Get hostnames for all registered static nodes.

        :note: We assume hostnames are unique across pools.

        :returns: A set of registered hostnames for the static driver.
        '''
        registered = Counter()
        for node in self.zk.nodeIterator():
            if node.provider != self.provider.name:
                continue
            registered.update([node.hostname])
        return registered

    def registerNodeFromConfig(self, count, provider_name, pool_name,
                               static_node):
        '''
        Register a static node from the config with ZooKeeper.

        A node can be registered multiple times to support max-parallel-jobs.
        These nodes will share a hostname.

        :param int count: Number of times to register this node.
        :param str provider_name: Name of the provider.
        :param str pool_name: Name of the pool owning the node.
        :param dict static_node: The node definition from the config file.
        '''
        host_keys = self.checkHost(static_node)

        for i in range(0, count):
            node = zk.Node()
            node.state = zk.READY
            node.provider = provider_name
            node.pool = pool_name
            node.launcher = "static driver"
            node.type = static_node["labels"]
            node.hostname = static_node["name"]
            node.username = static_node["username"]
            node.interface_ip = static_node["name"]
            node.connection_port = static_node["connection-port"]
            node.connection_type = static_node["connection-type"]
            nodeutils.set_node_ip(node)
            node.host_keys = host_keys
            self.zk.storeNode(node)
            self.log.debug("Registered static node %s", node.hostname)

    def deregisterNode(self, count, node_name):
        '''
        Attempt to delete READY nodes.

        We can only delete unlocked READY nodes. If we cannot delete those,
        let them remain until they naturally are deleted (we won't re-register
        them after they are deleted).

        :param str node_name: The static node name/hostname.
        '''
        self.log.debug("Deregistering %s nodes with hostname %s",
                       count, node_name)

        nodes = self.getRegisteredReadyNodes(node_name)

        for node in nodes:
            if count <= 0:
                break

            try:
                self.zk.lockNode(node, blocking=False)
            except exceptions.ZKLockException:
                # It's already locked so skip it.
                continue

            if node.state != zk.READY:
                # State changed so skip it.
                self.zk.unlockNode(node)
                continue

            node.state = zk.DELETING
            try:
                self.zk.storeNode(node)
                self.log.debug("Deregistered static node: id=%s, hostname=%s",
                               node.id, node.hostname)
                count = count - 1
            except Exception:
                self.log.exception("Error deregistering static node:")
            finally:
                self.zk.unlockNode(node)

    def _start(self, zk_conn):
        self.zk = zk_conn
        registered = self.getRegisteredNodeHostnames()

        for pool in self.provider.pools.values():
            for node in pool.nodes:
                current_count = registered[node["name"]]

                # Register nodes to synchronize with our configuration.
                if current_count < node["max-parallel-jobs"]:
                    register_cnt = node["max-parallel-jobs"] - current_count
                    try:
                        self.registerNodeFromConfig(
                            register_cnt, self.provider.name, pool.name, node)
                    except Exception:
                        self.log.exception("Couldn't register static node:")
                        continue

                # De-register nodes to synchronize with our configuration.
                # This case covers an existing node, but with a decreased
                # max-parallel-jobs value.
                elif current_count > node["max-parallel-jobs"]:
                    deregister_cnt = current_count - node["max-parallel-jobs"]
                    try:
                        self.deregisterNode(deregister_cnt, node["name"])
                    except Exception:
                        self.log.exception("Couldn't deregister static node:")
                        continue

                self.static_nodes[node["name"]] = node

        # De-register nodes to synchronize with our configuration.
        # This case covers any registered nodes that no longer appear in
        # the config.
        for hostname in list(registered):
            if hostname not in self.static_nodes:
                try:
                    self.deregisterNode(registered[hostname], hostname)
                except Exception:
                    self.log.exception("Couldn't deregister static node:")
                    continue

    def start(self, zk_conn):
        try:
            self._start(zk_conn)
        except Exception:
            self.log.exception("Cannot start static provider:")

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

    def getRequestHandler(self, poolworker, request):
        return StaticNodeRequestHandler(poolworker, request)

    def nodeDeletedNotification(self, node):
        '''
        Re-register the deleted node.
        '''
        # It's possible a deleted node no longer exists in our config, so
        # don't bother to reregister.
        if node.hostname not in self.static_nodes:
            return

        static_node = self.static_nodes[node.hostname]
        try:
            registered = self.getRegisteredNodeHostnames()
        except Exception:
            self.log.exception(
                "Cannot get registered hostnames for node re-registration:")
            return
        current_count = registered[node.hostname]

        # It's possible we were not able to de-register nodes due to a config
        # change (because they were in use). In that case, don't bother to
        # reregister.
        if current_count >= static_node["max-parallel-jobs"]:
            return

        try:
            self.registerNodeFromConfig(
                1, node.provider, node.pool, static_node)
        except Exception:
            self.log.exception("Cannot re-register deleted node %s", node)
