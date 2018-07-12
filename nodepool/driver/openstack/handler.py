# Copyright (C) 2011-2014 OpenStack Foundation
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

import math
import pprint
import random

from kazoo import exceptions as kze

from nodepool import exceptions
from nodepool import nodeutils as utils
from nodepool import zk
from nodepool.driver.utils import NodeLauncher, QuotaInformation
from nodepool.driver import NodeRequestHandler


class OpenStackNodeLauncher(NodeLauncher):
    def __init__(self, handler, node, provider_config, provider_label):
        '''
        Initialize the launcher.

        :param OpenStackNodeRequestHandler handler: The handler object.
        :param Node node: A Node object describing the node to launch.
        :param ProviderConfig provider_config: A ProviderConfig object
            describing the provider launching this node.
        :param ProviderLabel provider_label: A ProviderLabel object
            describing the label to use for the node.
        '''
        super().__init__(handler.zk, node, provider_config)

        # Number of times to retry failed launches.
        self._retries = provider_config.launch_retries

        self.label = provider_label
        self.pool = provider_label.pool
        self.handler = handler
        self.zk = handler.zk

    def _logConsole(self, server_id, hostname):
        if not self.label.console_log:
            return
        console = self.handler.manager.getServerConsole(server_id)
        if console:
            self.log.debug('Console log from hostname %s:' % hostname)
            for line in console.splitlines():
                self.log.debug(line.rstrip())

    def _launchNode(self):
        if self.label.diskimage:
            diskimage = self.provider_config.diskimages[
                self.label.diskimage.name]
        else:
            diskimage = None

        if diskimage:
            # launch using diskimage
            cloud_image = self.handler.zk.getMostRecentImageUpload(
                diskimage.name, self.provider_config.name)

            if not cloud_image:
                raise exceptions.LaunchNodepoolException(
                    "Unable to find current cloud image %s in %s" %
                    (diskimage.name, self.provider_config.name)
                )

            config_drive = diskimage.config_drive
            # Using a dict with the ID bypasses an image search during
            # server creation.
            image_external = dict(id=cloud_image.external_id)
            image_id = "{path}/{upload_id}".format(
                path=self.handler.zk._imageUploadPath(
                    cloud_image.image_name,
                    cloud_image.build_id,
                    cloud_image.provider_name),
                upload_id=cloud_image.id)
            image_name = diskimage.name
            username = cloud_image.username
            connection_type = diskimage.connection_type
            connection_port = diskimage.connection_port

        else:
            # launch using unmanaged cloud image
            config_drive = self.label.cloud_image.config_drive

            if self.label.cloud_image.image_id:
                # Using a dict with the ID bypasses an image search during
                # server creation.
                image_external = dict(id=self.label.cloud_image.image_id)
            else:
                image_external = self.label.cloud_image.external_name

            image_id = self.label.cloud_image.name
            image_name = self.label.cloud_image.name
            username = self.label.cloud_image.username
            connection_type = self.label.cloud_image.connection_type
            connection_port = self.label.cloud_image.connection_port

        hostname = self.provider_config.hostname_format.format(
            label=self.label, provider=self.provider_config, node=self.node
        )

        self.log.info("Creating server with hostname %s in %s from image %s "
                      "for node id: %s" % (hostname,
                                           self.provider_config.name,
                                           image_name,
                                           self.node.id))

        # NOTE: We store the node ID in the server metadata to use for leaked
        # instance detection. We cannot use the external server ID for this
        # because that isn't available in ZooKeeper until after the server is
        # active, which could cause a race in leak detection.

        server = self.handler.manager.createServer(
            hostname,
            image=image_external,
            min_ram=self.label.min_ram,
            flavor_name=self.label.flavor_name,
            key_name=self.label.key_name,
            az=self.node.az,
            config_drive=config_drive,
            nodepool_node_id=self.node.id,
            nodepool_node_label=self.node.type[0],
            nodepool_image_name=image_name,
            networks=self.pool.networks,
            security_groups=self.pool.security_groups,
            boot_from_volume=self.label.boot_from_volume,
            volume_size=self.label.volume_size)

        self.node.external_id = server.id
        self.node.hostname = hostname
        self.node.image_id = image_id
        if username:
            self.node.username = username
        self.node.connection_type = connection_type
        self.node.connection_port = connection_port

        # Checkpoint save the updated node info
        self.zk.storeNode(self.node)

        self.log.debug("Waiting for server %s for node id: %s" %
                       (server.id, self.node.id))
        server = self.handler.manager.waitForServer(
            server, self.provider_config.launch_timeout,
            auto_ip=self.pool.auto_floating_ip)

        if server.status != 'ACTIVE':
            raise exceptions.LaunchStatusException("Server %s for node id: %s "
                                                   "status: %s" %
                                                   (server.id, self.node.id,
                                                    server.status))

        # If we didn't specify an AZ, set it to the one chosen by Nova.
        # Do this after we are done waiting since AZ may not be available
        # immediately after the create request.
        if not self.node.az:
            self.node.az = server.location.zone

        interface_ip = server.interface_ip
        if not interface_ip:
            self.log.debug(
                "Server data for failed IP: %s" % pprint.pformat(
                    server))
            raise exceptions.LaunchNetworkException(
                "Unable to find public IP of server")

        self.node.interface_ip = interface_ip
        self.node.public_ipv4 = server.public_v4
        self.node.public_ipv6 = server.public_v6
        self.node.private_ipv4 = server.private_v4
        # devstack-gate multi-node depends on private_v4 being populated
        # with something. On clouds that don't have a private address, use
        # the public.
        if not self.node.private_ipv4:
            self.node.private_ipv4 = server.public_v4

        # Checkpoint save the updated node info
        self.zk.storeNode(self.node)

        self.log.debug(
            "Node %s is running [region: %s, az: %s, ip: %s ipv4: %s, "
            "ipv6: %s]" %
            (self.node.id, self.node.region, self.node.az,
             self.node.interface_ip, self.node.public_ipv4,
             self.node.public_ipv6))

        # wait and scan the new node and record in ZooKeeper
        host_keys = []
        if self.pool.host_key_checking:
            try:
                self.log.debug(
                    "Gathering host keys for node %s", self.node.id)
                # only gather host keys if the connection type is ssh
                gather_host_keys = connection_type == 'ssh'
                host_keys = utils.nodescan(
                    interface_ip,
                    timeout=self.provider_config.boot_timeout,
                    gather_hostkeys=gather_host_keys,
                    port=connection_port)

                if gather_host_keys and not host_keys:
                    raise exceptions.LaunchKeyscanException(
                        "Unable to gather host keys")
            except exceptions.ConnectionTimeoutException:
                self._logConsole(self.node.external_id, self.node.hostname)
                raise

        self.node.host_keys = host_keys
        self.zk.storeNode(self.node)

    def launch(self):
        attempts = 1
        while attempts <= self._retries:
            try:
                self._launchNode()
                break
            except kze.SessionExpiredError:
                # If we lost our ZooKeeper session, we've lost our node lock
                # so there's no need to continue.
                raise
            except Exception as e:
                if attempts <= self._retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self._retries, self.node.id)
                # If we created an instance, delete it.
                if self.node.external_id:
                    self.handler.manager.cleanupNode(self.node.external_id)
                    self.handler.manager.waitForNodeCleanup(
                        self.node.external_id)
                    self.node.external_id = None
                    self.node.public_ipv4 = None
                    self.node.public_ipv6 = None
                    self.node.interface_ip = None
                    self.zk.storeNode(self.node)
                if attempts == self._retries:
                    raise
                if 'quota exceeded' in str(e).lower():
                    # A quota exception is not directly recoverable so bail
                    # out immediately with a specific exception.
                    self.log.info("Quota exceeded, invalidating quota cache")
                    self.handler.manager.invalidateQuotaCache()
                    raise exceptions.QuotaException("Quota exceeded")
                attempts += 1

        self.node.state = zk.READY
        self.zk.storeNode(self.node)
        self.log.info("Node id %s is ready", self.node.id)


class OpenStackNodeRequestHandler(NodeRequestHandler):

    def __init__(self, pw, request):
        super().__init__(pw, request)
        self.chosen_az = None
        self._threads = []

    @property
    def alive_thread_count(self):
        count = 0
        for t in self._threads:
            if t.isAlive():
                count += 1
        return count

    def imagesAvailable(self):
        '''
        Determines if the requested images are available for this provider.

        ZooKeeper is queried for an image uploaded to the provider that is
        in the READY state.

        :returns: True if it is available, False otherwise.
        '''
        if self.provider.manage_images:
            for label in self.request.node_types:
                if self.pool.labels[label].cloud_image:
                    if not self.manager.labelReady(self.pool.labels[label]):
                        return False
                else:
                    if not self.zk.getMostRecentImageUpload(
                            self.pool.labels[label].diskimage.name,
                            self.provider.name):
                        return False
        return True

    def hasRemainingQuota(self, ntype):
        needed_quota = self.manager.quotaNeededByNodeType(ntype, self.pool)

        if not self.pool.ignore_provider_quota:
            # Calculate remaining quota which is calculated as:
            # quota = <total nodepool quota> - <used quota> - <quota for node>
            cloud_quota = self.manager.estimatedNodepoolQuota()
            cloud_quota.subtract(
                self.manager.estimatedNodepoolQuotaUsed(self.zk))
            cloud_quota.subtract(needed_quota)
            self.log.debug("Predicted remaining provider quota: %s",
                           cloud_quota)

            if not cloud_quota.non_negative():
                return False

        # Now calculate pool specific quota. Values indicating no quota default
        # to math.inf representing infinity that can be calculated with.
        pool_quota = QuotaInformation(cores=self.pool.max_cores,
                                      instances=self.pool.max_servers,
                                      ram=self.pool.max_ram,
                                      default=math.inf)
        pool_quota.subtract(
            self.manager.estimatedNodepoolQuotaUsed(self.zk, self.pool))
        self.log.debug("Current pool quota: %s" % pool_quota)
        pool_quota.subtract(needed_quota)
        self.log.debug("Predicted remaining pool quota: %s", pool_quota)

        return pool_quota.non_negative()

    def hasProviderQuota(self, node_types):
        needed_quota = QuotaInformation()

        for ntype in node_types:
            needed_quota.add(
                self.manager.quotaNeededByNodeType(ntype, self.pool))

        if not self.pool.ignore_provider_quota:
            cloud_quota = self.manager.estimatedNodepoolQuota()
            cloud_quota.subtract(needed_quota)

            if not cloud_quota.non_negative():
                return False

        # Now calculate pool specific quota. Values indicating no quota default
        # to math.inf representing infinity that can be calculated with.
        pool_quota = QuotaInformation(cores=self.pool.max_cores,
                                      instances=self.pool.max_servers,
                                      ram=self.pool.max_ram,
                                      default=math.inf)
        pool_quota.subtract(needed_quota)
        return pool_quota.non_negative()

    def checkReusableNode(self, node):
        if self.chosen_az and node.az != self.chosen_az:
            return False
        return True

    def nodeReusedNotification(self, node):
        """
        We attempt to group the node set within the same provider availability
        zone.
        For this to work properly, the provider entry in the nodepool
        config must list the availability zones. Otherwise, new nodes will be
        put in random AZs at nova's whim. The exception being if there is an
        existing node in the READY state that we can select for this node set.
        Its AZ will then be used for new nodes, as well as any other READY
        nodes.
        """
        # If we haven't already chosen an AZ, select the
        # AZ from this ready node. This will cause new nodes
        # to share this AZ, as well.
        if not self.chosen_az and node.az:
            self.chosen_az = node.az

    def setNodeMetadata(self, node):
        """
        Select grouping AZ if we didn't set AZ from a selected,
        pre-existing node
        """
        if not self.chosen_az:
            self.chosen_az = random.choice(
                self.pool.azs or self.manager.getAZs())
        node.az = self.chosen_az
        node.cloud = self.provider.cloud_config.name
        node.region = self.provider.region_name

    def launchesComplete(self):
        '''
        Check if all launch requests have completed.

        When all of the Node objects have reached a final state (READY, FAILED
        or ABORTED), we'll know all threads have finished the launch process.
        '''
        if not self._threads:
            return True

        # Give the NodeLaunch threads time to finish.
        if self.alive_thread_count:
            return False

        node_states = [node.state for node in self.nodeset]

        # NOTE: It's very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED, zk.ABORTED)
                   for s in node_states):
            return False

        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = OpenStackNodeLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
