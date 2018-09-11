# Copyright 2017 Red Hat
#
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

from nodepool.driver import NodeRequestHandler


class StaticNodeRequestHandler(NodeRequestHandler):
    log = logging.getLogger("nodepool.driver.static."
                            "StaticNodeRequestHandler")

    @property
    def alive_thread_count(self):
        # We don't spawn threads to launch nodes, so always return 1.
        return 1

    def imagesAvailable(self):
        '''
        This driver doesn't manage images, so always return True.
        '''
        return True

    def hasRemainingQuota(self, ntype):
        # We are always at quota since we cannot launch new nodes.
        return False

    def launch(self, node):
        # NOTE: We do not expect this to be called since hasRemainingQuota()
        # returning False should prevent the call.
        raise Exception("Node launching not supported by static driver")

    def launchesComplete(self):
        # We don't wait on a launch since we never actually launch.
        return True

    def checkReusableNode(self, node):
        return self.manager.checkNodeLiveness(node)
