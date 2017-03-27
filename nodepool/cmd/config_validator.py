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

        pool_label = {
            v.Required('name'): str,
            v.Required('diskimage'): str,
            'min-ram': int,
            'name-filter': str,
        }

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

        provider = {
            'name': str,
            'region-name': str,
            'cloud': str,
            'max-concurrency': int,
            'ipv6-preferred': bool,
            'boot-timeout': int,
            'launch-timeout': int,
            'launch-retries': int,
            'rate': float,
            'hostname-format': str,
            'image-name-format': str,
            'clean-floating-ips': bool,
            'pools': [pool],
            'diskimages': [provider_diskimage],
        }

        label = {
            'name': str,
            'min-ready': int,
        }

        diskimage = {
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
            'providers': [provider],
            'labels': [label],
            'diskimages': [diskimage],
        }

        log.info("validating %s" % self.config_file)
        config = yaml.load(open(self.config_file))

        # validate the overall schema
        schema = v.Schema(top_level)
        schema(config)
