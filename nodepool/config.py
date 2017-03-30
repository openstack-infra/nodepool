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

import os_client_config
from six.moves import configparser as ConfigParser
import time
import yaml

import fakeprovider
import zk


class ConfigValue(object):
    def __eq__(self, other):
        if isinstance(other, ConfigValue):
            if other.__dict__ == self.__dict__:
                return True
        return False


class Config(ConfigValue):
    pass


class Provider(ConfigValue):
    def __eq__(self, other):
        if (other.cloud_config != self.cloud_config or
            other.nodepool_id != self.nodepool_id or
            other.pools != self.pools or
            other.image_type != self.image_type or
            other.rate != self.rate or
            other.boot_timeout != self.boot_timeout or
            other.launch_timeout != self.launch_timeout or
            other.clean_floating_ips != self.clean_floating_ips or
            other.max_concurrency != self.max_concurrency or
            other.diskimages != self.diskimages):
            return False

        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "<Provider %s>" % self.name


class ProviderPool(ConfigValue):
    def __eq__(self, other):
        if (other.labels != self.labels or
            other.max_servers != self.max_servers or
            other.azs != self.azs or
            other.networks != self.networks):
            return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "<ProviderPool %s>" % self.name


class ProviderDiskImage(ConfigValue):
    def __repr__(self):
        return "<ProviderDiskImage %s>" % self.name


class Label(ConfigValue):
    def __repr__(self):
        return "<Label %s>" % self.name


class ProviderLabel(ConfigValue):
    def __eq__(self, other):
        if (other.diskimage != self.diskimage or
            other.min_ram != self.min_ram or
            other.name_filter != self.name_filter):
            return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "<ProviderLabel %s>" % self.name


class DiskImage(ConfigValue):
    def __eq__(self, other):
        if (other.name != self.name or
            other.elements != self.elements or
            other.release != self.release or
            other.rebuild_age != self.rebuild_age or
            other.env_vars != self.env_vars or
            other.image_types != self.image_types or
            other.pause != self.pause):
            return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "<DiskImage %s>" % self.name


def loadConfig(config_path):
    retry = 3

    # Since some nodepool code attempts to dynamically re-read its config
    # file, we need to handle the race that happens if an outside entity
    # edits it (causing it to temporarily not exist) at the same time we
    # attempt to reload it.
    while True:
        try:
            config = yaml.load(open(config_path))
            break
        except IOError as e:
            if e.errno == 2:
                retry = retry - 1
                time.sleep(.5)
            else:
                raise e
            if retry == 0:
                raise e

    cloud_config = os_client_config.OpenStackConfig()

    newconfig = Config()
    newconfig.db = None
    newconfig.providers = {}
    newconfig.labels = {}
    newconfig.elementsdir = config.get('elements-dir')
    newconfig.imagesdir = config.get('images-dir')
    newconfig.provider_managers = {}
    newconfig.zookeeper_servers = {}
    newconfig.diskimages = {}

    for server in config.get('zookeeper-servers', []):
        z = zk.ZooKeeperConnectionConfig(server['host'],
                                         server.get('port', 2181),
                                         server.get('chroot', None))
        name = z.host + '_' + str(z.port)
        newconfig.zookeeper_servers[name] = z

    for diskimage in config.get('diskimages', []):
        d = DiskImage()
        d.name = diskimage['name']
        newconfig.diskimages[d.name] = d
        if 'elements' in diskimage:
            d.elements = u' '.join(diskimage['elements'])
        else:
            d.elements = ''
        # must be a string, as it's passed as env-var to
        # d-i-b, but might be untyped in the yaml and
        # interpreted as a number (e.g. "21" for fedora)
        d.release = str(diskimage.get('release', ''))
        d.rebuild_age = int(diskimage.get('rebuild-age', 86400))
        d.env_vars = diskimage.get('env-vars', {})
        if not isinstance(d.env_vars, dict):
            #self.log.error("%s: ignoring env-vars; "
            #               "should be a dict" % d.name)
            d.env_vars = {}
        d.image_types = set(diskimage.get('formats', []))
        d.pause = bool(diskimage.get('pause', False))

    for label in config.get('labels', []):
        l = Label()
        l.name = label['name']
        newconfig.labels[l.name] = l
        l.min_ready = label.get('min-ready', 2)
        l.pools = []

    for provider in config.get('providers', []):
        p = Provider()
        p.name = provider['name']
        newconfig.providers[p.name] = p

        cloud_kwargs = _cloudKwargsFromProvider(provider)
        p.cloud_config = _get_one_cloud(cloud_config, cloud_kwargs)
        p.nodepool_id = provider.get('nodepool-id', None)
        p.region_name = provider.get('region-name')
        p.max_concurrency = provider.get('max-concurrency', -1)
        p.rate = provider.get('rate', 1.0)
        p.boot_timeout = provider.get('boot-timeout', 60)
        p.launch_timeout = provider.get('launch-timeout', 3600)
        p.launch_retries = provider.get('launch-retries', 3)
        p.clean_floating_ips = provider.get('clean-floating-ips')
        p.hostname_format = provider.get(
            'hostname-format',
            '{label.name}-{provider.name}-{node.id}'
        )
        p.image_name_format = provider.get(
            'image-name-format',
            '{image_name}-{timestamp}'
        )
        p.image_type = p.cloud_config.config['image_format']
        p.diskimages = {}
        for image in provider.get('diskimages', []):
            i = ProviderDiskImage()
            i.name = image['name']
            p.diskimages[i.name] = i
            diskimage = newconfig.diskimages[i.name]
            diskimage.image_types.add(p.image_type)
            #i.min_ram = image['min-ram']
            #i.name_filter = image.get('name-filter', None)
            i.pause = bool(image.get('pause', False))
            i.config_drive = image.get('config-drive', None)

            # This dict is expanded and used as custom properties when
            # the image is uploaded.
            i.meta = image.get('meta', {})
            # 5 elements, and no key or value can be > 255 chars
            # per Nova API rules
            if i.meta:
                if len(i.meta) > 5 or \
                   any([len(k) > 255 or len(v) > 255
                        for k, v in i.meta.iteritems()]):
                    # soft-fail
                    #self.log.error("Invalid metadata for %s; ignored"
                    #               % i.name)
                    i.meta = {}
        p.pools = {}
        for pool in provider.get('pools', []):
            pp = ProviderPool()
            pp.name = pool['name']
            pp.provider = p
            p.pools[pp.name] = pp
            pp.max_servers = pool['max-servers']
            pp.azs = pool.get('availability-zones')
            pp.networks = pool.get('networks', [])
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = ProviderLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                pl.diskimage = newconfig.diskimages[label['diskimage']]
                pl.min_ram = label['min-ram']
                pl.name_filter = label.get('name-filter', None)

                top_label = newconfig.labels[pl.name]
                top_label.pools.append(pp)

    return newconfig


def loadSecureConfig(config, secure_config_path):
    secure = ConfigParser.ConfigParser()
    secure.readfp(open(secure_config_path))

    #config.dburi = secure.get('database', 'dburi')


def _cloudKwargsFromProvider(provider):
    cloud_kwargs = {}
    for arg in ['region-name', 'cloud']:
        if arg in provider:
            cloud_kwargs[arg] = provider[arg]

    return cloud_kwargs


def _get_one_cloud(cloud_config, cloud_kwargs):
    '''This is a function to allow for overriding it in tests.'''
    if cloud_kwargs.get('cloud', '').startswith('fake'):
        return fakeprovider.fake_get_one_cloud(cloud_config, cloud_kwargs)
    return cloud_config.get_one_cloud(**cloud_kwargs)
