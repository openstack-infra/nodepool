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

import novaclient.client
import time
import paramiko
import socket
import logging
from sshclient import SSHClient

import nodedb

log = logging.getLogger("nodepool.utils")


def iterate_timeout(max_seconds, purpose):
    start = time.time()
    count = 0
    while (time.time() < start + max_seconds):
        count += 1
        yield count
        time.sleep(2)
    raise Exception("Timeout waiting for %s" % purpose)


def get_client(provider):
    args = ['1.1', provider.username, provider.password,
            provider.project_id, provider.auth_url]
    kwargs = {}
    if provider.service_type:
        kwargs['service_type'] = provider.service_type
    if provider.service_name:
        kwargs['service_name'] = provider.service_name
    if provider.region_name:
        kwargs['region_name'] = provider.region_name
    client = novaclient.client.Client(*args, **kwargs)
    return client

extension_cache = {}


def get_extensions(client):
    global extension_cache
    cache = extension_cache.get(client)
    if cache:
        return cache
    try:
        resp, body = client.client.get('/extensions')
        extensions = [x['alias'] for x in body['extensions']]
    except novaclient.exceptions.NotFound:
        extensions = []
    extension_cache[client] = extensions
    return extensions


def get_flavor(client, min_ram):
    flavors = [f for f in client.flavors.list() if f.ram >= min_ram]
    flavors.sort(lambda a, b: cmp(a.ram, b.ram))
    return flavors[0]


def get_public_ip(server, version=4):
    if 'os-floating-ips' in get_extensions(server.manager.api):
        log.debug('Server %s using floating ips' % server.id)
        for addr in server.manager.api.floating_ips.list():
            if addr.instance_id == server.id:
                return addr.ip
    log.debug('Server %s not using floating ips, addresses: %s' %
              (server.id, server.addresses))
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
    return None


def add_public_ip(server):
    ip = server.manager.api.floating_ips.create()
    log.debug('Created floading IP %s for server %s' % (ip, server.id))
    server.add_floating_ip(ip)
    for count in iterate_timeout(600, "ip to be added"):
        try:
            newip = ip.manager.get(ip.id)
        except Exception:
            log.exception('Unable to get IP details for server %s, '
                          'will retry' % (server.id))
            continue

        if newip.instance_id == server.id:
            return


def add_keypair(client, name):
    key = paramiko.RSAKey.generate(2048)
    public_key = key.get_name() + ' ' + key.get_base64()
    kp = client.keypairs.create(name, public_key)
    return key, kp


def wait_for_resource(wait_resource, timeout=3600):
    last_progress = None
    last_status = None
    for count in iterate_timeout(timeout, "waiting for %s" % wait_resource):
        try:
            resource = wait_resource.manager.get(wait_resource.id)
        except:
            log.exception('Unable to list resources, while waiting for %s '
                          'will retry' % wait_resource)
            continue

        # In Rackspace v1.0, there is no progress attribute while queued
        if hasattr(resource, 'progress'):
            if (last_progress != resource.progress
                    or last_status != resource.status):
                log.debug('Status of %s: %s %s' % (resource, resource.status,
                                                   resource.progress))
            last_progress = resource.progress
        elif last_status != resource.status:
            log.debug('Status of %s: %s' % (resource, resource.status))
        last_status = resource.status
        if resource.status == 'ACTIVE':
            return resource


def ssh_connect(ip, username, connect_kwargs={}, timeout=60):
    # HPcloud may return errno 111 for about 30 seconds after adding the IP
    for count in iterate_timeout(timeout, "ssh access"):
        try:
            client = SSHClient(ip, username, **connect_kwargs)
            break
        except socket.error, e:
            if e[0] != 111:
                log.exception('Exception while testing ssh access:')

    out = client.ssh("test ssh access", "echo access okay")
    if "access okay" in out:
        return client
    return None


def create_server(client, hostname, base_image, flavor, add_key=False):
    create_kwargs = dict(image=base_image, flavor=flavor, name=hostname)

    key = None
    # hpcloud can't handle dots in keypair names
    if add_key:
        key_name = hostname.split('.')[0]
        if 'os-keypairs' in get_extensions(client):
            key, kp = add_keypair(client, key_name)
            create_kwargs['key_name'] = key_name

    server = client.servers.create(**create_kwargs)
    return server, key


def create_image(client, server, image_name):
    # TODO: fix novaclient so it returns an image here
    # image = server.create_image(name)
    uuid = server.manager.create_image(server, image_name)
    image = client.images.get(uuid)
    return image


def delete_server(client, server):
    try:
        if 'os-floating-ips' in get_extensions(server.manager.api):
            for addr in server.manager.api.floating_ips.list():
                if addr.instance_id == server.id:
                    server.remove_floating_ip(addr)
                    addr.delete()
    except:
        log.exception('Unable to remove floating IP from server %s' %
                      server.id)

    try:
        if 'os-keypairs' in get_extensions(server.manager.api):
            for kp in server.manager.api.keypairs.list():
                if kp.name == server.key_name:
                    kp.delete()
    except:
        log.exception('Unable to delete keypair from server %s' % server.id)

    log.debug('Deleting server %s' % server.id)
    server.delete()
    for count in iterate_timeout(3600, "waiting for server %s deletion" %
                                 server.id):
        try:
            client.servers.get(server.id)
        except novaclient.exceptions.NotFound:
            return


def delete_node(client, node):
    node.state = nodedb.DELETE
    if node.external_id:
        try:
            server = client.servers.get(node.external_id)
            delete_server(client, server)
        except novaclient.exceptions.NotFound:
            pass

    node.delete()


def delete_image(client, image):
    server = None
    if image.server_external_id:
        try:
            server = client.servers.get(image.server_external_id)
        except novaclient.exceptions.NotFound:
            log.warning('Image server id %s not found' %
                        image.server_external_id)

    if server:
        delete_server(client, server)

    remote_image = None
    if image.external_id:
        try:
            remote_image = client.images.get(image.external_id)
        except novaclient.exceptions.NotFound:
            log.warning('Image id %s not found' % image.external_id)

    if remote_image:
        log.debug('Deleting image %s' % remote_image.id)
        remote_image.delete()

    image.delete()
