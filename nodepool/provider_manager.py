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

import threading
import time
import requests.exceptions
import sys

import shade
import novaclient

from nodeutils import iterate_timeout
from task_manager import Task, TaskManager, ManagerStoppedException


SERVER_LIST_AGE = 5   # How long to keep a cached copy of the server list
IPS_LIST_AGE = 5      # How long to keep a cached copy of the ip list


def get_public_ip(server, version=4):
    for addr in server.addresses.get('public', []):
        if type(addr) == type(u''):  # Rackspace/openstack 1.0
            return addr
        if addr['version'] == version:  # Rackspace/openstack 1.1
            return addr['addr']
    for addr in server.addresses.get('private', []):
        # HPcloud
        if (addr['version'] == version and version == 4):
            quad = map(int, addr['addr'].split('.'))
            if quad[0] == 10:
                continue
            if quad[0] == 192 and quad[1] == 168:
                continue
            if quad[0] == 172 and (16 <= quad[1] <= 31):
                continue
            return addr['addr']
    for addr in server.addresses.get('Ext-Net', []):
        if addr['version'] == version:  # OVH
            return addr['addr']
    return None


def get_private_ip(server):
    ret = []
    for (name, network) in server.addresses.iteritems():
        if name == 'private':
            ret.extend([addrs['addr']
                        for addrs in network if addrs['version'] == 4])
        else:
            for interface_spec in network:
                if interface_spec['version'] != 4:
                    continue
                if ('OS-EXT-IPS:type' in interface_spec
                        and interface_spec['OS-EXT-IPS:type'] == 'fixed'):
                    ret.append(interface_spec['addr'])
    if not ret:
        if server.status == 'ACTIVE':
            # Server expected to have at least one address in ACTIVE status
            # TODO: uncomment this code when all nodes have private IPs
            # raise KeyError('No private ip found for server')
            return None
        else:
            return None
    return ret[0]


def make_server_dict(server):
    d = dict(id=str(server.id),
             name=server.name,
             status=server.status,
             addresses=server.addresses)
    if hasattr(server, 'adminPass'):
        d['admin_pass'] = server.adminPass
    if hasattr(server, 'key_name'):
        d['key_name'] = server.key_name
    if hasattr(server, 'progress'):
        d['progress'] = server.progress
    if hasattr(server, 'metadata'):
        d['metadata'] = server.metadata
    d['public_v4'] = get_public_ip(server)
    d['private_v4'] = get_private_ip(server)
    d['public_v6'] = get_public_ip(server, version=6)
    return d


def make_image_dict(image):
    d = dict(id=str(image.id), name=image.name, status=image.status,
             metadata=image.metadata)
    if hasattr(image, 'progress'):
        d['progress'] = image.progress
    return d


class NotFound(Exception):
    pass


class CreateServerTask(Task):
    def main(self, client):
        server = client.nova_client.servers.create(**self.args)
        return str(server.id)


class GetServerTask(Task):
    def main(self, client):
        try:
            server = client.nova_client.servers.get(self.args['server_id'])
        except novaclient.exceptions.NotFound:
            raise NotFound()
        return make_server_dict(server)


class DeleteServerTask(Task):
    def main(self, client):
        client.nova_client.servers.delete(self.args['server_id'])


class ListServersTask(Task):
    def main(self, client):
        servers = client.nova_client.servers.list()
        return [make_server_dict(server) for server in servers]


class AddKeypairTask(Task):
    def main(self, client):
        client.nova_client.keypairs.create(**self.args)


class ListKeypairsTask(Task):
    def main(self, client):
        keys = client.nova_client.keypairs.list()
        return [dict(id=str(key.id), name=key.name) for
                key in keys]


class DeleteKeypairTask(Task):
    def main(self, client):
        client.nova_client.keypairs.delete(self.args['name'])


class CreateFloatingIPTask(Task):
    def main(self, client):
        ip = client.nova_client.floating_ips.create(**self.args)
        return dict(id=str(ip.id), ip=ip.ip)


class AddFloatingIPTask(Task):
    def main(self, client):
        client.nova_client.servers.add_floating_ip(**self.args)


class GetFloatingIPTask(Task):
    def main(self, client):
        ip = client.nova_client.floating_ips.get(self.args['ip_id'])
        return dict(id=str(ip.id), ip=ip.ip, instance_id=str(ip.instance_id))


class ListFloatingIPsTask(Task):
    def main(self, client):
        ips = client.nova_client.floating_ips.list()
        return [dict(id=str(ip.id), ip=ip.ip,
                     instance_id=str(ip.instance_id)) for
                ip in ips]


class RemoveFloatingIPTask(Task):
    def main(self, client):
        client.nova_client.servers.remove_floating_ip(**self.args)


class DeleteFloatingIPTask(Task):
    def main(self, client):
        client.nova_client.floating_ips.delete(self.args['ip_id'])


class CreateImageTask(Task):
    def main(self, client):
        # This returns an id
        return str(client.nova_client.servers.create_image(**self.args))


class GetImageTask(Task):
    def main(self, client):
        try:
            image = client.nova_client.images.get(**self.args)
        except novaclient.exceptions.NotFound:
            raise NotFound()
        # HP returns 404, rackspace can return a 'DELETED' image.
        if image.status == 'DELETED':
            raise NotFound()
        return make_image_dict(image)


class ListExtensionsTask(Task):
    def main(self, client):
        try:
            resp, body = client.nova_client.client.get('/extensions')
            return [x['alias'] for x in body['extensions']]
        except novaclient.exceptions.NotFound:
            # No extensions present.
            return []


class ListFlavorsTask(Task):
    def main(self, client):
        flavors = client.nova_client.flavors.list()
        return [dict(id=str(flavor.id), ram=flavor.ram, name=flavor.name)
                for flavor in flavors]


class ListImagesTask(Task):
    def main(self, client):
        images = client.nova_client.images.list()
        return [make_image_dict(image) for image in images]


class FindImageTask(Task):
    def main(self, client):
        image = client.nova_client.images.find(**self.args)
        return dict(id=str(image.id))


class DeleteImageTask(Task):
    def main(self, client):
        client.nova_client.images.delete(**self.args)


class FindNetworkTask(Task):
    def main(self, client):
        for network in client.neutron_client.list_networks()['networks']:
            if self.args['label'] == network['name']:
                return dict(id=str(network['id']))


class ProviderManager(TaskManager):
    log = logging.getLogger("nodepool.ProviderManager")

    def __init__(self, provider):
        super(ProviderManager, self).__init__(None, provider.name,
                                              provider.rate)
        self.provider = provider
        self.resetClient()
        self._images = {}
        self._networks = {}
        self._cloud_metadata_read = False
        self.__flavors = {}
        self.__extensions = {}
        self._servers = []
        self._servers_time = 0
        self._servers_lock = threading.Lock()
        self._ips = []
        self._ips_time = 0
        self._ips_lock = threading.Lock()

    @property
    def _flavors(self):
        if not self._cloud_metadata_read:
            self._getCloudMetadata()
        return self.__flavors

    @property
    def _extensions(self):
        if not self._cloud_metadata_read:
            self._getCloudMetadata()
        return self.__extensions

    def _getCloudMetadata(self):
        self.__flavors = self._getFlavors()
        self.__extensions = self.listExtensions()
        self._cloud_metadata_read = True

    def _getClient(self):
        return shade.OpenStackCloud(
            cloud_config=self.provider.cloud_config,
            **self.provider.cloud_config.config)

    def runTask(self, task):
        try:
            task.run(self._client)
        except requests.exceptions.ProxyError:
            # Try to get a new client object if we get a ProxyError
            self.log.exception('Resetting client due to ProxyError')
            self.resetClient()
            try:
                task.run(self._client)
            except requests.exceptions.ProxyError as e:
                # If we get a second ProxyError, then make sure it gets raised
                # the same way all other Exceptions from the Task object do.
                # This will move the Exception to the main thread.
                task.exception(e, sys.exc_info()[2])

    def resetClient(self):
        self._client = self._getClient()

    def _getFlavors(self):
        flavors = self.listFlavors()
        flavors.sort(lambda a, b: cmp(a['ram'], b['ram']))
        return flavors

    def hasExtension(self, extension):
        # Note: this will throw an error if the provider is offline
        # but all the callers are in threads so the mainloop won't be affected.
        if extension in self._extensions:
            return True
        return False

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
        image = self.submitTask(FindImageTask(name=name))
        self._images[name] = image
        return image

    def findNetwork(self, label):
        if label in self._networks:
            return self._networks[label]
        network = self.submitTask(FindNetworkTask(label=label))
        self._networks[label] = network
        return network

    def deleteImage(self, name):
        if name in self._images:
            del self._images[name]
        return self.submitTask(DeleteImageTask(image=name))

    def addKeypair(self, name):
        key = paramiko.RSAKey.generate(2048)
        public_key = key.get_name() + ' ' + key.get_base64()
        self.submitTask(AddKeypairTask(name=name, public_key=public_key))
        return key

    def listKeypairs(self):
        return self.submitTask(ListKeypairsTask())

    def deleteKeypair(self, name):
        return self.submitTask(DeleteKeypairTask(name=name))

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
        if self.provider.use_neutron:
            nics = []
            for network in self.provider.networks:
                if 'net-id' in network:
                    nics.append({'net-id': network['net-id']})
                elif 'net-label' in network:
                    net_id = self.findNetwork(network['net-label'])['id']
                    nics.append({'net-id': net_id})
                else:
                    raise Exception("Invalid 'networks' configuration.")
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

        return self.submitTask(CreateServerTask(**create_args))

    def getServer(self, server_id):
        return self.submitTask(GetServerTask(server_id=server_id))

    def getFloatingIP(self, ip_id):
        return self.submitTask(GetFloatingIPTask(ip_id=ip_id))

    def getServerFromList(self, server_id):
        for s in self.listServers():
            if s['id'] == server_id:
                return s
        raise NotFound()

    def _waitForResource(self, resource_type, resource_id, timeout):
        last_status = None
        for count in iterate_timeout(timeout,
                                     "%s %s in %s" % (resource_type,
                                                      resource_id,
                                                      self.provider.name)):
            try:
                if resource_type == 'server':
                    resource = self.getServerFromList(resource_id)
                elif resource_type == 'image':
                    resource = self.getImage(resource_id)
            except NotFound:
                continue
            except ManagerStoppedException:
                raise
            except Exception:
                self.log.exception('Unable to list %ss while waiting for '
                                   '%s will retry' % (resource_type,
                                                      resource_id))
                continue

            status = resource.get('status')
            if (last_status != status):
                self.log.debug(
                    'Status of {type} in {provider} {id}: {status}'.format(
                        type=resource_type,
                        provider=self.provider.name,
                        id=resource_id,
                        status=status))
            last_status = status
            if status in ['ACTIVE', 'ERROR']:
                return resource

    def waitForServer(self, server_id, timeout=3600):
        return self._waitForResource('server', server_id, timeout)

    def waitForServerDeletion(self, server_id, timeout=600):
        for count in iterate_timeout(600, "server %s deletion in %s" %
                                     (server_id, self.provider.name)):
            try:
                self.getServerFromList(server_id)
            except NotFound:
                return

    def waitForImage(self, image_id, timeout=3600):
        # TODO(mordred): This should just be handled by the Fake, but we're
        #                not quite plumbed through for that yet
        if image_id == 'fake-glance-id':
            return True
        return self._waitForResource('image', image_id, timeout)

    def createFloatingIP(self, pool=None):
        return self.submitTask(CreateFloatingIPTask(pool=pool))

    def addFloatingIP(self, server_id, address):
        self.submitTask(AddFloatingIPTask(server=server_id,
                                          address=address))

    def addPublicIP(self, server_id, pool=None):
        ip = self.createFloatingIP(pool)
        try:
            self.addFloatingIP(server_id, ip['ip'])
        except novaclient.exceptions.ClientException:
            # Delete the floating IP here as cleanupServer will not
            # have access to the ip -> server mapping preventing it
            # from removing this IP.
            self.deleteFloatingIP(ip['id'])
            raise
        for count in iterate_timeout(600, "ip to be added to %s in %s" %
                                     (server_id, self.provider.name)):
            try:
                newip = self.getFloatingIP(ip['id'])
            except ManagerStoppedException:
                raise
            except Exception:
                self.log.exception('Unable to get IP details for server %s, '
                                   'will retry' % (server_id))
                continue
            if newip['instance_id'] == server_id:
                return newip['ip']

    def createImage(self, server_id, image_name, meta):
        return self.submitTask(CreateImageTask(server=server_id,
                                               image_name=image_name,
                                               metadata=meta))

    def getImage(self, image_id):
        return self.submitTask(GetImageTask(image=image_id))

    def uploadImage(self, image_name, filename, disk_format, container_format,
                    meta):
        # configure glance and upload image.  Note the meta flags
        # are provided as custom glance properties
        # NOTE: we have wait=True set here. This is not how we normally
        # do things in nodepool, preferring to poll ourselves thankyouverymuch.
        # However - two things to note:
        #  - glance v1 has no aysnc mechanism, so we have to handle it anyway
        #  - glance v2 waiting is very strange and complex - but we have to
        #              block for our v1 clouds anyway, so we might as well
        #              have the interface be the same and treat faking-out
        #              a shade-level fake-async interface later
        image = self._client.create_image(
            name=image_name,
            filename='%s.%s' % (filename, disk_format),
            is_public=False,
            disk_format=disk_format,
            container_format=container_format,
            wait=True,
            **meta)
        return image.id

    def listExtensions(self):
        return self.submitTask(ListExtensionsTask())

    def listImages(self):
        return self.submitTask(ListImagesTask())

    def listFlavors(self):
        return self.submitTask(ListFlavorsTask())

    def listFloatingIPs(self):
        if time.time() - self._ips_time >= IPS_LIST_AGE:
            if self._ips_lock.acquire(False):
                try:
                    self._ips = self.submitTask(ListFloatingIPsTask())
                    self._ips_time = time.time()
                finally:
                    self._ips_lock.release()
        return self._ips

    def removeFloatingIP(self, server_id, address):
        return self.submitTask(RemoveFloatingIPTask(server=server_id,
                                                    address=address))

    def deleteFloatingIP(self, ip_id):
        return self.submitTask(DeleteFloatingIPTask(ip_id=ip_id))

    def listServers(self, cache=True):
        if (not cache or
            time.time() - self._servers_time >= SERVER_LIST_AGE):
            # Since we're using cached data anyway, we don't need to
            # have more than one thread actually submit the list
            # servers task.  Let the first one submit it while holding
            # a lock, and the non-blocking acquire method will cause
            # subsequent threads to just skip this and use the old
            # data until it succeeds.
            if self._servers_lock.acquire(False):
                try:
                    self._servers = self.submitTask(ListServersTask())
                    self._servers_time = time.time()
                finally:
                    self._servers_lock.release()
        return self._servers

    def deleteServer(self, server_id):
        return self.submitTask(DeleteServerTask(server_id=server_id))

    def cleanupServer(self, server_id):
        done = False
        while not done:
            try:
                server = self.getServerFromList(server_id)
                done = True
            except NotFound:
                # If we have old data, that's fine, it should only
                # indicate that a server exists when it doesn't; we'll
                # recover from that.  However, if we have no data at
                # all, wait until the first server list task
                # completes.
                if self._servers_time == 0:
                    time.sleep(SERVER_LIST_AGE + 1)
                else:
                    done = True

        # This will either get the server or raise an exception
        server = self.getServerFromList(server_id)

        if self.hasExtension('os-floating-ips'):
            for ip in self.listFloatingIPs():
                if ip['instance_id'] == server_id:
                    self.log.debug('Deleting floating ip for server %s' %
                                   server_id)
                    self.deleteFloatingIP(ip['id'])

        if (self.hasExtension('os-keypairs') and
                server['key_name'] != self.provider.keypair):
            for kp in self.listKeypairs():
                if kp['name'] == server['key_name']:
                    self.log.debug('Deleting keypair for server %s' %
                                   server_id)
                    self.deleteKeypair(kp['name'])

        self.log.debug('Deleting server %s' % server_id)
        self.deleteServer(server_id)
