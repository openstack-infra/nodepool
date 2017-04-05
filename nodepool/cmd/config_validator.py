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
import voluptuous as v
import yaml

log = logging.getLogger(__name__)


class ConfigValidator:
    """Check the layout and certain configuration options"""

    def __init__(self, config_file):
        self.config_file = config_file

    def validate(self):
        label_min_ram = v.Schema({v.Required('min-ram'): int}, extra=True)

        label_flavor_name = v.Schema({v.Required('flavor-name'): str},
                                     extra=True)

        label_diskimage = v.Schema({v.Required('diskimage'): str}, extra=True)

        label_cloud_image = v.Schema({v.Required('cloud-image'): str}, extra=True)

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

        pool_label = v.All(pool_label_main,
                           v.Any(label_min_ram, label_flavor_name),
                           v.Any(label_diskimage, label_cloud_image))

        pool = {
            'name': str,
            'networks': [str],
            'max-servers': int,
            'labels': [pool_label],
            'availability-zones': [str],
            }

        provider_diskimage = {
            'name': str,
            'pause': bool,
            'meta': dict,
            'config-drive': bool,
        }

        provider_cloud_images = {
            'name': str,
            'config-drive': bool,
            v.Exclusive('image-id', 'cloud-image-name-or-id'): str,
            v.Exclusive('image-name', 'cloud-image-name-or-id'): str,
        }

        provider = {
            'name': str,
            'driver': str,
            'region-name': str,
            v.Required('cloud'): str,
            'max-concurrency': int,
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
        }

        label = {
            'name': str,
            'min-ready': int,
            'max-ready-age': int,
        }

        diskimage = {
            'name': str,
            'pause': bool,
            'elements': [str],
            'formats': [str],
            'release': v.Any(str, int),
            'rebuild-age': int,
            'env-vars': {str: str},
            'username': str,
        }

        webapp = {
            'port': int,
            'listen_address': str,
        }

        top_level = {
            'webapp': webapp,
            'elements-dir': str,
            'images-dir': str,
            'zookeeper-servers': [{
                'host': str,
                'port': int,
                'chroot': str,
            }],
            'providers': [provider],
            'labels': [label],
            'diskimages': [diskimage],
        }

        log.info("validating %s" % self.config_file)
        config = yaml.load(open(self.config_file))

        # validate the overall schema
        schema = v.Schema(top_level)
        schema(config)
