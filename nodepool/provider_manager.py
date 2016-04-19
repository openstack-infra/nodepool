#!/usr/bin/env python

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

import json
import logging
import paramiko
from contextlib import contextmanager

import shade

import exceptions
import fakeprovider
from nodeutils import iterate_timeout
from task_manager import TaskManager, ManagerStoppedException


IPS_LIST_AGE = 5      # How long to keep a cached copy of the ip list


def make_image_dict(image):
    d = dict(id=str(image.id), name=image.name, status=image.status,
             metadata=image.metadata)
    if hasattr(image, 'progress'):
        d['progress'] = image.progress
    return d


@contextmanager
def shade_inner_exceptions():
    try:
        yield
    except shade.OpenStackCloudException as e:
        e.log_error()
        raise


class NotFound(Exception):
    pass


def get_provider_manager(provider):
    if (provider.cloud_config.get_auth_args().get('auth_url') == 'fake'):
        return FakeProviderManager(provider)
    else:
        return ProviderManager(provider)


class ProviderManager(TaskManager):
    log = logging.getLogger("nodepool.ProviderManager")

    @staticmethod
    def reconfigure(old_config, new_config):
        stop_managers = []
        for p in new_config.providers.values():
            oldmanager = None
            if old_config:
                oldmanager = old_config.provider_managers.get(p.name)
            if oldmanager and p != oldmanager.provider:
                stop_managers.append(oldmanager)
                oldmanager = None
            if oldmanager:
                new_config.provider_managers[p.name] = oldmanager
            else:
                ProviderManager.log.debug("Creating new ProviderManager object"
                                          " for %s" % p.name)
                new_config.provider_managers[p.name] = \
                    get_provider_manager(p)
                new_config.provider_managers[p.name].start()

        for stop_manager in stop_managers:
            stop_manager.stop()

    @staticmethod
    def stopProviders(config):
        for m in config.provider_managers.values():
            m.stop()
            m.join()

    def __init__(self, provider):
        super(ProviderManager, self).__init__(None, provider.name,
                                              provider.rate)
        self.provider = provider
        self.resetClient()
        self._images = {}
        self._networks = {}
        self.__flavors = {}

    @property
    def _flavors(self):
        if not self.__flavors:
            self.__flavors = self._getFlavors()
        return self.__flavors

    def _getClient(self):
        return shade.OpenStackCloud(
            cloud_config=self.provider.cloud_config,
            manager=self,
            **self.provider.cloud_config.config)

    def runTask(self, task):
        # Run the given task in the TaskManager passed to shade. It turns
        # out that this provider manager is the TaskManager we pass, so
        # this is a way of running each cloud operation in its own thread
        task.run(self._client)

    def resetClient(self):
        self._client = self._getClient()

    def _getFlavors(self):
        flavors = self.listFlavors()
        flavors.sort(lambda a, b: cmp(a['ram'], b['ram']))
        return flavors

    def findFlavor(self, min_ram, name_filter=None):
        # Note: this will throw an error if the provider is offline
        # but all the callers are in threads (they call in via CreateServer) so
        # the mainloop won't be affected.
        for f in self._flavors:
            if (f['ram'] >= min_ram
                    and (not name_filter or name_filter in f['name'])):
                return f
        raise Exception("Unable to find flavor with min ram: %s" % min_ram)

    def findImage(self, name):
        if name in self._images:
            return self._images[name]

        with shade_inner_exceptions():
            image = self._client.get_image(name)
        self._images[name] = image
        return image

    def findNetwork(self, name):
        if name in self._networks:
            return self._networks[name]

        with shade_inner_exceptions():
            network = self._client.get_network(name)
        self._networks[name] = network
        return network

    def deleteImage(self, name):
        if name in self._images:
            del self._images[name]

        with shade_inner_exceptions():
            return self._client.delete_image(name)

    def addKeypair(self, name):
        key = paramiko.RSAKey.generate(2048)
        public_key = key.get_name() + ' ' + key.get_base64()
        with shade_inner_exceptions():
            self._client.create_keypair(name=name, public_key=public_key)
        return key

    def listKeypairs(self):
        with shade_inner_exceptions():
            keypairs = self._client.list_keypairs()
        return keypairs

    def deleteKeypair(self, name):
        with shade_inner_exceptions():
            return self._client.delete_keypair(name=name)

    def createServer(self, name, min_ram, image_id=None, image_name=None,
                     az=None, key_name=None, name_filter=None,
                     config_drive=None, nodepool_node_id=None,
                     nodepool_image_name=None,
                     nodepool_snapshot_image_id=None):
        if image_name:
            image_id = self.findImage(image_name)['id']
        flavor = self.findFlavor(min_ram, name_filter)
        create_args = dict(name=name, image=image_id, flavor=flavor['id'],
                           config_drive=config_drive)
        if key_name:
            create_args['key_name'] = key_name
        if az:
            create_args['availability_zone'] = az
        nics = []
        for network in self.provider.networks:
            if network.id:
                nics.append({'net-id': network.id})
            elif network.name:
                net_id = self.findNetwork(network.name)['id']
                nics.append({'net-id': net_id})
            else:
                raise Exception("Invalid 'networks' configuration.")
        if nics:
            create_args['nics'] = nics
        # Put provider.name and image_name in as groups so that ansible
        # inventory can auto-create groups for us based on each of those
        # qualities
        # Also list each of those values directly so that non-ansible
        # consumption programs don't need to play a game of knowing that
        # groups[0] is the image name or anything silly like that.
        nodepool_meta = dict(provider_name=self.provider.name)
        groups_meta = [self.provider.name]
        if nodepool_node_id:
            nodepool_meta['node_id'] = nodepool_node_id
        if nodepool_snapshot_image_id:
            nodepool_meta['snapshot_image_id'] = nodepool_snapshot_image_id
        if nodepool_image_name:
            nodepool_meta['image_name'] = nodepool_image_name
            groups_meta.append(nodepool_image_name)
        create_args['meta'] = dict(
            groups=json.dumps(groups_meta),
            nodepool=json.dumps(nodepool_meta)
        )

        with shade_inner_exceptions():
            return self._client.create_server(wait=False, **create_args)

    def getServer(self, server_id):
        with shade_inner_exceptions():
            return self._client.get_server(server_id)

    def waitForServer(self, server, timeout=3600):
        with shade_inner_exceptions():
            return self._client.wait_for_server(
                server=server, auto_ip=True, reuse=False,
                timeout=timeout)

    def waitForServerDeletion(self, server_id, timeout=600):
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
            except NotFound:
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
        with shade_inner_exceptions():
            return self._client.create_image_snapshot(
                image_name, server, **meta)

    def getImage(self, image_id):
        with shade_inner_exceptions():
            return self._client.get_image(image_id)

    def uploadImage(self, image_name, filename, image_type=None, meta=None):
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
        with shade_inner_exceptions():
            image = self._client.create_image(
                name=image_name,
                filename=filename,
                is_public=False,
                wait=True,
                **meta)
        return image.id

    def listImages(self):
        with shade_inner_exceptions():
            return self._client.list_images()

    def listFlavors(self):
        with shade_inner_exceptions():
            return self._client.list_flavors(get_extra=False)

    def listServers(self):
        # shade list_servers carries the nodepool server list caching logic
        with shade_inner_exceptions():
            return self._client.list_servers()

    def deleteServer(self, server_id):
        with shade_inner_exceptions():
            return self._client.delete_server(server_id, delete_ips=True)

    def cleanupServer(self, server_id):
        server = self.getServer(server_id)
        if not server:
            raise NotFound()

        key_name = server.get('key_name')
        if key_name and key_name != self.provider.keypair:
            with shade_inner_exceptions():
                self._client.delete_keypair(name=server['key_name'])

        self.log.debug('Deleting server %s' % server_id)
        self.deleteServer(server_id)

    def cleanupLeakedFloaters(self):
        with shade_inner_exceptions():
            self._client.delete_unattached_floating_ips()


class FakeProviderManager(ProviderManager):
    def __init__(self, provider):
        self.__client = fakeprovider.FakeOpenStackCloud()
        super(FakeProviderManager, self).__init__(provider)

    def _getClient(self):
        return self.__client
