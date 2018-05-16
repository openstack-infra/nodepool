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
import math
import operator
import time

import shade

from nodepool import exceptions
from nodepool.driver import Provider
from nodepool.nodeutils import iterate_timeout
from nodepool.task_manager import ManagerStoppedException
from nodepool.task_manager import TaskManager
from nodepool import version


IPS_LIST_AGE = 5      # How long to keep a cached copy of the ip list
MAX_QUOTA_AGE = 5 * 60  # How long to keep the quota information cached


class QuotaInformation:

    def __init__(self, cores=None, instances=None, ram=None, default=0):
        '''
        Initializes the quota information with some values. None values will
        be initialized with default which will be typically 0 or math.inf
        indicating an infinite limit.

        :param cores:
        :param instances:
        :param ram:
        :param default:
        '''
        self.quota = {
            'compute': {
                'cores': self._get_default(cores, default),
                'instances': self._get_default(instances, default),
                'ram': self._get_default(ram, default),
            }
        }

    @staticmethod
    def construct_from_flavor(flavor):
        return QuotaInformation(instances=1,
                                cores=flavor.vcpus,
                                ram=flavor.ram)

    @staticmethod
    def construct_from_limits(limits):
        def bound_value(value):
            if value == -1:
                return math.inf
            return value

        return QuotaInformation(
            instances=bound_value(limits.max_total_instances),
            cores=bound_value(limits.max_total_cores),
            ram=bound_value(limits.max_total_ram_size))

    def _get_default(self, value, default):
        return value if value is not None else default

    def _add_subtract(self, other, add=True):
        for category in self.quota.keys():
            for resource in self.quota[category].keys():
                second_value = other.quota.get(category, {}).get(resource, 0)
                if add:
                    self.quota[category][resource] += second_value
                else:
                    self.quota[category][resource] -= second_value

    def subtract(self, other):
        self._add_subtract(other, add=False)

    def add(self, other):
        self._add_subtract(other, True)

    def non_negative(self):
        for key_i, category in self.quota.items():
            for resource, value in category.items():
                if value < 0:
                    return False
        return True

    def __str__(self):
        return str(self.quota)


class OpenStackProvider(Provider):
    log = logging.getLogger("nodepool.driver.openstack.OpenStackProvider")

    def __init__(self, provider, use_taskmanager):
        self.provider = provider
        self._images = {}
        self._networks = {}
        self.__flavors = {}
        self.__azs = None
        self._use_taskmanager = use_taskmanager
        self._taskmanager = None
        self._current_nodepool_quota = None

    def start(self):
        if self._use_taskmanager:
            self._taskmanager = TaskManager(None, self.provider.name,
                                            self.provider.rate)
            self._taskmanager.start()
        self.resetClient()

    def stop(self):
        if self._taskmanager:
            self._taskmanager.stop()

    def join(self):
        if self._taskmanager:
            self._taskmanager.join()

    @property
    def _flavors(self):
        if not self.__flavors:
            self.__flavors = self._getFlavors()
        return self.__flavors

    def _getClient(self):
        if self._use_taskmanager:
            manager = self._taskmanager
        else:
            manager = None
        return shade.OpenStackCloud(
            cloud_config=self.provider.cloud_config,
            manager=manager,
            app_name='nodepool',
            app_version=version.version_info.version_string(),
            **self.provider.cloud_config.config)

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

    def estimatedNodepoolQuotaUsed(self, zk, pool=None):
        '''
        Sums up the quota used (or planned) currently by nodepool. If pool is
        given it is filtered by the pool.

        :param zk: the object to access zookeeper
        :param pool: If given, filtered by the pool.
        :return: Calculated quota in use by nodepool
        '''
        used_quota = QuotaInformation()

        for node in zk.nodeIterator():
            if node.provider == self.provider.name:
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
                node_resources = self.quotaNeededByNodeType(
                    node.type[0], provider_pool)
                used_quota.add(node_resources)
        return used_quota

    def unmanagedQuotaUsed(self):
        '''
        Sums up the quota used by servers unmanaged by nodepool.

        :return: Calculated quota in use by unmanaged servers
        '''
        flavors = self.listFlavorsById()
        used_quota = QuotaInformation()

        for server in self.listNodes():
            meta = server.get('metadata', {})

            nodepool_provider_name = meta.get('nodepool_provider_name')
            if nodepool_provider_name and \
                    nodepool_provider_name == self.provider.name:
                # This provider (regardless of the launcher) owns this server
                # so it must not be accounted for unmanaged quota.
                continue

            flavor = flavors.get(server.flavor.id)
            used_quota.add(QuotaInformation.construct_from_flavor(flavor))

        return used_quota

    def resetClient(self):
        self._client = self._getClient()
        if self._use_taskmanager:
            self._taskmanager.setClient(self._client)

    def _getFlavors(self):
        flavors = self.listFlavors()
        flavors.sort(key=operator.itemgetter('ram'))
        return flavors

    # TODO(mordred): These next three methods duplicate logic that is in
    #                shade, but we can't defer to shade until we're happy
    #                with using shade's resource caching facility. We have
    #                not yet proven that to our satisfaction, but if/when
    #                we do, these should be able to go away.
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
                     networks=None, boot_from_volume=False, volume_size=50):
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
        if nodepool_node_id:
            meta['nodepool_node_id'] = nodepool_node_id
        if nodepool_image_name:
            meta['nodepool_image_name'] = nodepool_image_name
        if nodepool_node_label:
            meta['nodepool_node_label'] = nodepool_node_label
        create_args['meta'] = meta

        try:
            return self._client.create_server(wait=False, **create_args)
        except shade.OpenStackCloudBadRequest:
            # We've gotten a 400 error from nova - which means the request
            # was malformed. The most likely cause of that, unless something
            # became functionally and systemically broken, is stale image
            # or flavor cache. Log a message, invalidate the caches so that
            # next time we get new caches.
            self._images = {}
            self.__flavors = {}
            self.log.info(
                "Clearing flavor and image caches due to 400 error from nova")
            raise

    def getServer(self, server_id):
        return self._client.get_server(server_id)

    def getServerConsole(self, server_id):
        try:
            return self._client.get_server_console(server_id)
        except shade.OpenStackCloudException:
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

    def waitForImage(self, image_id, timeout=3600):
        last_status = None
        for count in iterate_timeout(
                timeout, exceptions.ImageCreateException, "image creation"):
            try:
                image = self.getImage(image_id)
            except exceptions.NotFound:
                continue
            except ManagerStoppedException:
                raise
            except Exception:
                self.log.exception('Unable to list images while waiting for '
                                   '%s will retry' % (image_id))
                continue

            # shade returns None when not found
            if not image:
                continue

            status = image['status']
            if (last_status != status):
                self.log.debug(
                    'Status of image in {provider} {id}: {status}'.format(
                        provider=self.provider.name,
                        id=image_id,
                        status=status))
                if status == 'ERROR' and 'fault' in image:
                    self.log.debug(
                        'ERROR in {provider} on {id}: {resason}'.format(
                            provider=self.provider.name,
                            id=image_id,
                            resason=image['fault']['message']))
            last_status = status
            # Glance client returns lower case statuses - but let's be sure
            if status.lower() in ['active', 'error']:
                return image

    def createImage(self, server, image_name, meta):
        return self._client.create_image_snapshot(
            image_name, server, **meta)

    def getImage(self, image_id):
        return self._client.get_image(image_id)

    def labelReady(self, label):
        if not label.cloud_image:
            return False
        image = self.getImage(label.cloud_image.external)
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
        #              a shade-level fake-async interface later
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

    def cleanupLeakedResources(self):
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
