# Copyright 2018 Red Hat
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
import math
import time

from nodepool import exceptions
from nodepool import zk
from nodepool.driver.utils import NodeLauncher, QuotaInformation
from nodepool.driver import NodeRequestHandler
from nodepool.nodeutils import nodescan


class AwsInstanceLauncher(NodeLauncher):
    def __init__(self, handler, node, provider_config, provider_label):
        super().__init__(handler.zk, node, provider_config)
        self.provider_name = provider_config.name
        self.retries = provider_config.launch_retries
        self.pool = provider_config.pools[provider_label.pool.name]
        self.handler = handler
        self.zk = handler.zk
        self.boot_timeout = provider_config.boot_timeout
        self.label = provider_label

    def launch(self):
        self.log.debug("Starting %s instance" % self.node.type)
        attempts = 1
        while attempts <= self.retries:
            try:
                instance = self.handler.manager.createInstance(self.label)
                break
            except Exception:
                if attempts <= self.retries:
                    self.log.exception(
                        "Launch attempt %d/%d failed for node %s:",
                        attempts, self.retries, self.node.id)
                if attempts == self.retries:
                    raise
                attempts += 1
            time.sleep(1)

        instance_id = instance.id
        self.node.external_id = instance_id
        self.zk.storeNode(self.node)

        boot_start = time.monotonic()
        while time.monotonic() - boot_start < self.boot_timeout:
            state = instance.state.get('Name')
            self.log.debug("Instance %s is %s" % (instance_id, state))
            if state == 'running':
                instance.create_tags(Tags=[{'Key': 'nodepool_id',
                                            'Value': str(self.node.id)}])
                instance.create_tags(Tags=[{'Key': 'nodepool_pool',
                                            'Value': str(self.pool.name)}])
                instance.create_tags(Tags=[{'Key': 'nodepool_provider',
                                            'Value': str(self.provider_name)}])
                break
            time.sleep(0.5)
            instance.reload()
        if state != 'running':
            raise exceptions.LaunchStatusException(
                "Instance %s failed to start: %s" % (instance_id, state))

        server_ip = instance.public_ip_address
        if not server_ip:
            raise exceptions.LaunchStatusException(
                "Instance %s doesn't have a public ip" % instance_id)

        self.node.connection_port = self.label.cloud_image.connection_port
        self.node.connection_type = self.label.cloud_image.connection_type
        if self.pool.host_key_checking:
            try:
                if (self.node.connection_type == 'ssh' or
                        self.node.connection_type == 'network_cli'):
                    gather_hostkeys = True
                else:
                    gather_hostkeys = False
                keys = nodescan(server_ip, port=self.node.connection_port,
                                timeout=180, gather_hostkeys=gather_hostkeys)
            except Exception:
                raise exceptions.LaunchKeyscanException(
                    "Can't scan instance %s key" % instance_id)

        self.log.info("Instance %s ready" % instance_id)
        self.node.state = zk.READY
        self.node.external_id = instance_id
        self.node.hostname = server_ip
        self.node.interface_ip = server_ip
        self.node.public_ipv4 = server_ip
        self.node.host_keys = keys
        self.node.username = self.label.cloud_image.username
        self.zk.storeNode(self.node)
        self.log.info("Instance %s is ready", instance_id)


class AwsNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.aws."
                            "AwsNodeRequestHandler")

    def __init__(self, pw, request):
        super().__init__(pw, request)
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

        :returns: True if it is available, False otherwise.
        '''
        if self.provider.manage_images:
            for label in self.request.node_types:
                if self.pool.labels[label].cloud_image:
                    if not self.manager.labelReady(self.pool.labels[label]):
                        return False
        return True

    def hasRemainingQuota(self, ntype):
        '''
        Apply max_servers check, ignoring other quotas.

        :returns: True if we have room, False otherwise.
        '''
        needed_quota = QuotaInformation(cores=1, instances=1, ram=1, default=1)
        n_running = self.manager.countNodes(self.pool.name)
        pool_quota = QuotaInformation(
            cores=math.inf,
            instances=self.pool.max_servers - n_running,
            ram=math.inf,
            default=math.inf)
        pool_quota.subtract(needed_quota)
        self.log.debug("hasRemainingQuota({},{}) = {}".format(
            self.pool, ntype, pool_quota))
        return pool_quota.non_negative()

    def hasProviderQuota(self, node_types):
        '''
        Apply max_servers check to a whole request

        :returns: True if we have room, False otherwise.
        '''
        needed_quota = QuotaInformation(
            cores=1,
            instances=len(node_types),
            ram=1,
            default=1)
        pool_quota = QuotaInformation(
            cores=math.inf,
            instances=self.pool.max_servers,
            ram=math.inf,
            default=math.inf)
        pool_quota.subtract(needed_quota)
        self.log.debug("hasProviderQuota({},{}) = {}".format(
            self.pool, node_types, pool_quota))
        return pool_quota.non_negative()

    def launchesComplete(self):
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

        node_states = [node.state for node in self.nodeset]

        # NOTE: It very important that NodeLauncher always sets one of
        # these states, no matter what.
        if not all(s in (zk.READY, zk.FAILED) for s in node_states):
            return False

        return True

    def launch(self, node):
        label = self.pool.labels[node.type[0]]
        thd = AwsInstanceLauncher(self, node, self.provider, label)
        thd.start()
        self._threads.append(thd)
