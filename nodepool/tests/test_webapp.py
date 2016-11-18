# Copyright (C) 2014 OpenStack Foundation
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
import urllib2

from nodepool import tests


class TestWebApp(tests.DBTestCase):
    log = logging.getLogger("nodepool.TestWebApp")

    def test_image_list(self):
        configfile = self.setup_config('node.yaml')
        pool = self.useNodepool(configfile, watermark_sleep=1)
        self._useBuilder(configfile)
        pool.start()
        webapp = self.useWebApp(pool, port=0)
        webapp.start()
        port = webapp.server.socket.getsockname()[1]

        self.waitForImage('fake-provider', 'fake-image')
        self.waitForNodes(pool)

        req = urllib2.Request(
            "http://localhost:%s/image-list" % port)
        f = urllib2.urlopen(req)
        data = f.read()
        self.assertTrue('fake-image' in data)
