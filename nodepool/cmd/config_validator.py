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

from nodepool.config import get_provider_config

log = logging.getLogger(__name__)


class ConfigValidator:
    """Check the layout and certain configuration options"""

    def __init__(self, config_file):
        self.config_file = config_file

    def validate(self):
        provider = {
            'name': v.Required(str),
            'driver': str,
            'max-concurrency': int,
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
            'build-log-dir': str,
            'build-log-retention': int,
            'zookeeper-servers': [{
                'host': str,
                'port': int,
                'chroot': str,
            }],
            'providers': list,
            'labels': [label],
            'diskimages': [diskimage],
        }

        log.info("validating %s" % self.config_file)
        config = yaml.load(open(self.config_file))

        # validate the overall schema
        schema = v.Schema(top_level)
        schema(config)
        for provider_dict in config.get('providers', []):
            provider_schema = get_provider_config(provider_dict).get_schema()
            provider_schema.extend(provider)(provider_dict)
