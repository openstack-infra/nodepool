# Copyright (C) 2011-2013 OpenStack Foundation
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

import copy
import logging
import operator
import os
import time

import openstack

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.driver.utils import QuotaInformation
from nodepool.nodeutils import iterate_timeout
from nodepool import stats
from nodepool import version
from nodepool import zk

# Import entire module to avoid partial-loading, circular import
from nodepool.driver.openstack import handler


IPS_LIST_AGE = 5      # How long to keep a cached copy of the ip list
MAX_QUOTA_AGE = 5 * 60  # How long to keep the quota information cached


class OpenStackProvider(Provider):
    log = logging.getLogger("nodepool.driver.openstack.OpenStackProvider")

    def __init__(self, provider):
        self.provider = provider
        self._images = {}
        self._networks = {}
        self.__flavors = {}  # TODO(gtema): caching
        self.__azs = None
        self._current_nodepool_quota = None
        self._zk = None
        self._down_ports = set()
        self._last_port_cleanup = None
        self._port_cleanup_interval_secs = 180
        self._statsd = stats.get_client()

    def start(self, zk_conn):
        self.resetClient()
        self._zk = zk_conn

    def stop(self):
        pass

    def join(self):
        pass

    def getRequestHandler(self, poolworker, request):
        return handler.OpenStackNodeRequestHandler(poolworker, request)

    # TODO(gtema): caching
    @property
    def _flavors(self):
        if not self.__flavors:
            self.__flavors = self._getFlavors()
        return self.__flavors

    def _getClient(self):
        rate_limit = None
        # nodepool tracks rate limit in time between requests.
        # openstacksdk tracks rate limit in requests per second.
        # 1/time = requests-per-second.
        if self.provider.rate:
            rate_limit = 1 / self.provider.rate
        return openstack.connection.Connection(
            config=self.provider.cloud_config,
            rate_limit=rate_limit,
            statsd_host=os.getenv('STATSD_HOST', None),
            statsd_port=os.getenv('STATSD_PORT ', None),
            statsd_prefix='nodepool.task.{0}'.format(self.provider.name),
            app_name='nodepool',
            app_version=version.version_info.version_string()
        )

    def quotaNeededByNodeType(self, ntype, pool):
        provider_label = pool.labels[ntype]

        flavor = self.findFlavor(provider_label.flavor_name,
                                 provider_label.min_ram)

        return QuotaInformation.construct_from_flavor(flavor)

    def estimatedNodepoolQuota(self):
        '''
        Determine how much quota is available for nodepool managed resources.
        This needs to take into account the quota of the tenant, resources
        used outside of nodepool and the currently used resources by nodepool,
        max settings in nodepool config. This is cached for MAX_QUOTA_AGE
        seconds.

        :return: Total amount of resources available which is currently
                 available to nodepool including currently existing nodes.
        '''

        if self._current_nodepool_quota:
            now = time.time()
            if now < self._current_nodepool_quota['timestamp'] + MAX_QUOTA_AGE:
                return copy.deepcopy(self._current_nodepool_quota['quota'])

        limits = self._client.get_compute_limits()

        # This is initialized with the full tenant quota and later becomes
        # the quota available for nodepool.
        nodepool_quota = QuotaInformation.construct_from_limits(limits)
        self.log.debug("Provider quota for %s: %s",
                       self.provider.name, nodepool_quota)

        # Subtract the unmanaged quota usage from nodepool_max
        # to get the quota available for us.
        nodepool_quota.subtract(self.unmanagedQuotaUsed())

        self._current_nodepool_quota = {
            'quota': nodepool_quota,
            'timestamp': time.time()
        }

        self.log.debug("Available quota for %s: %s",
                       self.provider.name, nodepool_quota)

        return copy.deepcopy(nodepool_quota)

    def invalidateQuotaCache(self):
        self._current_nodepool_quota['timestamp'] = 0

    def estimatedNodepoolQuotaUsed(self, pool=None):
        '''
        Sums up the quota used (or planned) currently by nodepool. If pool is
        given it is filtered by the pool.

        :param pool: If given, filtered by the pool.
        :return: Calculated quota in use by nodepool
        '''
        used_quota = QuotaInformation()

        for node in self._zk.nodeIterator():
            if node.provider == self.provider.name:
                try:
                    if pool and not node.pool == pool.name:
                        continue
                    provider_pool = self.provider.pools.get(node.pool)
                    if not provider_pool:
                        self.log.warning(
                            "Cannot find provider pool for node %s" % node)
                        # This node is in a funny state we log it for debugging
                        # but move on and don't account it as we can't properly
                        # calculate its cost without pool info.
                        continue
                    if node.type[0] not in provider_pool.labels:
                        self.log.warning("Node type is not in provider pool "
                                         "for node %s" % node)
                        # This node is also in a funny state; the config
                        # may have changed under it.  It should settle out
                        # eventually when it's deleted.
                        continue
                    node_resources = self.quotaNeededByNodeType(
                        node.type[0], provider_pool)
                    used_quota.add(node_resources)
                except Exception:
                    self.log.exception("Couldn't consider invalid node %s "
                                       "for quota:" % node)
        return used_quota

    def unmanagedQuotaUsed(self):
        '''
        Sums up the quota used by servers unmanaged by nodepool.

        :return: Calculated quota in use by unmanaged servers
        '''
        flavors = self.listFlavorsById()
        used_quota = QuotaInformation()

        node_ids = set([n.id for n in self._zk.nodeIterator()])

        for server in self.listNodes():
            meta = server.get('metadata', {})

            nodepool_provider_name = meta.get('nodepool_provider_name')
            if (nodepool_provider_name and
                nodepool_provider_name == self.provider.name):
                # This provider (regardless of the launcher) owns this
                # server so it must not be accounted for unmanaged
                # quota; unless it has leaked.
                nodepool_node_id = meta.get('nodepool_node_id')
                # FIXME(tobiash): Add a test case for this
                if nodepool_node_id and nodepool_node_id in node_ids:
                    # It has not leaked.
                    continue

            flavor = flavors.get(server.flavor.id)
            used_quota.add(QuotaInformation.construct_from_flavor(flavor))

        return used_quota

    def resetClient(self):
        self._client = self._getClient()

    def _getFlavors(self):
        flavors = self.listFlavors()
        flavors.sort(key=operator.itemgetter('ram'))
        return flavors

    # TODO(gtema): These next three methods duplicate logic that is in
    #              openstacksdk, caching is not enabled there by default
    #              Remove it when caching is default
    def _findFlavorByName(self, flavor_name):
        for f in self._flavors:
            if flavor_name in (f['name'], f['id']):
                return f
        raise Exception("Unable to find flavor: %s" % flavor_name)

    def _findFlavorByRam(self, min_ram, flavor_name):
        for f in self._flavors:
            if (f['ram'] >= min_ram
                    and (not flavor_name or flavor_name in f['name'])):
                return f
        raise Exception("Unable to find flavor with min ram: %s" % min_ram)

    def findFlavor(self, flavor_name, min_ram):
        # Note: this will throw an error if the provider is offline
        # but all the callers are in threads (they call in via CreateServer) so
        # the mainloop won't be affected.
        # TODO(gtema): enable commented block when openstacksdk has caching
        # enabled by default
        # if min_ram:
        #     return self._client.get_flavor_by_ram(
        #         ram=min_ram,
        #         include=flavor_name,
        #         get_extra=False)
        # else:
        #     return self._client.get_flavor(flavor_name, get_extra=False)

        if min_ram:
            return self._findFlavorByRam(min_ram, flavor_name)
        else:
            return self._findFlavorByName(flavor_name)

    def findImage(self, name):
        if name in self._images:
            return self._images[name]

        image = self._client.get_image(name)
        self._images[name] = image
        return image

    def findNetwork(self, name):
        if name in self._networks:
            return self._networks[name]

        network = self._client.get_network(name)
        self._networks[name] = network
        return network

    def deleteImage(self, name):
        if name in self._images:
            del self._images[name]

        return self._client.delete_image(name)

    def createServer(self, name, image,
                     flavor_name=None, min_ram=None,
                     az=None, key_name=None, config_drive=True,
                     nodepool_node_id=None, nodepool_node_label=None,
                     nodepool_image_name=None,
                     networks=None, security_groups=None,
                     boot_from_volume=False, volume_size=50,
                     instance_properties=None, userdata=None):
        if not networks:
            networks = []
        if not isinstance(image, dict):
            # if it's a dict, we already have the cloud id. If it's not,
            # we don't know if it's name or ID so need to look it up
            image = self.findImage(image)
        flavor = self.findFlavor(flavor_name=flavor_name, min_ram=min_ram)
        create_args = dict(name=name,
                           image=image,
                           flavor=flavor,
                           config_drive=config_drive)
        if boot_from_volume:
            create_args['boot_from_volume'] = boot_from_volume
            create_args['volume_size'] = volume_size
            # NOTE(pabelanger): Always cleanup volumes when we delete a server.
            create_args['terminate_volume'] = True
        if key_name:
            create_args['key_name'] = key_name
        if az:
            create_args['availability_zone'] = az
        if security_groups:
            create_args['security_groups'] = security_groups
        if userdata:
            create_args['userdata'] = userdata
        nics = []
        for network in networks:
            net_id = self.findNetwork(network)['id']
            nics.append({'net-id': net_id})
        if nics:
            create_args['nics'] = nics
        # Put provider.name and image_name in as groups so that ansible
        # inventory can auto-create groups for us based on each of those
        # qualities
        # Also list each of those values directly so that non-ansible
        # consumption programs don't need to play a game of knowing that
        # groups[0] is the image name or anything silly like that.
        groups_list = [self.provider.name]

        if nodepool_image_name:
            groups_list.append(nodepool_image_name)
        if nodepool_node_label:
            groups_list.append(nodepool_node_label)
        meta = dict(
            groups=",".join(groups_list),
            nodepool_provider_name=self.provider.name,
        )
        # merge in any provided properties
        if instance_properties:
            meta = {**instance_properties, **meta}
        if nodepool_node_id:
            meta['nodepool_node_id'] = nodepool_node_id
        if nodepool_image_name:
            meta['nodepool_image_name'] = nodepool_image_name
        if nodepool_node_label:
            meta['nodepool_node_label'] = nodepool_node_label
        create_args['meta'] = meta

        try:
            return self._client.create_server(wait=False, **create_args)
        except openstack.exceptions.BadRequestException:
            # We've gotten a 400 error from nova - which means the request
            # was malformed. The most likely cause of that, unless something
            # became functionally and systemically broken, is stale az, image
            # or flavor cache. Log a message, invalidate the caches so that
            # next time we get new caches.
            self._images = {}
            self.__azs = None
            self.__flavors = {}  # TODO(gtema): caching
            self.log.info(
                "Clearing az, flavor and image caches due to 400 error "
                "from nova")
            raise

    def getServer(self, server_id):
        return self._client.get_server(server_id)

    def getServerConsole(self, server_id):
        try:
            return self._client.get_server_console(server_id)
        except openstack.exceptions.OpenStackCloudException:
            return None

    def waitForServer(self, server, timeout=3600, auto_ip=True):
        return self._client.wait_for_server(
            server=server, auto_ip=auto_ip,
            reuse=False, timeout=timeout)

    def waitForNodeCleanup(self, server_id, timeout=600):
        for count in iterate_timeout(
                timeout, exceptions.ServerDeleteException,
                "server %s deletion" % server_id):
            if not self.getServer(server_id):
                return

    def createImage(self, server, image_name, meta):
        return self._client.create_image_snapshot(
            image_name, server, **meta)

    def getImage(self, image_id):
        return self._client.get_image(image_id)

    def labelReady(self, label):
        if not label.cloud_image:
            return False

        # If an image ID was supplied, we'll assume it is ready since
        # we don't currently have a way of validating that (except during
        # server creation).
        if label.cloud_image.image_id:
            return True

        image = self.getImage(label.cloud_image.external_name)
        if not image:
            self.log.warning(
                "Provider %s is configured to use %s as the"
                " cloud-image for label %s and that"
                " cloud-image could not be found in the"
                " cloud." % (self.provider.name,
                             label.cloud_image.external_name,
                             label.name))
            return False
        return True

    def uploadImage(self, image_name, filename, image_type=None, meta=None,
                    md5=None, sha256=None):
        # configure glance and upload image.  Note the meta flags
        # are provided as custom glance properties
        # NOTE: we have wait=True set here. This is not how we normally
        # do things in nodepool, preferring to poll ourselves thankyouverymuch.
        # However - two things to note:
        #  - PUT has no aysnc mechanism, so we have to handle it anyway
        #  - v2 w/task waiting is very strange and complex - but we have to
        #              block for our v1 clouds anyway, so we might as well
        #              have the interface be the same and treat faking-out
        #              a openstacksdk-level fake-async interface later
        if not meta:
            meta = {}
        if image_type:
            meta['disk_format'] = image_type
        image = self._client.create_image(
            name=image_name,
            filename=filename,
            is_public=False,
            wait=True,
            md5=md5,
            sha256=sha256,
            **meta)
        return image.id

    def listPorts(self, status=None):
        '''
        List known ports.

        :param str status: A valid port status. E.g., 'ACTIVE' or 'DOWN'.
        '''
        if status:
            ports = self._client.list_ports(filters={'status': status})
        else:
            ports = self._client.list_ports()
        return ports

    def deletePort(self, port_id):
        self._client.delete_port(port_id)

    def listImages(self):
        return self._client.list_images()

    def listFlavors(self):
        return self._client.list_flavors(get_extra=False)

    def listFlavorsById(self):
        flavors = {}
        for flavor in self._client.list_flavors(get_extra=False):
            flavors[flavor.id] = flavor
        return flavors

    def listNodes(self):
        # list_servers carries the nodepool server list caching logic
        return self._client.list_servers()

    def deleteServer(self, server_id):
        return self._client.delete_server(server_id, delete_ips=True)

    def cleanupNode(self, server_id):
        server = self.getServer(server_id)
        if not server:
            raise exceptions.NotFound()

        self.log.debug('Deleting server %s' % server_id)
        self.deleteServer(server_id)

    def cleanupLeakedInstances(self):
        '''
        Delete any leaked server instances.

        Remove any servers found in this provider that are not recorded in
        the ZooKeeper data.
        '''

        deleting_nodes = {}

        for node in self._zk.nodeIterator():
            if node.state == zk.DELETING:
                if node.provider != self.provider.name:
                    continue
                if node.provider not in deleting_nodes:
                    deleting_nodes[node.provider] = []
                deleting_nodes[node.provider].append(node.external_id)

        for server in self.listNodes():
            meta = server.get('metadata', {})

            if 'nodepool_provider_name' not in meta:
                continue

            if meta['nodepool_provider_name'] != self.provider.name:
                # Another launcher, sharing this provider but configured
                # with a different name, owns this.
                continue

            if (self.provider.name in deleting_nodes and
                server.id in deleting_nodes[self.provider.name]):
                # Already deleting this node
                continue

            if not self._zk.getNode(meta['nodepool_node_id']):
                self.log.warning(
                    "Marking for delete leaked instance %s (%s) in %s "
                    "(unknown node id %s)",
                    server.name, server.id, self.provider.name,
                    meta['nodepool_node_id']
                )
                # Create an artifical node to use for deleting the server.
                node = zk.Node()
                node.external_id = server.id
                node.provider = self.provider.name
                node.state = zk.DELETING
                self._zk.storeNode(node)

    def filterComputePorts(self, ports):
        '''
        Return a list of compute ports (or no device owner).

        We are not interested in ports for routers or DHCP.
        '''
        ret = []
        for p in ports:
            if p.device_owner is None or p.device_owner.startswith("compute:"):
                ret.append(p)
        return ret

    def cleanupLeakedPorts(self):
        if not self._last_port_cleanup:
            self._last_port_cleanup = time.monotonic()
            ports = self.listPorts(status='DOWN')
            ports = self.filterComputePorts(ports)
            self._down_ports = set([p.id for p in ports])
            return

        # Return if not enough time has passed between cleanup
        last_check_in_secs = int(time.monotonic() - self._last_port_cleanup)
        if last_check_in_secs <= self._port_cleanup_interval_secs:
            return

        ports = self.listPorts(status='DOWN')
        ports = self.filterComputePorts(ports)
        current_set = set([p.id for p in ports])
        remove_set = current_set & self._down_ports

        removed_count = 0
        for port_id in remove_set:
            try:
                self.deletePort(port_id)
            except Exception:
                self.log.exception("Exception deleting port %s in %s:",
                                   port_id, self.provider.name)
            else:
                removed_count += 1
                self.log.debug("Removed DOWN port %s in %s",
                               port_id, self.provider.name)

        if self._statsd and removed_count:
            key = 'nodepool.provider.%s.downPorts' % (self.provider.name)
            self._statsd.incr(key, removed_count)

        self._last_port_cleanup = time.monotonic()

        # Rely on OpenStack to tell us the down ports rather than doing our
        # own set adjustment.
        ports = self.listPorts(status='DOWN')
        ports = self.filterComputePorts(ports)
        self._down_ports = set([p.id for p in ports])

    def cleanupLeakedResources(self):
        self.cleanupLeakedInstances()
        self.cleanupLeakedPorts()
        if self.provider.clean_floating_ips:
            self._client.delete_unattached_floating_ips()

    def getAZs(self):
        if self.__azs is None:
            self.__azs = self._client.list_availability_zone_names()
            if not self.__azs:
                # If there are no zones, return a list containing None so that
                # random.choice can pick None and pass that to Nova. If this
                # feels dirty, please direct your ire to policy.json and the
                # ability to turn off random portions of the OpenStack API.
                self.__azs = [None]
        return self.__azs
