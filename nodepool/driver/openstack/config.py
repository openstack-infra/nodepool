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

import math
import voluptuous as v

from nodepool.driver import ProviderConfig
from nodepool.driver import ConfigValue
from nodepool.driver import ConfigPool


class ProviderDiskImage(ConfigValue):
    def __init__(self):
        self.name = None
        self.pause = False
        self.config_drive = None
        self.connection_type = None
        self.connection_port = None
        self.meta = None

    def __eq__(self, other):
        if isinstance(other, ProviderDiskImage):
            return (self.name == other.name and
                    self.pause == other.pause and
                    self.config_drive == other.config_drive and
                    self.connection_type == other.connection_type and
                    self.connection_port == other.connection_port and
                    self.meta == other.meta)
        return False

    def __repr__(self):
        return "<ProviderDiskImage %s>" % self.name


class ProviderCloudImage(ConfigValue):
    def __init__(self):
        self.name = None
        self.config_drive = None
        self.image_id = None
        self.image_name = None
        self.username = None
        self.connection_type = None
        self.connection_port = None

    def __eq__(self, other):
        if isinstance(other, ProviderCloudImage):
            return (self.name == other.name and
                    self.config_drive == other.config_drive and
                    self.image_id == other.image_id and
                    self.image_name == other.image_name and
                    self.username == other.username and
                    self.connection_type == other.connection_type and
                    self.connection_port == other.connection_port)
        return False

    def __repr__(self):
        return "<ProviderCloudImage %s>" % self.name

    @property
    def external_name(self):
        '''Human readable version of external.'''
        return self.image_id or self.image_name or self.name


class ProviderLabel(ConfigValue):
    def __init__(self):
        self.name = None
        self.diskimage = None
        self.cloud_image = None
        self.min_ram = None
        self.flavor_name = None
        self.key_name = None
        self.console_log = False
        self.boot_from_volume = False
        self.volume_size = None
        # The ProviderPool object that owns this label.
        self.pool = None

    def __eq__(self, other):
        if isinstance(other, ProviderLabel):
            # NOTE(Shrews): We intentionally do not compare 'pool' here
            # since this causes recursive checks with ProviderPool.
            return (other.diskimage == self.diskimage and
                    other.cloud_image == self.cloud_image and
                    other.min_ram == self.min_ram and
                    other.flavor_name == self.flavor_name and
                    other.key_name == self.key_name and
                    other.name == self.name and
                    other.console_log == self.console_log and
                    other.boot_from_volume == self.boot_from_volume and
                    other.volume_size == self.volume_size)
        return False

    def __repr__(self):
        return "<ProviderLabel %s>" % self.name


class ProviderPool(ConfigPool):
    def __init__(self):
        self.name = None
        self.max_cores = None
        self.max_ram = None
        self.ignore_provider_quota = False
        self.azs = None
        self.networks = None
        self.security_groups = None
        self.auto_floating_ip = True
        self.host_key_checking = True
        self.labels = None
        # The OpenStackProviderConfig object that owns this pool.
        self.provider = None

        # Initialize base class attributes
        super().__init__()

    def __eq__(self, other):
        if isinstance(other, ProviderPool):
            # NOTE(Shrews): We intentionally do not compare 'provider' here
            # since this causes recursive checks with OpenStackProviderConfig.
            return (super().__eq__(other) and
                    other.name == self.name and
                    other.max_cores == self.max_cores and
                    other.max_ram == self.max_ram and
                    other.ignore_provider_quota == (
                        self.ignore_provider_quota) and
                    other.azs == self.azs and
                    other.networks == self.networks and
                    other.security_groups == self.security_groups and
                    other.auto_floating_ip == self.auto_floating_ip and
                    other.host_key_checking == self.host_key_checking and
                    other.labels == self.labels)
        return False

    def __repr__(self):
        return "<ProviderPool %s>" % self.name


class OpenStackProviderConfig(ProviderConfig):
    def __init__(self, driver, provider):
        self.driver_object = driver
        self.__pools = {}
        self.cloud_config = None
        self.image_type = None
        self.rate = None
        self.boot_timeout = None
        self.launch_timeout = None
        self.clean_floating_ips = None
        self.diskimages = {}
        self.cloud_images = {}
        self.hostname_format = None
        self.image_name_format = None
        super().__init__(provider)

    def __eq__(self, other):
        if isinstance(other, OpenStackProviderConfig):
            return (super().__eq__(other) and
                    other.cloud_config == self.cloud_config and
                    other.pools == self.pools and
                    other.image_type == self.image_type and
                    other.rate == self.rate and
                    other.boot_timeout == self.boot_timeout and
                    other.launch_timeout == self.launch_timeout and
                    other.clean_floating_ips == self.clean_floating_ips and
                    other.diskimages == self.diskimages and
                    other.cloud_images == self.cloud_images)
        return False

    def _cloudKwargs(self):
        cloud_kwargs = {}
        for arg in ['region-name', 'cloud']:
            if arg in self.provider:
                cloud_kwargs[arg] = self.provider[arg]
        return cloud_kwargs

    @property
    def pools(self):
        return self.__pools

    @property
    def manage_images(self):
        return True

    def load(self, config):
        cloud_kwargs = self._cloudKwargs()
        occ = self.driver_object.os_client_config
        self.cloud_config = occ.get_one_cloud(**cloud_kwargs)

        self.image_type = self.cloud_config.config['image_format']
        self.region_name = self.provider.get('region-name')
        self.rate = float(self.provider.get('rate', 1.0))
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

        default_port_mapping = {
            'ssh': 22,
            'winrm': 5986,
        }

        for image in self.provider.get('diskimages', []):
            i = ProviderDiskImage()
            i.name = image['name']
            self.diskimages[i.name] = i
            diskimage = config.diskimages[i.name]
            diskimage.image_types.add(self.image_type)
            i.pause = bool(image.get('pause', False))
            i.config_drive = image.get('config-drive', None)
            i.connection_type = image.get('connection-type', 'ssh')
            i.connection_port = image.get(
                'connection-port',
                default_port_mapping.get(i.connection_type, 22))

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

        for image in self.provider.get('cloud-images', []):
            i = ProviderCloudImage()
            i.name = image['name']
            i.config_drive = image.get('config-drive', None)
            i.image_id = image.get('image-id', None)
            i.image_name = image.get('image-name', None)
            i.username = image.get('username', None)
            i.connection_type = image.get('connection-type', 'ssh')
            i.connection_port = image.get(
                'connection-port',
                default_port_mapping.get(i.connection_type, 22))
            self.cloud_images[i.name] = i

        for pool in self.provider.get('pools', []):
            pp = ProviderPool()
            pp.name = pool['name']
            pp.provider = self
            self.pools[pp.name] = pp
            pp.max_cores = pool.get('max-cores', math.inf)
            pp.max_servers = pool.get('max-servers', math.inf)
            pp.max_ram = pool.get('max-ram', math.inf)
            pp.ignore_provider_quota = pool.get('ignore-provider-quota', False)
            pp.azs = pool.get('availability-zones')
            pp.networks = pool.get('networks', [])
            pp.security_groups = pool.get('security-groups', [])
            pp.auto_floating_ip = bool(pool.get('auto-floating-ip', True))
            pp.host_key_checking = bool(pool.get('host-key-checking', True))

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

    def getSchema(self):
        provider_diskimage = {
            'name': str,
            'pause': bool,
            'meta': dict,
            'config-drive': bool,
            'connection-type': str,
            'connection-port': int,
        }

        provider_cloud_images = {
            'name': str,
            'config-drive': bool,
            'connection-type': str,
            'connection-port': int,
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
            'host-key-checking': bool,
            'ignore-provider-quota': bool,
            'max-cores': int,
            'max-servers': int,
            'max-ram': int,
            'labels': [pool_label],
            'availability-zones': [str],
            'security-groups': [str]
        }

        return v.Schema({
            'region-name': str,
            v.Required('cloud'): str,
            'boot-timeout': int,
            'launch-timeout': int,
            'launch-retries': int,
            'nodepool-id': str,
            'rate': v.Coerce(float),
            'hostname-format': str,
            'image-name-format': str,
            'clean-floating-ips': bool,
            'pools': [pool],
            'diskimages': [provider_diskimage],
            'cloud-images': [provider_cloud_images],
        })

    def getSupportedLabels(self, pool_name=None):
        labels = set()
        for pool in self.pools.values():
            if not pool_name or (pool.name == pool_name):
                labels.update(pool.labels.keys())
        return labels
