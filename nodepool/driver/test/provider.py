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

from nodepool.driver import Provider
from nodepool.driver.test import handler


class TestProvider(Provider):
    def __init__(self, provider):
        self.provider = provider

    def start(self, zk_conn):
        pass

    def stop(self):
        pass

    def join(self):
        pass

    def labelReady(self, name):
        return True

    def cleanupNode(self, node_id):
        pass

    def waitForNodeCleanup(self, node_id):
        pass

    def cleanupLeakedResources(self):
        pass

    def listNodes(self):
        return []

    def getRequestHandler(self, poolworker, request):
        return handler.TestHandler(poolworker, request)
