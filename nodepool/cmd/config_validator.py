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
            'image-update': str,
        }

        images = {
            'name': str,
            'base-image': str,
            'min-ram': int,
            'name-filter': str,
            'diskimage': str,
            'meta': dict,
            'setup': str,
            'username': str,
            'private-key': str,
            'config-drive': bool,
        }

        old_network = {
            'net-id': str,
            'net-label': str,
        }

        network = {
            'name': v.Required(str),
            'public': bool,
        }

        providers = {
            'name': str,
            'region-name': str,
            'service-type': str,
            'service-name': str,
            'availability-zones': [str],
            'keypair': str,
            'cloud': str,
            'username': str,
            'password': str,
            'auth-url': str,
            'project-id': str,
            'project-name': str,
            'max-servers': int,
            'pool': str,
            'image-type': str,
            'networks': [v.Any(old_network, network)],
            'ipv6-preferred': bool,
            'boot-timeout': int,
            'api-timeout': int,
            'launch-timeout': int,
            'rate': float,
            'images': [images],
            'template-hostname': str,
            'clean-floating-ips': bool,
        }

        labels = {
            'name': str,
            'image': str,
            'min-ready': int,
            'ready-script': str,
            'subnodes': int,
            'providers': [{
                'name': str,
            }],
        }

        targets = {
            'name': str,
            'hostname': str,
            'subnode-hostname': str,
            'assign-via-gearman': bool,
            'jenkins': {
                'url': str,
                'user': str,
                'apikey': str,
                'credentials-id': str,
                'test-job': str
            }
        }

        diskimages = {
            'name': str,
            'elements': [str],
            'release': v.Any(str, int),
            'env-vars': dict,
        }

        top_level = {
            'script-dir': str,
            'elements-dir': str,
            'images-dir': str,
            'dburi': str,
            'zmq-publishers': [str],
            'gearman-servers': [{
                'host': str,
                'port': int,
            }],
            'cron': cron,
            'providers': [providers],
            'labels': [labels],
            'targets': [targets],
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
