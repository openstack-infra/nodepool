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

from nodepool import zk


class ConfigValue(object):
    def __eq__(self, other):
        if isinstance(other, ConfigValue):
            if other.__dict__ == self.__dict__:
                return True
        return False


class Config(ConfigValue):
    pass


class Driver(ConfigValue):
    pass


class Provider(ConfigValue):
    def __eq__(self, other):
        if (other.cloud_config != self.cloud_config or
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


class ProviderCloudImage(ConfigValue):
    def __repr__(self):
        return "<ProviderCloudImage %s>" % self.name

    @property
    def external(self):
        '''External identifier to pass to the cloud.'''
        if self.image_id:
            return dict(id=self.image_id)
        else:
            return self.image_name or self.name

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.image_id or self.image_name or self.name


class Label(ConfigValue):
    def __repr__(self):
        return "<Label %s>" % self.name


class ProviderLabel(ConfigValue):
    def __eq__(self, other):
        if (other.diskimage != self.diskimage or
            other.cloud_image != self.cloud_image or
            other.min_ram != self.min_ram or
            other.flavor_name != self.flavor_name or
            other.key_name != self.key_name):
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
            other.pause != self.pause or
            other.username != self.username):
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
    newconfig.webapp = {
        'port': config.get('webapp', {}).get('port', 8005),
        'listen_address': config.get('webapp', {}).get('listen_address',
                                                       '0.0.0.0')
    }
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
        d.username = diskimage.get('username', 'zuul')

    for label in config.get('labels', []):
        l = Label()
        l.name = label['name']
        newconfig.labels[l.name] = l
        l.max_ready_age = label.get('max-ready-age', 0)
        l.min_ready = label.get('min-ready', 2)
        l.pools = []

    for provider in config.get('providers', []):
        provider.setdefault('driver', 'openstack')
        # Ensure legacy configuration still works when using fake name
        if provider.get('name', '').startswith('fake'):
            provider['driver'] = 'fake'
        p = Provider()
        p.name = provider['name']
        p.driver = Driver()
        p.driver.name = provider['driver']
        p.driver.manage_images = False
        newconfig.providers[p.name] = p

        cloud_kwargs = _cloudKwargsFromProvider(provider)
        p.cloud_config = None
        p.image_type = None
        if p.driver.name in ('openstack', 'fake'):
            p.driver.manage_images = True
            p.cloud_config = cloud_config.get_one_cloud(**cloud_kwargs)
            p.image_type = p.cloud_config.config['image_format']
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
        p.diskimages = {}
        for image in provider.get('diskimages', []):
            i = ProviderDiskImage()
            i.name = image['name']
            p.diskimages[i.name] = i
            diskimage = newconfig.diskimages[i.name]
            diskimage.image_types.add(p.image_type)
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
                        for k, v in i.meta.items()]):
                    # soft-fail
                    #self.log.error("Invalid metadata for %s; ignored"
                    #               % i.name)
                    i.meta = {}
        p.cloud_images = {}
        for image in provider.get('cloud-images', []):
            i = ProviderCloudImage()
            i.name = image['name']
            i.config_drive = image.get('config-drive', None)
            i.image_id = image.get('image-id', None)
            i.image_name = image.get('image-name', None)
            p.cloud_images[i.name] = i
        p.pools = {}
        for pool in provider.get('pools', []):
            pp = ProviderPool()
            pp.name = pool['name']
            pp.provider = p
            p.pools[pp.name] = pp
            pp.max_servers = pool['max-servers']
            pp.azs = pool.get('availability-zones')
            pp.networks = pool.get('networks', [])
            pp.auto_floating_ip = bool(pool.get('auto-floating-ip', True))
            pp.labels = {}
            for label in pool.get('labels', []):
                pl = ProviderLabel()
                pl.name = label['name']
                pl.pool = pp
                pp.labels[pl.name] = pl
                diskimage = label.get('diskimage', None)
                if diskimage:
                    pl.diskimage = newconfig.diskimages[diskimage]
                else:
                    pl.diskimage = None
                cloud_image_name = label.get('cloud-image', None)
                if cloud_image_name:
                    cloud_image = p.cloud_images.get(cloud_image_name, None)
                    if not cloud_image:
                        raise ValueError(
                            "cloud-image %s does not exist in provider %s"
                            " but is referenced in label %s" %
                            (cloud_image_name, p.name, pl.name))
                else:
                    cloud_image = None
                pl.cloud_image = cloud_image
                pl.min_ram = label.get('min-ram', 0)
                pl.flavor_name = label.get('flavor-name', None)
                pl.key_name = label.get('key-name')
                pl.console_log = label.get('console-log', False)
                pl.boot_from_volume = bool(label.get('boot-from-volume',
                                                     False))
                pl.volume_size = label.get('volume-size', 50)

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
    if provider['driver'] == 'fake':
        cloud_kwargs['validate'] = False
    return cloud_kwargs
