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
        cron = {
            'check': str,
            'cleanup': str,
        }

        images = {
            'name': str,
            'pause': bool,
            'min-ram': int,
            'name-filter': str,
            'diskimage': str,
            'meta': dict,
            'config-drive': bool,
        }

        old_network = {
            'net-id': str,
            'net-label': str,
        }

        network = {
            'name': v.Required(str),
            'public': bool,  # Ignored, but kept for backwards compat
        }

        providers = {
            'name': str,
            'region-name': str,
            'availability-zones': [str],
            'keypair': str,
            'cloud': str,
            'max-servers': int,
            'max-concurrency': int,
            'pool': str,  # Ignored, but kept for backwards compat
            'image-type': str,
            'networks': [v.Any(old_network, network)],
            'ipv6-preferred': bool,
            'boot-timeout': int,
            'api-timeout': int,
            'launch-timeout': int,
            'launch-retries': int,
            'rate': float,
            'images': [images],
            'hostname-format': str,
            'image-name-format': str,
            'clean-floating-ips': bool,
        }

        labels = {
            'name': str,
            'image': str,
            'min-ready': int,
            'providers': [{
                'name': str,
            }],
        }

        diskimages = {
            'name': str,
            'pause': bool,
            'elements': [str],
            'formats': [str],
            'release': v.Any(str, int),
            'rebuild-age': int,
            'env-vars': dict,
        }

        top_level = {
            'elements-dir': str,
            'images-dir': str,
            'zookeeper-servers': [{
                'host': str,
                'port': int,
                'chroot': str,
            }],
            'cron': cron,
            'providers': [providers],
            'labels': [labels],
            'diskimages': [diskimages],
        }

        log.info("validating %s" % self.config_file)
        config = yaml.load(open(self.config_file))

        # validate the overall schema
        schema = v.Schema(top_level)
        schema(config)

        # labels must list valid providers
        all_providers = [p['name'] for p in config['providers']]
        for label in config['labels']:
            for provider in label['providers']:
                if not provider['name'] in all_providers:
                    raise AssertionError('label %s requests '
                                         'non-existent provider %s'
                                         % (label['name'], provider['name']))
