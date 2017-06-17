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

from nodepool import fakeprovider
from nodepool.driver.openstack.provider import OpenStackProvider


class FakeProvider(OpenStackProvider):
    def __init__(self, provider, use_taskmanager):
        self.createServer_fails = 0
        self.__client = fakeprovider.FakeOpenStackCloud()
        super(FakeProvider, self).__init__(provider, use_taskmanager)

    def _getClient(self):
        return self.__client

    def createServer(self, *args, **kwargs):
        while self.createServer_fails:
            self.createServer_fails -= 1
            raise Exception("Expected createServer exception")
        return super(FakeProvider, self).createServer(*args, **kwargs)
