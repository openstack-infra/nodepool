#!/usr/bin/env python

# Copyright (C) 2011-2014 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
#
# See the License for the specific language governing permissions and
# limitations under the License.

import collections
import logging
import os
import os.path
import pprint
import random
import socket
import threading
import time

import exceptions
import nodeutils as utils
import provider_manager
import stats
import config as nodepool_config

import zk

MINS = 60
HOURS = 60 * MINS

WATERMARK_SLEEP = 10         # Interval between checking if new servers needed
IMAGE_TIMEOUT = 6 * HOURS    # How long to wait for an image save
CONNECT_TIMEOUT = 10 * MINS  # How long to try to connect after a server
                             # is ACTIVE
LOCK_CLEANUP = 8 * HOURS     # When to delete node request lock znodes
NODE_CLEANUP = 8 * HOURS     # When to start deleting a node that is not
                             # READY or HOLD
TEST_CLEANUP = 5 * MINS      # When to start deleting a node that is in TEST
IMAGE_CLEANUP = 8 * HOURS    # When to start deleting an image that is not
                             # READY or is not the current or previous image
DELETE_DELAY = 1 * MINS      # Delay before deleting a node that has completed
                             # its job.
SUSPEND_WAIT_TIME = 30       # How long to wait between checks for ZooKeeper
                             # connectivity if it disappears.


class LaunchNodepoolException(Exception):
    statsd_key = 'error.nodepool'


class LaunchStatusException(Exception):
    statsd_key = 'error.status'


class LaunchNetworkException(Exception):
    statsd_key = 'error.network'


class LaunchKeyscanException(Exception):
    statsd_key = 'error.keyscan'


class StatsReporter(object):
    '''
    Class adding statsd reporting functionality.
    '''
    def __init__(self):
        super(StatsReporter, self).__init__()
        self._statsd = stats.get_client()

    def recordLaunchStats(self, subkey, dt, image_name,
                          provider_name, node_az, requestor):
        '''
        Record node launch statistics.

        :param str subkey: statsd key
        :param int dt: Time delta in milliseconds
        :param str image_name: Name of the image used
        :param str provider_name: Name of the provider
        :param str node_az: AZ of the launched node
        :param str requestor: Identifier for the request originator
        '''
        if not self._statsd:
            return

        keys = [
            'nodepool.launch.provider.%s.%s' % (provider_name, subkey),
            'nodepool.launch.image.%s.%s' % (image_name, subkey),
            'nodepool.launch.%s' % (subkey,),
            ]

        if node_az:
            keys.append('nodepool.launch.provider.%s.%s.%s' %
                        (provider_name, node_az, subkey))

        if requestor:
            # Replace '.' which is a graphite hierarchy, and ':' which is
            # a statsd delimeter.
            requestor = requestor.replace('.', '_')
            requestor = requestor.replace(':', '_')
            keys.append('nodepool.launch.requestor.%s.%s' %
                        (requestor, subkey))

        for key in keys:
            self._statsd.timing(key, dt)
            self._statsd.incr(key)


    def updateNodeStats(self, zk_conn, provider):
        '''
        Refresh statistics for all known nodes.

        :param ZooKeeper zk_conn: A ZooKeeper connection object.
        :param Provider provider: A config Provider object.
        '''
        if not self._statsd:
            return

        states = {}

        # Initialize things we know about to zero
        for state in zk.Node.VALID_STATES:
            key = 'nodepool.nodes.%s' % state
            states[key] = 0
            key = 'nodepool.provider.%s.nodes.%s' % (provider.name, state)
            states[key] = 0

        for node in zk_conn.nodeIterator():
            #nodepool.nodes.STATE
            key = 'nodepool.nodes.%s' % node.state
            states[key] += 1

            #nodepool.label.LABEL.nodes.STATE
            key = 'nodepool.label.%s.nodes.%s' % (node.type, node.state)
            # It's possible we could see node types that aren't in our config
            if key in states:
                states[key] += 1
            else:
                states[key] = 1

            #nodepool.provider.PROVIDER.nodes.STATE
            key = 'nodepool.provider.%s.nodes.%s' % (node.provider, node.state)
            # It's possible we could see providers that aren't in our config
            if key in states:
                states[key] += 1
            else:
                states[key] = 1

        for key, count in states.items():
            self._statsd.gauge(key, count)

        #nodepool.provider.PROVIDER.max_servers
        key = 'nodepool.provider.%s.max_servers' % provider.name
        max_servers = sum([p.max_servers for p in provider.pools.values()])
        self._statsd.gauge(key, max_servers)


class InstanceDeleter(threading.Thread, StatsReporter):
    log = logging.getLogger("nodepool.InstanceDeleter")

    def __init__(self, zk, manager, node):
        threading.Thread.__init__(self, name='InstanceDeleter for %s %s' %
                                  (node.provider, node.external_id))
        StatsReporter.__init__(self)
        self._zk = zk
        self._manager = manager
        self._node = node

    @staticmethod
    def delete(zk_conn, manager, node, node_exists=True):
        '''
        Delete a server instance and ZooKeeper node.

        This is a class method so we can support instantaneous deletes.

        :param ZooKeeper zk_conn: A ZooKeeper object to use.
        :param ProviderManager manager: ProviderManager object to use for
            deleting the server.
        :param Node node: A locked Node object that describes the server to
            delete.
        :param bool node_exists: True if the node actually exists in ZooKeeper.
            An artifical Node object can be passed that can be used to delete
            a leaked instance.
        '''
        try:
            node.state = zk.DELETING
            zk_conn.storeNode(node)
            if node.external_id:
                manager.cleanupServer(node.external_id)
                manager.waitForServerDeletion(node.external_id)
        except provider_manager.NotFound:
            InstanceDeleter.log.info("Instance %s not found in provider %s",
                                     node.external_id, node.provider)
        except Exception:
            InstanceDeleter.log.exception(
                "Exception deleting instance %s from %s:",
                node.external_id, node.provider)
            # Don't delete the ZK node in this case, but do unlock it
            if node_exists:
                zk_conn.unlockNode(node)
            return

        if node_exists:
            InstanceDeleter.log.info(
                "Deleting ZK node id=%s, state=%s, external_id=%s",
                node.id, node.state, node.external_id)
            # This also effectively releases the lock
            zk_conn.deleteNode(node)

    def run(self):
        # Since leaked instances won't have an actual node in ZooKeeper,
        # we need to check 'id' to see if this is an artificial Node.
        if self._node.id is None:
            node_exists = False
        else:
            node_exists = True

        self.delete(self._zk, self._manager, self._node, node_exists)

        try:
            self.updateNodeStats(self._zk, self._manager.provider)
        except Exception:
            self.log.exception("Exception while reporting stats:")


class NodeLauncher(threading.Thread, StatsReporter):

    def __init__(self, zk, provider_label, provider_manager, requestor,
                 node, retries):
        '''
        Initialize the launcher.

        :param ZooKeeper zk: A ZooKeeper object.
        :param ProviderLabel provider: A config ProviderLabel object.
        :param ProviderManager provider_manager: The manager object used to
            interact with the selected provider.
        :param str requestor: Identifier for the request originator.
        :param Node node: The node object.
        :param int retries: Number of times to retry failed launches.
        '''
        threading.Thread.__init__(self, name="NodeLauncher-%s" % node.id)
        StatsReporter.__init__(self)
        self.log = logging.getLogger("nodepool.NodeLauncher-%s" % node.id)
        self._zk = zk
        self._label = provider_label
        self._manager = provider_manager
        self._node = node
        self._retries = retries
        self._image_name = None
        self._requestor = requestor

        self._pool = self._label.pool
        self._provider = self._pool.provider
        self._diskimage = self._provider.diskimages[self._label.diskimage.name]

    def _launchNode(self):
        cloud_image = self._zk.getMostRecentImageUpload(
            self._diskimage.name, self._provider.name)
        if not cloud_image:
            raise LaunchNodepoolException(
                "Unable to find current cloud image %s in %s" %
                (self._diskimage.name, self._provider.name)
            )

        hostname = self._provider.hostname_format.format(
            label=self._label, provider=self._provider, node=self._node
        )

        self.log.info("Creating server with hostname %s in %s from image %s "
                      "for node id: %s" % (hostname, self._provider.name,
                                           self._diskimage.name,
                                           self._node.id))

        # NOTE: We store the node ID in the server metadata to use for leaked
        # instance detection. We cannot use the external server ID for this
        # because that isn't available in ZooKeeper until after the server is
        # active, which could cause a race in leak detection.

        server = self._manager.createServer(
            hostname,
            self._label.min_ram,
            cloud_image.external_id,
            name_filter=self._label.name_filter,
            az=self._node.az,
            config_drive=self._diskimage.config_drive,
            nodepool_node_id=self._node.id,
            nodepool_image_name=self._diskimage.name,
            networks=self._pool.networks)

        self._node.external_id = server.id
        self._node.hostname = hostname
        self._node.image_id = "{path}/{upload_id}".format(
            path=self._zk._imageUploadPath(cloud_image.image_name,
                                           cloud_image.build_id,
                                           cloud_image.provider_name),
            upload_id=cloud_image.id)

        # Checkpoint save the updated node info
        self._zk.storeNode(self._node)

        self.log.debug("Waiting for server %s for node id: %s" %
                       (server.id, self._node.id))
        server = self._manager.waitForServer(
            server, self._provider.launch_timeout)

        if server.status != 'ACTIVE':
            raise LaunchStatusException("Server %s for node id: %s "
                                        "status: %s" %
                                        (server.id, self._node.id,
                                         server.status))

        # If we didn't specify an AZ, set it to the one chosen by Nova.
        # Do this after we are done waiting since AZ may not be available
        # immediately after the create request.
        if not self._node.az:
            self._node.az = server.location.zone

        interface_ip = server.interface_ip
        if not interface_ip:
            self.log.debug(
                "Server data for failed IP: %s" % pprint.pformat(
                    server))
            raise LaunchNetworkException("Unable to find public IP of server")

        self._node.interface_ip = interface_ip
        self._node.public_ipv4 = server.public_v4
        self._node.public_ipv6 = server.public_v6
        self._node.private_ipv4 = server.private_v4
        # devstack-gate multi-node depends on private_v4 being populated
        # with something. On clouds that don't have a private address, use
        # the public.
        if not self._node.private_ipv4:
            self._node.private_ipv4 = server.public_v4

        # Checkpoint save the updated node info
        self._zk.storeNode(self._node)

        self.log.debug(
            "Node %s is running [az: %s, ip: %s ipv4: %s, ipv6: %s]" %
            (self._node.id, self._node.az, self._node.interface_ip,
             self._node.public_ipv4, self._node.public_ipv6))

        # Get the SSH public keys for the new node and record in ZooKeeper
        self.log.debug("Gathering host keys for node %s", self._node.id)
        host_keys = utils.keyscan(
            interface_ip, timeout=self._provider.boot_timeout)
        if not host_keys:
            raise LaunchKeyscanException("Unable to gather host keys")
        self._node.host_keys = host_keys
        self._zk.storeNode(self._node)

    def _run(self):
        attempts = 1
        while attempts <= self._retries:
            try:
                self._launchNode()
                break
            except Exception:
                if attempts <= self._retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self._retries, self._node.id)
                # If we created an instance, delete it.
                if self._node.external_id:
                    self._manager.cleanupServer(self._node.external_id)
                    self._manager.waitForServerDeletion(self._node.external_id)
                    self._node.external_id = None
                    self._node.public_ipv4 = None
                    self._node.public_ipv6 = None
                    self._node.inerface_ip = None
                    self._zk.storeNode(self._node)
                if attempts == self._retries:
                    raise
                attempts += 1

        self._node.state = zk.READY
        self._zk.storeNode(self._node)
        self.log.info("Node id %s is ready", self._node.id)

    def run(self):
        start_time = time.time()
        statsd_key = 'ready'

        try:
            self._run()
        except Exception as e:
            self.log.exception("Launch failed for node %s:",
                               self._node.id)
            self._node.state = zk.FAILED
            self._zk.storeNode(self._node)

            if hasattr(e, 'statsd_key'):
                statsd_key = e.statsd_key
            else:
                statsd_key = 'error.unknown'

        try:
            dt = int((time.time() - start_time) * 1000)
            self.recordLaunchStats(statsd_key, dt, self._image_name,
                                   self._node.provider, self._node.az,
                                   self._requestor)
            self.updateNodeStats(self._zk, self._provider)
        except Exception:
            self.log.exception("Exception while reporting stats:")


class NodeLaunchManager(object):
    '''
    Handle launching multiple nodes in parallel.
    '''
    def __init__(self, zk, pool, provider_manager,
                 requestor, retries):
        '''
        Initialize the launch manager.

        :param ZooKeeper zk: A ZooKeeper object.
        :param ProviderPool pool: A config ProviderPool object.
        :param ProviderManager provider_manager: The manager object used to
            interact with the selected provider.
        :param str requestor: Identifier for the request originator.
        :param int retries: Number of times to retry failed launches.
        '''
        self._retries = retries
        self._nodes = []
        self._failed_nodes = []
        self._ready_nodes = []
        self._threads = []
        self._zk = zk
        self._pool = pool
        self._manager = provider_manager
        self._requestor = requestor

    @property
    def alive_thread_count(self):
        count = 0
        for t in self._threads:
            if t.isAlive():
                count += 1
        return count

    @property
    def failed_nodes(self):
        return self._failed_nodes

    @property
    def ready_nodes(self):
        return self._ready_nodes

    def launch(self, node):
        '''
        Launch a new node as described by the supplied Node.

        We expect each NodeLauncher thread to directly modify the node that
        is passed to it. The poll() method will expect to see the node.state
        attribute to change as the node is processed.

        :param Node node: The node object.
        '''
        self._nodes.append(node)
        provider_label = self._pool.labels[node.type]
        t = NodeLauncher(self._zk, provider_label, self._manager,
                         self._requestor, node, self._retries)
        t.start()
        self._threads.append(t)

    def poll(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY or
        FAILED), we'll know all threads have finished the launch process.
        '''
        if not self._threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self.alive_thread_count:
            return False

        node_states = [node.state for node in self._nodes]

        # NOTE: It very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED) for s in node_states):
            return False

        for node in self._nodes:
            if node.state == zk.READY:
                self._ready_nodes.append(node)
            else:
                self._failed_nodes.append(node)

        return True


class NodeRequestHandler(object):
    '''
    Class to process a single node request.

    The PoolWorker thread will instantiate a class of this type for each
    node request that it pulls from ZooKeeper.
    '''

    def __init__(self, pw, request):
        '''
        :param PoolWorker pw: The parent PoolWorker object.
        :param NodeRequest request: The request to handle.
        '''
        self.log = logging.getLogger("nodepool.NodeRequestHandler")
        self.pw = pw
        self.request = request
        self.launch_manager = None
        self.nodeset = []
        self.done = False
        self.chosen_az = None
        self.paused = False

    def _setFromPoolWorker(self):
        '''
        Set values that we pull from the parent PoolWorker.

        We don't do this in __init__ because this class is re-entrant and we
        want the updated values.
        '''
        self.provider = self.pw.getProviderConfig()
        self.pool = self.pw.getPoolConfig()
        self.zk = self.pw.getZK()
        self.manager = self.pw.getProviderManager()
        self.launcher_id = self.pw.launcher_id

    def _imagesAvailable(self):
        '''
        Determines if the requested images are available for this provider.

        ZooKeeper is queried for an image uploaded to the provider that is
        in the READY state.

        :returns: True if it is available, False otherwise.
        '''
        for label in self.request.node_types:
            img = self.pool.labels[label].diskimage.name

            if not self.zk.getMostRecentImageUpload(img, self.provider.name):
                return False
        return True

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

    def _countNodes(self):
        '''
        Query ZooKeeper to determine the number of provider nodes launched.

        :returns: An integer for the number launched for this provider.
        '''
        count = 0
        for node in self.zk.nodeIterator():
            if (node.provider == self.provider.name and
                node.pool == self.pool.name):
                count += 1
        return count

    def _waitForNodeSet(self):
        '''
        Fill node set for the request.

        Obtain nodes for the request, pausing all new request handling for
        this provider until the node set can be filled.

        We attempt to group the node set within the same provider availability
        zone. For this to work properly, the provider entry in the nodepool
        config must list the availability zones. Otherwise, new nodes will be
        put in random AZs at nova's whim. The exception being if there is an
        existing node in the READY state that we can select for this node set.
        Its AZ will then be used for new nodes, as well as any other READY
        nodes.

        note:: This code is a bit racey in its calculation of the number of
            nodes in use for quota purposes. It is possible for multiple
            launchers to be doing this calculation at the same time. Since we
            currently have no locking mechanism around the "in use"
            calculation, if we are at the edge of the quota, one of the
            launchers could attempt to launch a new node after the other
            launcher has already started doing so. This would cause an
            expected failure from the underlying library, which is ok for now.
        '''
        if not self.launch_manager:
            self.launch_manager = NodeLaunchManager(
                self.zk, self.pool, self.manager,
                self.request.requestor, retries=self.provider.launch_retries)

        # Since this code can be called more than once for the same request,
        # we need to calculate the difference between our current node set
        # and what was requested. We cannot use set operations here since a
        # node type can appear more than once in the requested types.
        saved_types = collections.Counter([n.type for n in self.nodeset])
        requested_types = collections.Counter(self.request.node_types)
        diff = requested_types - saved_types
        needed_types = list(diff.elements())

        ready_nodes = self.zk.getReadyNodesOfTypes(needed_types)

        for ntype in needed_types:
            # First try to grab from the list of already available nodes.
            got_a_node = False
            if self.request.reuse and ntype in ready_nodes:
                for node in ready_nodes[ntype]:
                    # Only interested in nodes from this provider and
                    # pool, and within the selected AZ.
                    if node.provider != self.provider.name:
                        continue
                    if node.pool != self.pool.name:
                        continue
                    if self.chosen_az and node.az != self.chosen_az:
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

                        # If we haven't already chosen an AZ, select the
                        # AZ from this ready node. This will cause new nodes
                        # to share this AZ, as well.
                        if not self.chosen_az and node.az:
                            self.chosen_az = node.az
                        break

            # Could not grab an existing node, so launch a new one.
            if not got_a_node:
                # Select grouping AZ if we didn't set AZ from a selected,
                # pre-existing node
                if not self.chosen_az and self.pool.azs:
                    self.chosen_az = random.choice(self.pool.azs)

                # If we calculate that we're at capacity, pause until nodes
                # are released by Zuul and removed by the DeletedNodeWorker.
                if self._countNodes() >= self.pool.max_servers:
                    if not self.paused:
                        self.log.debug(
                            "Pausing request handling to satisfy request %s",
                            self.request)
                    self.paused = True
                    return

                if self.paused:
                    self.log.debug("Unpaused request %s", self.request)
                    self.paused = False

                node = zk.Node()
                node.state = zk.INIT
                node.type = ntype
                node.provider = self.provider.name
                node.pool = self.pool.name
                node.az = self.chosen_az
                node.launcher = self.launcher_id
                node.allocated_to = self.request.id

                # Note: It should be safe (i.e., no race) to lock the node
                # *after* it is stored since nodes in INIT state are not
                # locked anywhere.
                self.zk.storeNode(node)
                self.zk.lockNode(node, blocking=False)
                self.log.debug("Locked building node %s for request %s",
                               node.id, self.request.id)

                # Set state AFTER lock so sthat it isn't accidentally cleaned
                # up (unlocked BUILDING nodes will be deleted).
                node.state = zk.BUILDING
                self.zk.storeNode(node)

                self.nodeset.append(node)
                self.launch_manager.launch(node)

    def _run(self):
        '''
        Main body for the NodeRequestHandler.
        '''
        self._setFromPoolWorker()

        declined_reasons = []
        invalid_types = self._invalidNodeTypes()
        if invalid_types:
            declined_reasons.append('node type(s) [%s] not available' %
                                    ','.join(invalid_types))
        elif not self._imagesAvailable():
            declined_reasons.append('images are not available')
        if len(self.request.node_types) > self.pool.max_servers:
            declined_reasons.append('it would exceed quota')

        if declined_reasons:
            self.log.debug("Declining node request %s because %s",
                           self.request.id, ', '.join(declined_reasons))
            self.request.declined_by.append(self.launcher_id)
            launchers = set(self.zk.getRegisteredLaunchers())
            if launchers.issubset(set(self.request.declined_by)):
                self.log.debug("Failing declined node request %s",
                               self.request.id)
                # All launchers have declined it
                self.request.state = zk.FAILED
            self.unlockNodeSet(clear_allocation=True)
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

    @property
    def alive_thread_count(self):
        if not self.launch_manager:
            return 0
        return self.launch_manager.alive_thread_count

    #----------------------------------------------------------------
    # Public methods
    #----------------------------------------------------------------

    def unlockNodeSet(self, clear_allocation=False):
        '''
        Attempt unlocking all Nodes in the node set.

        :param bool clear_allocation: If true, clears the node allocated_to
            attribute.
        '''
        for node in self.nodeset:
            if not node.lock:
                continue

            if clear_allocation:
                node.allocated_to = None
                self.zk.storeNode(node)

            try:
                self.zk.unlockNode(node)
            except Exception:
                self.log.exception("Error unlocking node:")
            self.log.debug("Unlocked node %s for request %s",
                           node.id, self.request.id)

        self.nodeset = []

    def run(self):
        '''
        Execute node request handling.

        This code is designed to be re-entrant. Because we can't always
        satisfy a request immediately (due to lack of provider resources), we
        need to be able to call run() repeatedly until the request can be
        fulfilled. The node set is saved and added to between calls.
        '''
        try:
            self._run()
        except Exception:
            self.log.exception("Exception in NodeRequestHandler:")
            self.unlockNodeSet(clear_allocation=True)
            self.request.state = zk.FAILED
            self.zk.storeNodeRequest(self.request)
            self.zk.unlockNodeRequest(self.request)
            self.done = True

    def poll(self):
        '''
        Check if the request has been handled.

        Once the request has been handled, the 'nodeset' attribute will be
        filled with the list of nodes assigned to the request, or it will be
        empty if the request could not be fulfilled.

        :returns: True if we are done with the request, False otherwise.
        '''
        if self.paused:
            return False

        if self.done:
            return True

        if not self.launch_manager.poll():
            return False

        # If the request has been pulled, unallocate the node set so other
        # requests can use them.
        if not self.zk.getNodeRequest(self.request.id):
            self.log.info("Node request %s disappeared", self.request.id)
            for node in self.nodeset:
                node.allocated_to = None
                self.zk.storeNode(node)
            self.unlockNodeSet()
            self.zk.unlockNodeRequest(self.request)
            return True

        if self.launch_manager.failed_nodes:
            self.log.debug("Declining node request %s because nodes failed",
                           self.request.id)
            self.request.declined_by.append(self.launcher_id)
            launchers = set(self.zk.getRegisteredLaunchers())
            if launchers.issubset(set(self.request.declined_by)):
                # All launchers have declined it
                self.log.debug("Failing declined node request %s",
                               self.request.id)
                self.request.state = zk.FAILED
            else:
                self.request.state = zk.REQUESTED
        else:
            for node in self.nodeset:
                # Record node ID in the request
                self.request.nodes.append(node.id)
            self.log.debug("Fulfilled node request %s",
                           self.request.id)
            self.request.state = zk.FULFILLED

        self.unlockNodeSet()
        self.zk.storeNodeRequest(self.request)
        self.zk.unlockNodeRequest(self.request)
        return True


class PoolWorker(threading.Thread):
    '''
    Class that manages node requests for a single provider pool.

    The NodePool thread will instantiate a class of this type for each
    provider pool found in the nodepool configuration file. If the
    pool or provider to which this thread is assigned is removed from
    the configuration file, then that will be recognized and this
    thread will shut itself down.
    '''

    def __init__(self, nodepool, provider_name, pool_name):
        threading.Thread.__init__(
            self, name='PoolWorker.%s-%s' % (provider_name, pool_name)
        )
        self.log = logging.getLogger("nodepool.%s" % self.name)
        self.nodepool = nodepool
        self.provider_name = provider_name
        self.pool_name = pool_name
        self.running = False
        self.paused_handler = None
        self.request_handlers = []
        self.watermark_sleep = nodepool.watermark_sleep
        self.zk = self.getZK()
        self.launcher_id = "%s-%s-%s" % (socket.gethostname(),
                                         os.getpid(),
                                         self.name)

    #----------------------------------------------------------------
    # Private methods
    #----------------------------------------------------------------

    def _assignHandlers(self):
        '''
        For each request we can grab, create a NodeRequestHandler for it.

        The NodeRequestHandler object will kick off any threads needed to
        satisfy the request, then return. We will need to periodically poll
        the handler for completion.
        '''
        provider = self.getProviderConfig()
        if provider.max_concurrency == 0:
            return

        for req_id in self.zk.getNodeRequests():
            if self.paused_handler:
                return

            # Get active threads for all pools for this provider
            active_threads = sum([
                w.activeThreads() for
                w in self.nodepool.getPoolWorkers(self.provider_name)
            ])

            # Short-circuit for limited request handling
            if (provider.max_concurrency > 0 and
                active_threads >= provider.max_concurrency
            ):
                return

            req = self.zk.getNodeRequest(req_id)
            if not req:
                continue

            # Only interested in unhandled requests
            if req.state != zk.REQUESTED:
                continue

            # Skip it if we've already declined
            if self.launcher_id in req.declined_by:
                continue

            try:
                self.zk.lockNodeRequest(req, blocking=False)
            except exceptions.ZKLockException:
                continue

            # Make sure the state didn't change on us after getting the lock
            req2 = self.zk.getNodeRequest(req_id)
            if req2 and req2.state != zk.REQUESTED:
                self.zk.unlockNodeRequest(req)
                continue

            # Got a lock, so assign it
            self.log.info("Assigning node request %s" % req)
            rh = NodeRequestHandler(self, req)
            rh.run()
            if rh.paused:
                self.paused_handler = rh
            self.request_handlers.append(rh)

    def _removeCompletedHandlers(self):
        '''
        Poll handlers to see which have completed.
        '''
        active_handlers = []
        for r in self.request_handlers:
            if not r.poll():
                active_handlers.append(r)
        self.request_handlers = active_handlers

    #----------------------------------------------------------------
    # Public methods
    #----------------------------------------------------------------

    def activeThreads(self):
        '''
        Return the number of alive threads in use by this provider.

        This is an approximate, top-end number for alive threads, since some
        threads obviously may have finished by the time we finish the
        calculation.
        '''
        total = 0
        for r in self.request_handlers:
            total += r.alive_thread_count
        return total

    def getZK(self):
        return self.nodepool.getZK()

    def getProviderConfig(self):
        return self.nodepool.config.providers[self.provider_name]

    def getPoolConfig(self):
        return self.getProviderConfig().pools[self.pool_name]

    def getProviderManager(self):
        return self.nodepool.getProviderManager(self.provider_name)

    def run(self):
        self.running = True

        while self.running:
            # Don't do work if we've lost communication with the ZK cluster
            while self.zk and (self.zk.suspended or self.zk.lost):
                self.log.info("ZooKeeper suspended. Waiting")
                time.sleep(SUSPEND_WAIT_TIME)

            # Make sure we're always registered with ZK
            self.zk.registerLauncher(self.launcher_id)

            try:
                if not self.paused_handler:
                    self._assignHandlers()
                else:
                    # If we are paused, one request handler could not
                    # satisify its assigned request, so give it
                    # another shot. Unpause ourselves if it completed.
                    self.paused_handler.run()
                    if not self.paused_handler.paused:
                        self.paused_handler = None

                self._removeCompletedHandlers()
            except Exception:
                self.log.exception("Error in PoolWorker:")
            time.sleep(self.watermark_sleep)

        # Cleanup on exit
        if self.paused_handler:
            self.paused_handler.unlockNodeSet(clear_allocation=True)

    def stop(self):
        '''
        Shutdown the PoolWorker thread.

        Do not wait for the request handlers to finish. Any nodes
        that are in the process of launching will be cleaned up on a
        restart. They will be unlocked and BUILDING in ZooKeeper.
        '''
        self.log.info("%s received stop" % self.name)
        self.running = False


class BaseCleanupWorker(threading.Thread):
    def __init__(self, nodepool, interval, name):
        threading.Thread.__init__(self, name=name)
        self._nodepool = nodepool
        self._interval = interval
        self._running = False

    def _deleteInstance(self, node):
        '''
        Delete an instance from a provider.

        A thread will be spawned to delete the actual instance from the
        provider.

        :param Node node: A Node object representing the instance to delete.
        '''
        self.log.info("Deleting %s instance %s from %s",
                      node.state, node.external_id, node.provider)
        try:
            t = InstanceDeleter(
                self._nodepool.getZK(),
                self._nodepool.getProviderManager(node.provider),
                node)
            t.start()
        except Exception:
            self.log.exception("Could not delete instance %s on provider %s",
                               node.external_id, node.provider)

    def run(self):
        self.log.info("Starting")
        self._running = True

        while self._running:
            # Don't do work if we've lost communication with the ZK cluster
            zk_conn = self._nodepool.getZK()
            while zk_conn and (zk_conn.suspended or zk_conn.lost):
                self.log.info("ZooKeeper suspended. Waiting")
                time.sleep(SUSPEND_WAIT_TIME)

            self._run()
            time.sleep(self._interval)

        self.log.info("Stopped")

    def stop(self):
        self._running = False
        self.join()


class CleanupWorker(BaseCleanupWorker):
    def __init__(self, nodepool, interval):
        super(CleanupWorker, self).__init__(
            nodepool, interval, name='CleanupWorker')
        self.log = logging.getLogger("nodepool.CleanupWorker")

    def _resetLostRequest(self, zk_conn, req):
        '''
        Reset the request state and unallocate nodes.

        :param ZooKeeper zk_conn: A ZooKeeper connection object.
        :param NodeRequest req: The lost NodeRequest object.
        '''
        # Double check the state after the lock
        req = zk_conn.getNodeRequest(req.id)
        if req.state != zk.PENDING:
            return

        for node in zk_conn.nodeIterator():
            if node.allocated_to == req.id:
                try:
                    zk_conn.lockNode(node)
                except exceptions.ZKLockException:
                    self.log.warning(
                        "Unable to unallocate node %s from request %s",
                        node.id, req.id)
                    return

                node.allocated_to = None
                zk_conn.storeNode(node)
                zk_conn.unlockNode(node)
                self.log.debug("Unallocated lost request node %s", node.id)

        req.state = zk.REQUESTED
        req.nodes = []
        zk_conn.storeNodeRequest(req)
        self.log.info("Reset lost request %s", req.id)

    def _cleanupLostRequests(self):
        '''
        Look for lost requests and reset them.

        A lost request is a node request that was left in the PENDING state
        when nodepool exited. We need to look for these (they'll be unlocked)
        and disassociate any nodes we've allocated to the request and reset
        the request state to REQUESTED so it will be processed again.
        '''
        zk_conn = self._nodepool.getZK()
        for req in zk_conn.nodeRequestIterator():
            if req.state == zk.PENDING:
                try:
                    zk_conn.lockNodeRequest(req, blocking=False)
                except exceptions.ZKLockException:
                    continue

                self._resetLostRequest(zk_conn, req)
                zk_conn.unlockNodeRequest(req)

    def _cleanupNodeRequestLocks(self):
        '''
        Remove request locks where the request no longer exists.

        Because the node request locks are not direct children of the request
        znode, we need to remove the locks separately after the request has
        been processed. Only remove them after LOCK_CLEANUP seconds have
        passed. This helps prevent the scenario where a request could go
        away _while_ a lock is currently held for processing and the cleanup
        thread attempts to delete it. The delay should reduce the chance that
        we delete a currently held lock.
        '''
        zk = self._nodepool.getZK()
        requests = zk.getNodeRequests()
        now = time.time()
        for lock in zk.nodeRequestLockIterator():
            if lock.id in requests:
                continue
            if (now - lock.stat.mtime/1000) > LOCK_CLEANUP:
                zk.deleteNodeRequestLock(lock.id)

    def _cleanupLeakedInstances(self):
        '''
        Delete any leaked server instances.

        Remove any servers we find in providers we know about that are not
        recorded in the ZooKeeper data.
        '''
        zk_conn = self._nodepool.getZK()

        for provider in self._nodepool.config.providers.values():
            manager = self._nodepool.getProviderManager(provider.name)

            for server in manager.listServers():
                meta = server.get('metadata', {})

                if 'nodepool_provider_name' not in meta:
                    continue

                if meta['nodepool_provider_name'] != provider.name:
                    # Another launcher, sharing this provider but configured
                    # with a different name, owns this.
                    continue

                if not zk_conn.getNode(meta['nodepool_node_id']):
                    self.log.warning(
                        "Deleting leaked instance %s (%s) in %s "
                        "(unknown node id %s)",
                        server.name, server.id, provider.name,
                        meta['nodepool_node_id']
                    )
                    # Create an artifical node to use for deleting the server.
                    node = zk.Node()
                    node.external_id = server.id
                    node.provider = provider.name
                    self._deleteInstance(node)

            if provider.clean_floating_ips:
                manager.cleanupLeakedFloaters()

    def _run(self):
        '''
        Catch exceptions individually so that other cleanup routines may
        have a chance.
        '''
        try:
            self._cleanupNodeRequestLocks()
        except Exception:
            self.log.exception(
                "Exception in DeletedNodeWorker (node request lock cleanup):")

        try:
            self._cleanupLeakedInstances()
        except Exception:
            self.log.exception(
                "Exception in DeletedNodeWorker (leaked instance cleanup):")

        try:
            self._cleanupLostRequests()
        except Exception:
            self.log.exception(
                "Exception in DeletedNodeWorker (lost request cleanup):")


class DeletedNodeWorker(BaseCleanupWorker):
    def __init__(self, nodepool, interval):
        super(DeletedNodeWorker, self).__init__(
            nodepool, interval, name='DeletedNodeWorker')
        self.log = logging.getLogger("nodepool.DeletedNodeWorker")

    def _cleanupNodes(self):
        '''
        Delete instances from providers and nodes entries from ZooKeeper.
        '''
        cleanup_states = (zk.USED, zk.IN_USE, zk.BUILDING, zk.FAILED,
                          zk.DELETING)

        zk_conn = self._nodepool.getZK()
        for node in zk_conn.nodeIterator():
            # If a ready node has been allocated to a request, but that
            # request is now missing, deallocate it.
            if (node.state == zk.READY and node.allocated_to
                and not zk_conn.getNodeRequest(node.allocated_to)
            ):
                try:
                    zk_conn.lockNode(node, blocking=False)
                except exceptions.ZKLockException:
                    pass
                else:
                    # Double check node conditions after lock
                    if node.state == zk.READY and node.allocated_to:
                        self.log.debug(
                            "Unallocating node %s with missing request %s",
                            node.id, node.allocated_to)
                        node.allocated_to = None
                        zk_conn.storeNode(node)

                    zk_conn.unlockNode(node)

            # Can't do anything if we aren't configured for this provider.
            if node.provider not in self._nodepool.config.providers:
                continue

            # Any nodes in these states that are unlocked can be deleted.
            if node.state in cleanup_states:
                try:
                    zk_conn.lockNode(node, blocking=False)
                except exceptions.ZKLockException:
                    continue

                # Double check the state now that we have a lock since it
                # may have changed on us.
                if node.state not in cleanup_states:
                    zk_conn.unlockNode(node)
                    continue

                # The InstanceDeleter thread will unlock and remove the
                # node from ZooKeeper if it succeeds.
                self._deleteInstance(node)

    def _run(self):
        try:
            self._cleanupNodes()
        except Exception:
            self.log.exception("Exception in DeletedNodeWorker:")


class NodePool(threading.Thread):
    log = logging.getLogger("nodepool.NodePool")

    def __init__(self, securefile, configfile,
                 watermark_sleep=WATERMARK_SLEEP):
        threading.Thread.__init__(self, name='NodePool')
        self.securefile = securefile
        self.configfile = configfile
        self.watermark_sleep = watermark_sleep
        self.cleanup_interval = 60
        self.delete_interval = 5
        self._stopped = False
        self.config = None
        self.zk = None
        self.statsd = stats.get_client()
        self._pool_threads = {}
        self._cleanup_thread = None
        self._delete_thread = None
        self._wake_condition = threading.Condition()
        self._submittedRequests = {}

    def stop(self):
        self._stopped = True
        self._wake_condition.acquire()
        self._wake_condition.notify()
        self._wake_condition.release()
        if self.config:
            provider_manager.ProviderManager.stopProviders(self.config)

        if self._cleanup_thread:
            self._cleanup_thread.stop()
            self._cleanup_thread.join()

        if self._delete_thread:
            self._delete_thread.stop()
            self._delete_thread.join()

        # Don't let stop() return until all pool threads have been
        # terminated.
        self.log.debug("Stopping pool threads")
        for thd in self._pool_threads.values():
            if thd.isAlive():
                thd.stop()
            self.log.debug("Waiting for %s" % thd.name)
            thd.join()

        if self.isAlive():
            self.join()
        self.zk.disconnect()
        self.log.debug("Finished stopping")

    def loadConfig(self):
        config = nodepool_config.loadConfig(self.configfile)
        nodepool_config.loadSecureConfig(config, self.securefile)
        return config

    def reconfigureZooKeeper(self, config):
        if self.config:
            running = self.config.zookeeper_servers.values()
        else:
            running = None

        configured = config.zookeeper_servers.values()
        if running == configured:
            return

        if not self.zk and configured:
            self.log.debug("Connecting to ZooKeeper servers")
            self.zk = zk.ZooKeeper()
            self.zk.connect(configured)
        else:
            self.log.debug("Detected ZooKeeper server changes")
            self.zk.resetHosts(configured)

    def setConfig(self, config):
        self.config = config

    def getZK(self):
        return self.zk

    def getProviderManager(self, provider_name):
        return self.config.provider_managers[provider_name]

    def getPoolWorkers(self, provider_name):
        return [t for t in self._pool_threads.values() if
                t.provider_name == provider_name]

    def updateConfig(self):
        config = self.loadConfig()
        provider_manager.ProviderManager.reconfigure(self.config, config)
        self.reconfigureZooKeeper(config)
        self.setConfig(config)

    def removeCompletedRequests(self):
        '''
        Remove (locally and in ZK) fulfilled node requests.

        We also must reset the allocated_to attribute for each Node assigned
        to our request, since we are deleting the request.
        '''
        for label in self._submittedRequests.keys():
            label_requests = self._submittedRequests[label]
            active_requests = []

            for req in label_requests:
                req = self.zk.getNodeRequest(req.id)

                if not req:
                    continue

                if req.state == zk.FULFILLED:
                    # Reset node allocated_to
                    for node_id in req.nodes:
                        node = self.zk.getNode(node_id)
                        node.allocated_to = None
                        # NOTE: locking shouldn't be necessary since a node
                        # with allocated_to set should not be locked except
                        # by the creator of the request (us).
                        self.zk.storeNode(node)
                    self.zk.deleteNodeRequest(req)
                elif req.state == zk.FAILED:
                    self.log.debug("min-ready node request failed: %s", req)
                    self.zk.deleteNodeRequest(req)
                else:
                    active_requests.append(req)

            if active_requests:
                self._submittedRequests[label] = active_requests
            else:
                self.log.debug(
                    "No more active min-ready requests for label %s", label)
                del self._submittedRequests[label]

    def labelImageIsAvailable(self, label):
        '''
        Check if the image associated with a label is ready in any provider.

        :param Label label: The label config object.

        :returns: True if image associated with the label is uploaded and
            ready in at least one provider. False otherwise.
        '''
        for pool in label.pools:
            for pool_label in pool.labels.values():
                if self.zk.getMostRecentImageUpload(pool_label.diskimage.name,
                                                    pool.provider.name):
                    return True
        return False

        for provider_name in label.providers.keys():
            if self.zk.getMostRecentImageUpload(label.image, provider_name):
                return True
        return False

    def createMinReady(self):
        '''
        Create node requests to make the minimum amount of ready nodes.

        Since this method will be called repeatedly, we need to take care to
        note when we have already submitted node requests to satisfy min-ready.
        Requests we've already submitted are stored in the _submittedRequests
        dict, keyed by label.
        '''
        def createRequest(label_name):
            req = zk.NodeRequest()
            req.state = zk.REQUESTED
            req.requestor = "NodePool:min-ready"
            req.node_types.append(label_name)
            req.reuse = False    # force new node launches
            self.zk.storeNodeRequest(req)
            if label_name not in self._submittedRequests:
                self._submittedRequests[label_name] = []
            self._submittedRequests[label_name].append(req)

        # Since we could have already submitted node requests, do not
        # resubmit a request for a type if a request for that type is
        # still in progress.
        self.removeCompletedRequests()
        label_names = self.config.labels.keys()
        requested_labels = self._submittedRequests.keys()
        needed_labels = list(set(label_names) - set(requested_labels))

        ready_nodes = self.zk.getReadyNodesOfTypes(needed_labels)

        for label in self.config.labels.values():
            if label.name not in needed_labels:
                continue
            min_ready = label.min_ready
            if min_ready == -1:
                continue   # disabled

            # Calculate how many nodes of this type we need created
            need = 0
            if label.name not in ready_nodes.keys():
                need = label.min_ready
            elif len(ready_nodes[label.name]) < min_ready:
                need = min_ready - len(ready_nodes[label.name])

            if need and self.labelImageIsAvailable(label):
                # Create requests for 1 node at a time. This helps to split
                # up requests across providers, and avoids scenario where a
                # single provider might fail the entire request because of
                # quota (e.g., min-ready=2, but max-servers=1).
                self.log.info("Creating requests for %d %s nodes",
                              need, label.name)
                for i in range(0, need):
                    createRequest(label.name)

    def run(self):
        '''
        Start point for the NodePool thread.
        '''
        while not self._stopped:
            try:
                self.updateConfig()

                # Don't do work if we've lost communication with the ZK cluster
                while self.zk and (self.zk.suspended or self.zk.lost):
                    self.log.info("ZooKeeper suspended. Waiting")
                    time.sleep(SUSPEND_WAIT_TIME)

                self.createMinReady()

                if not self._cleanup_thread:
                    self._cleanup_thread = CleanupWorker(
                        self, self.cleanup_interval)
                    self._cleanup_thread.start()

                if not self._delete_thread:
                    self._delete_thread = DeletedNodeWorker(
                        self, self.delete_interval)
                    self._delete_thread.start()

                # Stop any PoolWorker threads if the pool was removed
                # from the config.
                pool_keys = set()
                for provider in self.config.providers.values():
                    for pool in provider.pools.values():
                        pool_keys.add(provider.name + '-' + pool.name)

                for key in self._pool_threads.keys():
                    if key not in pool_keys:
                        self._pool_threads[key].stop()

                # Start (or restart) provider threads for each provider in
                # the config. Removing a provider from the config and then
                # adding it back would cause a restart.
                for provider in self.config.providers.values():
                    for pool in provider.pools.values():
                        key = provider.name + '-' + pool.name
                        if key not in self._pool_threads.keys():
                            t = PoolWorker(self, provider.name, pool.name)
                            self.log.info( "Starting %s" % t.name)
                            t.start()
                            self._pool_threads[key] = t
                        elif not self._pool_threads[key].isAlive():
                            self._pool_threads[key].join()
                            t = PoolWorker(self, provider.name, pool.name)
                            self.log.info( "Restarting %s" % t.name)
                            t.start()
                            self._pool_threads[key] = t
            except Exception:
                self.log.exception("Exception in main loop:")

            self._wake_condition.acquire()
            self._wake_condition.wait(self.watermark_sleep)
            self._wake_condition.release()
