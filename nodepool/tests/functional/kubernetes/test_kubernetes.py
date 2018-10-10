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

from nodepool import tests


class TestKubernetes(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestKubernetes")

    def setup_config(self, filename):
        adjusted_filename = "functional/kubernetes/" + filename
        return super().setup_config(adjusted_filename)

    def test_basic(self):
        configfile = self.setup_config('basic.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        pool.start()

        # Add something here to test for namespace/pods/whatever
        namespace = self.waitForNodes("kubernetes-namespace", 1)
        self.assertEqual(1, len(namespace))
        self.assertEqual(namespace[0].connection_type, "namespace")

        pod = self.waitForNodes("pod-fedora", 1)
        self.assertEqual(1, len(pod))
        self.assertEqual(pod[0].connection_type, "kubectl")
