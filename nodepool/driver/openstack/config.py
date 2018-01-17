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
import voluptuous as v

from nodepool.driver import ProviderConfig
from nodepool.driver import ConfigValue


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


class ProviderLabel(ConfigValue):
    def __eq__(self, other):
        if (other.diskimage != self.diskimage or
            other.cloud_image != self.cloud_image or
            other.min_ram != self.min_ram or
            other.flavor_name != self.flavor_name or
            other.key_name != self.key_name):
            return False
        return True

    def __repr__(self):
        return "<ProviderLabel %s>" % self.name


class ProviderPool(ConfigValue):
    def __eq__(self, other):
        if (other.labels != self.labels or
            other.max_cores != self.max_cores or
            other.max_servers != self.max_servers or
            other.max_ram != self.max_ram or
            other.azs != self.azs or
            other.networks != self.networks):
            return False
        return True

    def __repr__(self):
        return "<ProviderPool %s>" % self.name


class OpenStackProviderConfig(ProviderConfig):
    os_client_config = None

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

    def _cloudKwargs(self):
        cloud_kwargs = {}
        for arg in ['region-name', 'cloud']:
            if arg in self.provider:
                cloud_kwargs[arg] = self.provider[arg]
        return cloud_kwargs

    def load(self, config):
        if OpenStackProviderConfig.os_client_config is None:
            OpenStackProviderConfig.os_client_config = \
                os_client_config.OpenStackConfig()
        cloud_kwargs = self._cloudKwargs()
        self.cloud_config = self.os_client_config.get_one_cloud(**cloud_kwargs)

        self.image_type = self.cloud_config.config['image_format']
        self.driver.manage_images = True
        self.region_name = self.provider.get('region-name')
        self.rate = self.provider.get('rate', 1.0)
        self.boot_timeout = self.provider.get('boot-timeout', 60)
        self.launch_timeout = self.provider.get('launch-timeout', 3600)
        self.launch_retries = self.provider.get('launch-retries', 3)
        self.clean_floating_ips = self.provider.get('clean-floating-ips')
        self.hostname_format = self.provider.get(
            'hostname-format',
            '{label.name}-{provider.name}-{node.id}'
        )
        self.image_name_format = self.provider.get(
            'image-name-format',
            '{image_name}-{timestamp}'
        )
        self.diskimages = {}
        for image in self.provider.get('diskimages', []):
            i = ProviderDiskImage()
            i.name = image['name']
            self.diskimages[i.name] = i
            diskimage = config.diskimages[i.name]
            diskimage.image_types.add(self.image_type)
            i.pause = bool(image.get('pause', False))
            i.config_drive = image.get('config-drive', None)
            i.connection_type = image.get('connection-type', 'ssh')

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
                    # self.log.error("Invalid metadata for %s; ignored"
                    #               % i.name)
                    i.meta = {}

        self.cloud_images = {}
        for image in self.provider.get('cloud-images', []):
            i = ProviderCloudImage()
            i.name = image['name']
            i.config_drive = image.get('config-drive', None)
            i.image_id = image.get('image-id', None)
            i.image_name = image.get('image-name', None)
            i.username = image.get('username', None)
            i.connection_type = image.get('connection-type', 'ssh')
            self.cloud_images[i.name] = i

        self.pools = {}
        for pool in self.provider.get('pools', []):
            pp = ProviderPool()
            pp.name = pool['name']
            pp.provider = self
            self.pools[pp.name] = pp
            pp.max_cores = pool.get('max-cores', None)
            pp.max_servers = pool.get('max-servers', None)
            pp.max_ram = pool.get('max-ram', None)
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
                    pl.diskimage = config.diskimages[diskimage]
                else:
                    pl.diskimage = None
                cloud_image_name = label.get('cloud-image', None)
                if cloud_image_name:
                    cloud_image = self.cloud_images.get(cloud_image_name, None)
                    if not cloud_image:
                        raise ValueError(
                            "cloud-image %s does not exist in provider %s"
                            " but is referenced in label %s" %
                            (cloud_image_name, self.name, pl.name))
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

                top_label = config.labels[pl.name]
                top_label.pools.append(pp)

    def get_schema(self):
        provider_diskimage = {
            'name': str,
            'pause': bool,
            'meta': dict,
            'config-drive': bool,
            'connection-type': str,
        }

        provider_cloud_images = {
            'name': str,
            'config-drive': bool,
            'connection-type': str,
            v.Exclusive('image-id', 'cloud-image-name-or-id'): str,
            v.Exclusive('image-name', 'cloud-image-name-or-id'): str,
            'username': str,
        }

        pool_label_main = {
            v.Required('name'): str,
            v.Exclusive('diskimage', 'label-image'): str,
            v.Exclusive('cloud-image', 'label-image'): str,
            'min-ram': int,
            'flavor-name': str,
            'key-name': str,
            'console-log': bool,
            'boot-from-volume': bool,
            'volume-size': int,
        }

        label_min_ram = v.Schema({v.Required('min-ram'): int}, extra=True)

        label_flavor_name = v.Schema({v.Required('flavor-name'): str},
                                     extra=True)

        label_diskimage = v.Schema({v.Required('diskimage'): str}, extra=True)

        label_cloud_image = v.Schema({v.Required('cloud-image'): str},
                                     extra=True)

        pool_label = v.All(pool_label_main,
                           v.Any(label_min_ram, label_flavor_name),
                           v.Any(label_diskimage, label_cloud_image))

        pool = {
            'name': str,
            'networks': [str],
            'auto-floating-ip': bool,
            'max-cores': int,
            'max-servers': int,
            'max-ram': int,
            'labels': [pool_label],
            'availability-zones': [str],
        }

        return v.Schema({
            'region-name': str,
            v.Required('cloud'): str,
            'boot-timeout': int,
            'launch-timeout': int,
            'launch-retries': int,
            'nodepool-id': str,
            'rate': float,
            'hostname-format': str,
            'image-name-format': str,
            'clean-floating-ips': bool,
            'pools': [pool],
            'diskimages': [provider_diskimage],
            'cloud-images': [provider_cloud_images],
        })
