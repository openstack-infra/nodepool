# Copyright (C) 2018 Red Hat
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
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import os

import yaml

from nodepool import tests


class TestOpenShift(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestOpenShift")

    def setup_config(self, filename):
        adjusted_filename = "functional/openshift/" + filename
        # Openshift context name are not hardcoded,
        # discover the name setup by oc login
        kubecfg = yaml.safe_load(open(os.path.expanduser("~/.kube/config")))
        try:
            ctx_name = kubecfg['contexts'][0]['name']
        except IndexError:
            raise RuntimeError("Run oc login first")
        self.log.debug("Using %s context name", ctx_name)
        return super().setup_config(adjusted_filename, context_name=ctx_name)

    def test_basic(self):
        configfile = self.setup_config('basic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        nodes = self.waitForNodes("openshift-project", 1)
        self.assertEqual(1, len(nodes))
        self.assertEqual(nodes[0].connection_type, "project")

        nodes = self.waitForNodes("openshift-pod", 1)
        self.assertEqual(1, len(nodes))
        self.assertEqual(nodes[0].connection_type, "kubectl")
